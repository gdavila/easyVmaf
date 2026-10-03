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
from easyvmaf.results import read_scores


def run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["easyvmaf", *arguments])
    cli.main()


def exit_code(monkeypatch, *arguments):
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, *arguments)
    return exc.value.code


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
            getVmaf=Mock(return_value=SimpleNamespace(
                scores=dict(SCORES), log_path=str(output), cambi_heatmap_path=None)))
    constructor = Mock(side_effect=lambda main, ref, **kwargs: calculations[main])
    real_glob = cli.glob.glob
    monkeypatch.setattr(cli.glob, "glob", lambda p: files if p == "*.mkv" else real_glob(p))
    monkeypatch.setattr(cli, "vmaf", constructor)
    return SimpleNamespace(files=files, reference=str(reference), calc=calculations,
                           constructor=constructor)


@pytest.mark.parametrize("arguments, code, message", [
    ([], 1, "usage:"),
    (["-d", "dist.mkv", "-json"], 2, "-r"),
    (["-unknown"], 2, "-unknown"),
    (["-sw=-1"], 2, "-sw"),
    (["-ss=nan"], 2, "-ss"),
    (["-fps=inf"], 2, "-fps"),
    (["-subsample=0"], 2, "-subsample"),
    (["-subsample=1.5"], 2, "-subsample"),
    (["-threads=-1"], 2, "-threads"),
    (["-model=hd"], 2, "-model"),
    (["-output_fmt=JSON"], 2, "-output_fmt"),
    (["-sync_only"], 2, "-sync_only"),
    (["-sync_only", "-sw=0"], 2, "-sync_only"),
], ids=lambda value: " ".join(value) if isinstance(value, list) else None)
def test_invalid_arguments_fail_on_stderr_before_ffmpeg_check(monkeypatch, capsys,
                                                              arguments, code, message):
    if arguments and arguments[0] != "-d":
        arguments = ["-d", "dist.mkv", "-r", "ref.mkv", *arguments, "-json"]
    check = Mock()
    monkeypatch.setattr(cli, "check_ffmpeg", check)

    assert exit_code(monkeypatch, *arguments) == code

    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err and message in err
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

    assert exit_code(monkeypatch, "-d", "dist.mkv", "-r", "ref.mkv", "-gpu", "-json") == 1

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

    assert exit_code(monkeypatch, "-d", distorted, "-r", batch.reference, "-json") == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert message in err
    batch.constructor.assert_not_called()


HD = dict(subsample=1, threads=0, manual_fps=0, display="hd", vmaf_versions=("0.6",))


@pytest.mark.parametrize("options, forwarded, output_fmt", [
    ([], HD, "json"),
    (["-sw", "0.4", "-ss", "1.25", "-fps", "23.976", "-subsample", "3", "-threads", "2",
      "-model", "4K"], dict(HD, subsample=3, threads=2, manual_fps=23.976, display="4k"), "json"),
    (["-output_fmt", "xml"], HD, "xml"),
    (["-output_fmt", "csv"], HD, "csv"),
], ids=["defaults", "all-options-4k", "xml", "csv"])
def test_options_are_forwarded_and_scores_reported(batch, monkeypatch, capsys,
                                                   options, forwarded, output_fmt):
    distorted = batch.files[0]
    calculation = batch.calc[distorted]
    model = "4K" if forwarded["display"] == "4k" else "HD"
    names = model_names(select_models(forwarded["display"], ("0.6",)))
    scores = {name: SCORES.get(name, 92.0) for name in names}
    output = Path(distorted).with_name("scores." + output_fmt)
    calculation.getVmaf.return_value.scores = scores
    calculation.getVmaf.return_value.log_path = str(output)

    run(monkeypatch, "-d", distorted, "-r", batch.reference, "-json", *options)

    assert batch.constructor.call_args.args == (distorted, batch.reference)
    assert batch.constructor.call_args.kwargs.items() >= dict(forwarded, output_fmt=output_fmt).items()
    calculation.getVmaf.assert_called_once_with()
    if "-sw" in options:
        calculation.syncOffset.assert_called_once_with(0.4, 1.25, False)
    else:
        calculation.syncOffset.assert_not_called()
        assert calculation.offset == 0
    assert strict_loads(capsys.readouterr().out)["vmaf"] == dict(
        model=model, output_file=str(output),
        **{name: round(scores[name], 6) for name in names})


@pytest.mark.parametrize("options", [["-sync_only"], ["-sync_only", "-json"], ["-json"]],
                         ids=" ".join)
def test_batch_processes_each_input_once_in_glob_order(batch, monkeypatch, capsys, options):
    run(monkeypatch, "-d", "*.mkv", "-r", batch.reference, "-sw", "0.4", *options)

    full = "-sync_only" not in options
    assert [call.args[0] for call in batch.constructor.call_args_list] == batch.files
    for distorted in batch.files:
        batch.calc[distorted].syncOffset.assert_called_once_with(0.4, 0, False)
        assert batch.calc[distorted].getVmaf.call_count == full
    lines = capsys.readouterr().out.splitlines()
    if "-json" not in options:
        assert lines == ["offset: {} | psnr: {}".format(index * 0.1, 30.1234567 + index)
                         for index in range(3)]
        return
    records = [strict_loads(line) for line in lines]
    assert [record["distorted"] for record in records] == batch.files
    for index, record in enumerate(records):
        assert record["reference"] == batch.reference
        assert record["sync"] == {"offset": round(index * 0.1, 6),
                                  "psnr": round(30.1234567 + index, 6)}
        assert ("vmaf" in record) == full


@pytest.mark.parametrize("seconds, reverse, use_json", [
    ("0.2", False, False), ("0.2", True, True), ("0", True, True),
], ids=["forward-text", "reverse-json", "zero-reverse-json"])
def test_manual_offset_is_applied_and_reported_for_every_input(
        tmp_path, monkeypatch, capsys, ffmpeg_ok, seconds, reverse, use_json):
    # Real preprocessing and trim construction; only probing and scoring are stubbed.
    reference = tmp_path / "reference.mkv"
    files = [tmp_path / "first.mkv", tmp_path / "second.mkv"]
    for path in [reference, *files]:
        path.touch()
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: [str(path) for path in files])
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(STREAM))

    def score(qos, models, **kwargs):
        qos.vmafpath = str(Path(qos.main.videoSrc).with_suffix(".json"))
        write_scores(qos.vmafpath)

    instances = []

    def construct(*args, **kwargs):
        instances.append(vmaf(*args, **kwargs))
        instances[-1].syncOffset = Mock(side_effect=AssertionError("unexpected sync search"))
        return instances[-1]

    monkeypatch.setattr(ffmpeg.FFmpegQos, "getVmaf", score)
    monkeypatch.setattr(cli, "vmaf", construct)

    run(monkeypatch, "-d", "*.mkv", "-r", str(reference), "-fps", "10", "-ss", seconds,
        *(["-reverse"] if reverse else []), *(["-json"] if use_json else []))

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
            return SimpleNamespace(scores=read_scores(qos.vmafpath, "json", model_names(models)),
                                   log_path=qos.vmafpath, cambi_heatmap_path=None)
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

    assert exit_code(monkeypatch, "-d", "*.mkv", "-r", str(reference), "-json",
                     *(["-progress"] if progress else [])) == 1

    out, err = capsys.readouterr()
    records = [strict_loads(line) for line in out.splitlines()]
    assert [record["distorted"] for record in records] == files[:1]
    assert constructed == files[:2]
    assert "FFmpeg execution failed" in err


def test_unserializable_result_stops_batch_without_partial_json(batch, monkeypatch, capsys):
    batch.calc[batch.files[1]].syncOffset.return_value = (float("inf"), 42.0)

    assert exit_code(monkeypatch, "-d", "*.mkv", "-r", batch.reference, "-sw", "0.4",
                     "-json") == 1

    out, err = capsys.readouterr()
    assert [strict_loads(line)["distorted"] for line in out.splitlines()] == batch.files[:1]
    assert "strict JSON" in err
    assert batch.constructor.call_count == 2
