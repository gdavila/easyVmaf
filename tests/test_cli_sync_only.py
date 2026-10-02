"""Sync-only CLI contracts and a real CPU batch regression for F03/F04."""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import cli, ffmpeg


def run_main():
    """Accept either a normal return or an explicit successful CLI exit."""
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0


@pytest.fixture
def batch(tmp_path, monkeypatch):
    # Deliberately preserve a nonalphabetical glob order.
    files = [str(tmp_path / name) for name in ("z.mp4", "a.mp4", "m.mp4")]
    reference = tmp_path / "reference.mp4"
    reference.touch()
    scores = {"vmaf_hd": 91, "vmaf_hd_neg": 90, "vmaf_hd_phone": 95}
    calculations = {}
    for index, filename in enumerate(files):
        Path(filename).touch()
        output = Path(filename).with_suffix(".json")
        output.write_text(json.dumps({"frames": [{"metrics": scores}]}))
        calculations[filename] = SimpleNamespace(
            syncOffset=Mock(return_value=(index * 0.1, 30.0 + index)),
            getVmaf=Mock(), ffmpegQos=SimpleNamespace(vmafpath=str(output)),
        )
    constructor = Mock(side_effect=lambda main, ref, **kwargs: calculations[main])
    monkeypatch.setattr(cli, "vmaf", constructor)
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(
        meets_minimum=True, builtin_models=True, version_str="9.0", cuda_vmaf=False))
    return files, str(reference), calculations, constructor


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("use_json", [False, True])
def test_sync_only_processes_each_input_once(batch, monkeypatch, capsys, count, use_json):
    files, reference, calculations, constructor = batch
    inputs = files[:count]
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: inputs)
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", "*.mp4", "-r", reference,
                                      "-sw", "0.4", "-sync_only"] +
                        (["-json"] if use_json else []))

    run_main()

    assert [call.args[0] for call in constructor.call_args_list] == inputs
    for filename in inputs:
        calculations[filename].syncOffset.assert_called_once_with(0.4, 0, False)
        calculations[filename].getVmaf.assert_not_called()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == count
    if use_json:
        assert [json.loads(line) for line in lines] == [
            {"distorted": filename, "reference": reference,
             "sync": {"offset": round(index * 0.1, 6), "psnr": 30.0 + index}}
            for index, filename in enumerate(inputs)
        ]
    else:
        assert lines == [f"offset: {index * 0.1} | psnr: {30.0 + index}"
                         for index in range(count)]


@pytest.mark.parametrize("window", [None, "0", "-0.4", "nan", "inf", "-inf"])
@pytest.mark.parametrize("use_json", [False, True])
def test_invalid_sync_only_window_rejected_before_environment_or_probing(
        monkeypatch, capsys, window, use_json):
    arguments = ["easyvmaf", "-d", "missing.mp4", "-r", "missing-ref.mp4", "-sync_only"]
    if window is not None:
        arguments.append("-sw=" + window)
    if use_json:
        arguments.append("-json")
    monkeypatch.setattr(sys, "argv", arguments)
    environment_check = Mock(return_value=dict(meets_minimum=True, builtin_models=True,
                                               version_str="9.0", cuda_vmaf=False))
    constructor = Mock()
    monkeypatch.setattr(cli, "check_ffmpeg", environment_check)
    monkeypatch.setattr(cli, "vmaf", constructor)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "-sync_only" in output.err
    assert "-sw" in output.err
    environment_check.assert_not_called()
    constructor.assert_not_called()


@pytest.mark.parametrize("window", [None, "-0.4", "0.4"])
def test_full_computation_still_runs_once_per_input(batch, monkeypatch, capsys, window):
    files, reference, calculations, constructor = batch
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: files)
    arguments = ["easyvmaf", "-d", "*.mp4", "-r", reference, "-json"]
    if window is not None:
        arguments.append("-sw=" + window)
    monkeypatch.setattr(sys, "argv", arguments)

    run_main()

    assert [call.args[0] for call in constructor.call_args_list] == files
    for calculation in calculations.values():
        calculation.getVmaf.assert_called_once_with()
        if window is None:
            calculation.syncOffset.assert_not_called()
        else:
            calculation.syncOffset.assert_called_once_with(0.4, 0, False)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [record["distorted"] for record in records] == files
    assert all(record["vmaf"]["vmaf_hd"] == 91 for record in records)


def test_real_sync_only_batch_emits_every_clip_without_vmaf(tmp_path):
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        pytest.skip("FFmpeg unavailable for CPU sync-only reproduction")
    filters = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf required by CLI startup check")
    reference = tmp_path / "reference.mkv"
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=64x64:r=10:d=1", "-c:v", "ffv1", str(reference)],
                   check=True, capture_output=True)
    inputs = [tmp_path / name for name in ("dist-z.mkv", "dist-a.mkv")]
    for index, clip in enumerate(inputs):
        # A brightness change keeps PSNR finite; nonfinite JSON belongs to T4.
        subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-i", str(reference),
                        "-vf", f"eq=brightness={0.03 * (index + 1)}", "-c:v", "ffv1", str(clip)],
                       check=True, capture_output=True)
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-m", "easyvmaf", "-d", str(tmp_path / "dist-*.mkv"),
                             "-r", str(reference), "-sw", "0.2", "-sync_only", "-fps", "10",
                             "-threads", "1", "-json"], capture_output=True, text=True,
                            env=environment, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == len(inputs)
    assert {record["distorted"] for record in records} == {str(clip) for clip in inputs}
    assert all("vmaf" not in record for record in records)
    assert all(math.isfinite(record["sync"]["psnr"]) for record in records)
    assert not list(tmp_path.glob("*_vmaf.*"))
