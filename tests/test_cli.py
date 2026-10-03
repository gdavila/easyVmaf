"""Layer 3: argument validation, batch processing and stdout/stderr contracts."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import CAPABILITIES, SCORES, STREAM, strict_loads, write_scores
from easyvmaf import cli, ffmpeg, vmaf
from easyvmaf.models import model_names, select_models
from easyvmaf.results import VmafResult, read_scores


def run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["easyvmaf", *arguments])
    cli.main()


def exit_code(monkeypatch, *arguments):
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, *arguments)
    return exc.value.code


def result_for(models, scores, log_path):
    return VmafResult(scores=scores, models=models, display="hd", pix_fmt="yuv420p",
                      hfr=False, log_path=log_path)


@pytest.fixture
def scored(monkeypatch):
    """Real vmaf preprocessing; FFprobe and libvmaf are stubbed, every requested model scores 90."""
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(STREAM))

    def score(qos, models, **kwargs):
        qos.vmafpath = str(Path(qos.main.videoSrc).with_name(
            Path(qos.main.videoSrc).stem + "_vmaf.json"))
        write_scores(qos.vmafpath, dict.fromkeys(model_names(models), 90.0))

    monkeypatch.setattr(ffmpeg.FFmpegQos, "getVmaf", score)


@pytest.fixture
def batch(tmp_path, monkeypatch, ffmpeg_ok):
    """Three distorted inputs with stubbed calculations; `*.mkv` keeps a nonalphabetical order."""
    reference = tmp_path / "reference.mkv"
    reference.touch()
    files, calculations = [], {}
    for index, name in enumerate(("z", "a", "m")):
        distorted = tmp_path / (name + ".mkv")
        distorted.touch()
        output = tmp_path / (name + "_vmaf.json")
        files.append(str(distorted))
        calculations[str(distorted)] = SimpleNamespace(
            syncOffset=Mock(return_value=(index * 0.1, 30.1234567 + index)),
            getVmaf=Mock(return_value=result_for(
                select_models("hd", ("0.6",)), dict(SCORES), str(output))))
    constructor = Mock(side_effect=lambda main, ref, **kwargs: calculations[main])
    real_glob = cli.glob.glob
    monkeypatch.setattr(cli.glob, "glob", lambda p: files if p == "*.mkv" else real_glob(p))
    monkeypatch.setattr(cli, "vmaf", constructor)
    return SimpleNamespace(files=files, reference=str(reference), calc=calculations,
                           constructor=constructor)


@pytest.mark.parametrize("arguments, code, message", [
    ([], 1, "usage:"),
    (["-d", "dist.mkv", "--json"], 2, "-r"),
    (["-unknown"], 2, "-unknown"),
    (["--sync-window=-1"], 2, "--sync-window"),
    (["--sync-start=nan"], 2, "--sync-start"),
    (["--fps=inf"], 2, "--fps"),
    (["--subsample=0"], 2, "--subsample"),
    (["--subsample=1.5"], 2, "--subsample"),
    (["--threads=-1"], 2, "--threads"),
    (["--output-format=JSON"], 2, "--output-format"),
    (["--sync-only"], 2, "--sync-only"),
    (["--sync-only", "--sync-window=0"], 2, "--sync-only"),
    # GPU scripts of 3.x now get VMAF v1 by default, which libvmaf_cuda cannot compute.
    (["--gpu"], 2, "--vmaf-version 0.6"),
    # ':' or '|' would inject options or models into the libvmaf filtergraph.
    (["--model-option", "cambi.topk=0.5:eotf=pq"], 2, "--model-option"),
    # 3.x scripts fail with the 4.0 name of the removed flag, not a bare unknown-flag error.
    (["-sw", "2"], 2, "--sync-window"),
    (["-model", "4k"], 2, "--display"),  # 4.0 betas accepted it as an alias
    (["-reverse"], 2, "--reverse"),  # argparse alone would read it as '-r everse'
], ids=lambda value: " ".join(value) if isinstance(value, list) else None)
def test_invalid_arguments_fail_on_stderr_before_ffmpeg_check(monkeypatch, capsys,
                                                              arguments, code, message):
    if arguments and arguments[0] != "-d":
        arguments = ["-d", "dist.mkv", "-r", "ref.mkv", *arguments, "--json"]
    check = Mock()
    monkeypatch.setattr(cli, "check_ffmpeg", check)

    assert exit_code(monkeypatch, *arguments) == code

    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err and message in err.splitlines()[0]
    check.assert_not_called()


@pytest.mark.parametrize("capability, message", [
    ("meets_minimum", "requires FFmpeg >= 8.1"),
    ("libvmaf_v1", "requires libvmaf >= 3.2.1"),
    ("cuda_vmaf", "libvmaf_cuda filter"),
])
def test_unusable_ffmpeg_fails_on_stderr(monkeypatch, capsys, capability, message):
    capabilities = dict(CAPABILITIES, cuda_vmaf=True)
    capabilities[capability] = False
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: capabilities)
    constructor = Mock()
    monkeypatch.setattr(cli, "vmaf", constructor)

    assert exit_code(monkeypatch, "-d", "dist.mkv", "-r", "ref.mkv", "--gpu",
                     "--vmaf-version", "0.6", "--json") == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert message in err
    constructor.assert_not_called()


@pytest.mark.parametrize("missing, message", [
    ("distorted", "Distorted Video files not found"),
    ("reference", "Reference Video file not found"),
])
def test_missing_input_fails_on_stderr(batch, monkeypatch, capsys, missing, message):
    distorted = batch.files[0]
    Path(distorted if missing == "distorted" else batch.reference).unlink()

    assert exit_code(monkeypatch, "-d", distorted, "-r", batch.reference, "--json") == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert message in err
    batch.constructor.assert_not_called()


DEFAULTS = dict(subsample=1, threads=0, manual_fps=0, display="hd", vmaf_versions=("1",),
                views=None, hfr="auto", bitdepth="auto", enc_size=None, enc_bitdepth=None,
                model_options=(), output_fmt="json")


@pytest.mark.parametrize("options, forwarded", [
    ([], DEFAULTS),
    (["--sync-window", "0.4", "--sync-start", "1.25", "--fps", "23.976", "--subsample", "3",
      "--threads", "2",
      "--display", "4K", "--view", "1.5h", "3H", "--hfr", "on", "--bitdepth", "10",
      "--enc-size", "1280x720", "--enc-bitdepth", "8", "--model-option", "cambi.topk=0.5",
      "--model-option", "motion3.motion_fps_weight=1.0"],
     dict(DEFAULTS, subsample=3, threads=2, manual_fps=23.976, display="4k",
          views=("1.5h", "3h"), hfr="on", bitdepth="10", enc_size=(1280, 720), enc_bitdepth=8,
          model_options=("cambi.topk=0.5", "motion3.motion_fps_weight=1.0"))),
    (["--vmaf-version", "1", "0.6", "--output-format", "xml"],
     dict(DEFAULTS, vmaf_versions=("1", "0.6"), output_fmt="xml")),
], ids=["defaults", "all-options-4k", "both-versions-xml"])
def test_options_are_forwarded(batch, monkeypatch, options, forwarded):
    distorted = batch.files[0]
    calculation = batch.calc[distorted]

    run(monkeypatch, "-d", distorted, "-r", batch.reference, "--json", *options)

    assert batch.constructor.call_args.args == (distorted, batch.reference)
    assert batch.constructor.call_args.kwargs.items() >= forwarded.items()
    calculation.getVmaf.assert_called_once_with()
    if "--sync-window" in options:
        calculation.syncOffset.assert_called_once_with(0.4, 1.25, False)
    else:
        calculation.syncOffset.assert_not_called()
        assert calculation.offset == 0


def test_json_reports_v1_by_default(tmp_path, monkeypatch, capsys, ffmpeg_ok, scored):
    """JSON schema 2 contract, with VMAF v1 as the default model set."""
    reference, distorted = tmp_path / "ref.mkv", tmp_path / "dist.mkv"
    reference.touch()
    distorted.touch()

    run(monkeypatch, "-d", str(distorted), "-r", str(reference), "--fps", "10", "--json")

    assert strict_loads(capsys.readouterr().out) == {
        "schema_version": 2,
        "distorted": str(distorted),
        "reference": str(reference),
        "sync": {"offset": 0.0, "psnr": None},
        "vmaf": {
            "display": "hd",
            "pix_fmt": "yuv420p10le",
            "hfr": False,
            "scores": {"vmaf_v1_hd": 90.0, "vmaf_v1_phone": 90.0},
            "models": [
                {"name": "vmaf_v1_hd", "libvmaf_model": "vmaf_v1.0.16_3d0h",
                 "vmaf_version": "1", "view": "3h", "range": [0, 100]},
                {"name": "vmaf_v1_phone", "libvmaf_model": "vmaf_v1.0.16_5d0h",
                 "vmaf_version": "1", "view": "5h", "range": [0, 100]},
            ],
            "output_file": str(tmp_path / "dist_vmaf.json"),
        },
    }


def test_vmaf_v06_keeps_its_metrics(tmp_path, monkeypatch, capsys, ffmpeg_ok, scored):
    """3.x users migrating with --vmaf-version 0.6 keep vmaf_hd, vmaf_hd_neg and vmaf_hd_phone."""
    reference, distorted = tmp_path / "ref.mkv", tmp_path / "dist.mkv"
    reference.touch()
    distorted.touch()

    run(monkeypatch, "-d", str(distorted), "-r", str(reference), "--fps", "10",
        "--vmaf-version", "0.6", "--json")

    record = strict_loads(capsys.readouterr().out)
    assert list(record["vmaf"]["scores"]) == ["vmaf_hd", "vmaf_hd_neg", "vmaf_hd_phone"]
    assert record["vmaf"]["pix_fmt"] == "yuv420p"


@pytest.mark.parametrize("options", [["--sync-only"], ["--sync-only", "--json"], ["--json"]],
                         ids=" ".join)
def test_batch_processes_each_input_once_in_glob_order(batch, monkeypatch, capsys, options):
    run(monkeypatch, "-d", "*.mkv", "-r", batch.reference, "--sync-window", "0.4", *options)

    full = "--sync-only" not in options
    assert [call.args[0] for call in batch.constructor.call_args_list] == batch.files
    for distorted in batch.files:
        batch.calc[distorted].syncOffset.assert_called_once_with(0.4, 0, False)
        assert batch.calc[distorted].getVmaf.call_count == full
    lines = capsys.readouterr().out.splitlines()
    if "--json" not in options:
        assert lines == ["offset: {} | psnr: {}".format(index * 0.1, 30.1234567 + index)
                         for index in range(3)]
        return
    records = [strict_loads(line) for line in lines]
    assert [record["distorted"] for record in records] == batch.files
    for index, record in enumerate(records):
        assert record["schema_version"] == 2
        assert record["reference"] == batch.reference
        assert record["sync"] == {"offset": round(index * 0.1, 6),
                                  "psnr": round(30.1234567 + index, 6)}
        assert ("vmaf" in record) == full


@pytest.mark.parametrize("seconds, reverse, use_json", [
    ("0.2", False, False), ("0.2", True, True), ("0", True, True),
], ids=["forward-text", "reverse-json", "zero-reverse-json"])
def test_manual_offset_is_applied_and_reported_for_every_input(
        tmp_path, monkeypatch, capsys, ffmpeg_ok, scored, seconds, reverse, use_json):
    # Real preprocessing and trim construction; only probing and scoring are stubbed.
    reference = tmp_path / "reference.mkv"
    files = [tmp_path / "first.mkv", tmp_path / "second.mkv"]
    for path in [reference, *files]:
        path.touch()
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: [str(path) for path in files])
    instances = []

    def construct(*args, **kwargs):
        instances.append(vmaf(*args, **kwargs))
        instances[-1].syncOffset = Mock(side_effect=AssertionError("unexpected sync search"))
        return instances[-1]

    monkeypatch.setattr(cli, "vmaf", construct)

    run(monkeypatch, "-d", "*.mkv", "-r", str(reference), "--fps", "10",
        "--sync-start", seconds,
        *(["--reverse"] if reverse else []), *(["--json"] if use_json else []))

    expected = (-0.2 if reverse else 0.2) if float(seconds) else 0.0
    out = capsys.readouterr().out
    if use_json:
        records = [strict_loads(line) for line in out.splitlines()]
        assert all(record["sync"]["psnr"] is None for record in records)
        offsets = [record["sync"]["offset"] for record in records]
    else:
        offsets = [float(line.split("|")[0].split(":", 1)[1])
                   for line in out.splitlines() if line.startswith("offset:")]
    # Compare signs too: -ss 0 -reverse must not report -0.0.
    assert [(o, math.copysign(1, o)) for o in offsets] == [(expected, math.copysign(1, expected))] * 2
    assert len(instances) == 2
    for instance in instances:
        main = ";".join(instance.ffmpegQos.main.filtersList)
        ref = ";".join(instance.ffmpegQos.ref.filtersList)
        if not expected:
            assert "trim=" not in main + ref
            continue
        trimmed, untrimmed = (main, ref) if reverse else (ref, main)
        assert "trim=start=0.2:duration=1.0" in trimmed
        assert "trim=start=0:duration=1.0" in untrimmed


@pytest.mark.parametrize("progress", [False, True])
def test_ffmpeg_failure_stops_batch_without_reading_stale_results(
        tmp_path, monkeypatch, capsys, ffmpeg_ok, progress):
    files = [str(tmp_path / name) for name in ("first.mkv", "failed.mkv", "unreached.mkv")]
    reference = tmp_path / "reference.mkv"
    reference.touch()
    for filename in files:
        Path(filename).touch()
        # A previous run's result must never be reported for a failed calculation.
        write_scores(Path(filename).with_name(Path(filename).stem + "_vmaf.json"))
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: files)
    constructed = []

    def calculation(main, ref, **kwargs):
        constructed.append(main)
        qos = ffmpeg.FFmpegQos(main, ref)
        models = select_models("hd", ("0.6",))

        def get_vmaf():
            qos.getVmaf(models, print_progress=progress)
            return result_for(models, read_scores(qos.vmafpath, "json", model_names(models)),
                              qos.vmafpath)
        return SimpleNamespace(ffmpegQos=qos, getVmaf=get_vmaf)

    def process_for_command(cmd, **kwargs):
        failed = files[1] in cmd

        def progress_events():
            yield 0
            if failed:
                raise RuntimeError("controlled process failure")
            yield 100

        return SimpleNamespace(returncode=7 if failed else 0, communicate=lambda: (b"", None),
                               stderr="", run_command_with_progress=progress_events)

    monkeypatch.setattr(cli, "vmaf", calculation)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", process_for_command)
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", process_for_command)

    assert exit_code(monkeypatch, "-d", "*.mkv", "-r", str(reference), "--json",
                     *(["--progress"] if progress else [])) == 1

    out, err = capsys.readouterr()
    records = [strict_loads(line) for line in out.splitlines()]
    assert [record["distorted"] for record in records] == files[:1]
    assert constructed == files[:2]
    assert "FFmpeg execution failed" in err


def test_unserializable_result_stops_batch_without_partial_json(batch, monkeypatch, capsys):
    batch.calc[batch.files[1]].syncOffset.return_value = (float("inf"), 42.0)

    assert exit_code(monkeypatch, "-d", "*.mkv", "-r", batch.reference,
                     "--sync-window", "0.4", "--json") == 1

    out, err = capsys.readouterr()
    assert [strict_loads(line)["distorted"] for line in out.splitlines()] == batch.files[:1]
    assert "strict JSON" in err
    assert batch.constructor.call_count == 2
