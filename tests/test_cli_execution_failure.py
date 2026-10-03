"""CLI regression coverage, including real CPU reproduction of F01."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import cli, ffmpeg


SCORES = {"vmaf_hd": 91.0, "vmaf_hd_neg": 90.0, "vmaf_hd_phone": 95.0}


@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("existing_output", [False, True])
def test_cli_does_not_read_results_after_execution_failure(tmp_path, monkeypatch, capsys,
                                                          progress, existing_output):
    distorted = tmp_path / "distorted.mp4"
    reference = tmp_path / "reference.mp4"
    distorted.touch()
    reference.touch()
    output = tmp_path / "distorted_vmaf.json"
    if existing_output:
        output.write_text(json.dumps({"frames": [{"metrics": SCORES}]}))
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(distorted), "-r", str(reference),
                                      "-json"] + (["-progress"] if progress else []))
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(meets_minimum=True,
                        builtin_models=True, version_str="9.0", cuda_vmaf=False))
    qos = ffmpeg.FFmpegQos(str(distorted), str(reference))
    monkeypatch.setattr(cli, "vmaf", lambda *args, **kwargs: SimpleNamespace(
        ffmpegQos=qos, getVmaf=lambda: qos.getVmaf(print_progress=progress)))
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs:
                        SimpleNamespace(returncode=7, communicate=lambda: (b"", None)))

    def failed_progress():
        yield 0
        raise RuntimeError("controlled process failure")

    monkeypatch.setattr(ffmpeg, "FfmpegProgress", lambda cmd: SimpleNamespace(
        stderr="controlled process failure", run_command_with_progress=failed_progress))
    # Opening even a valid artifact after the failed execution is a regression.
    open_result = Mock(side_effect=AssertionError("result opened after FFmpeg failure"))
    monkeypatch.setattr(cli, "open", open_result, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FFmpeg" in captured.err
    assert "ERROR" in captured.err
    open_result.assert_not_called()


def test_real_ffmpeg_failure_never_reuses_previous_score(tmp_path, monkeypatch, capsys):
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        pytest.skip("FFmpeg unavailable for CPU VMAF reproduction")
    filters = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf for CPU VMAF reproduction")
    clip = tmp_path / "distorted.mkv"
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=64x64:r=2:d=1", "-c:v", "ffv1", str(clip)], check=True,
                   capture_output=True)
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(clip), "-r", str(clip),
                                      "-fps", "2", "-threads", "1", "-json"])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    output = Path(result["vmaf"]["output_file"])
    previous_score = output.read_bytes()

    # Keep CLI arguments valid and fail inside the real scoring subprocess,
    # before FFmpeg can overwrite the valid result from the preceding run.
    original_commit = ffmpeg.FFmpegQos._commit

    def fail_scoring(qos):
        original_commit(qos)
        qos._cmd.append("-easyvmaf_test_invalid_option")

    monkeypatch.setattr(ffmpeg.FFmpegQos, "_commit", fail_scoring)
    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    failure = capsys.readouterr()
    assert failure.out == ""
    assert "FFmpeg execution failed" in failure.err
    assert "Traceback" not in failure.err
    assert output.read_bytes() == previous_score


@pytest.mark.parametrize("progress", [False, True])
def test_batch_stops_at_failure_preserving_only_prior_success(tmp_path, monkeypatch, capsys, progress):
    files = [str(tmp_path / name) for name in ["first.mp4", "failed.mp4", "unreached.mp4"]]
    reference = tmp_path / "reference.mp4"
    reference.touch()
    for filename in files:
        Path(filename).touch()
        Path(filename).with_name(Path(filename).stem + "_vmaf.json").write_text(
            json.dumps({"frames": [{"metrics": SCORES}]}))
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(tmp_path / "*.mp4"),
                                      "-r", str(reference), "-json"] +
                        (["-progress"] if progress else []))
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: files)
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(meets_minimum=True,
                        builtin_models=True, version_str="9.0", cuda_vmaf=False))
    constructed = []

    def calculation(main, ref, **kwargs):
        constructed.append(main)
        qos = ffmpeg.FFmpegQos(main, ref)
        return SimpleNamespace(ffmpegQos=qos,
                               getVmaf=lambda: qos.getVmaf(print_progress=progress))

    def process_for_command(cmd, **kwargs):
        failed = files[1] in cmd

        def progress_events():
            yield 0
            if failed:
                raise RuntimeError("controlled process failure")
            yield 100

        return SimpleNamespace(returncode=7 if failed else 0,
                               communicate=lambda: (b"", None), stderr="",
                               run_command_with_progress=progress_events)

    monkeypatch.setattr(cli, "vmaf", calculation)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", process_for_command)
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", process_for_command)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == 1
    assert records[0]["distorted"] == files[0]
    assert records[0]["vmaf"]["vmaf_hd"] == SCORES["vmaf_hd"]
    assert constructed == files[:2]
    assert "FFmpeg execution failed" in captured.err
