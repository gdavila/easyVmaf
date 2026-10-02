"""Strict NDJSON and diagnostic-channel regressions for F05/F06."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import cli, ffmpeg


SCORES = {"vmaf_hd": 91.1234567, "vmaf_hd_neg": 90.0, "vmaf_hd_phone": 95.0}
CAPABILITIES = dict(meets_minimum=True, builtin_models=True,
                    version_str="9.0", cuda_vmaf=False)


def strict_loads(value):
    def reject_constant(token):
        raise ValueError("Non-JSON constant: " + token)
    return json.loads(value, parse_constant=reject_constant)


@pytest.fixture
def calculation(tmp_path, monkeypatch):
    distorted, reference = tmp_path / "distorted.mkv", tmp_path / "reference.mkv"
    distorted.touch()
    reference.touch()
    output = tmp_path / "distorted_vmaf.json"
    output.write_text(json.dumps({"frames": [{"metrics": SCORES}]}))
    instance = SimpleNamespace(syncOffset=Mock(return_value=(0.1234567, 42.1234567)),
                               getVmaf=Mock(),
                               ffmpegQos=SimpleNamespace(vmafpath=str(output)))
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: CAPABILITIES.copy())
    monkeypatch.setattr(cli, "vmaf", lambda *args, **kwargs: instance)
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(distorted), "-r", str(reference),
                                      "-sw", "0.2", "-json"])
    return instance


@pytest.mark.parametrize("sync_only", [False, True])
@pytest.mark.parametrize("psnr, expected", [
    (42.1234567, {"psnr": 42.123457}),
    (None, {"psnr": None}),
    (float("inf"), {"psnr": None, "psnr_status": "positive_infinity"}),
    (float("-inf"), {"psnr": None, "psnr_status": "negative_infinity"}),
    (float("nan"), {"psnr": None, "psnr_status": "nan"}),
])
def test_psnr_json_schema(calculation, capsys, sync_only, psnr, expected):
    calculation.syncOffset.return_value = (0.1234567, psnr)
    if sync_only:
        sys.argv.append("-sync_only")

    cli.main()

    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    result = strict_loads(captured.out)
    assert result["sync"] == dict(offset=0.123457, **expected)
    assert result["distorted"] == sys.argv[2]
    assert result["reference"] == sys.argv[4]
    if sync_only:
        assert set(result) == {"distorted", "reference", "sync"}
        calculation.getVmaf.assert_not_called()
    else:
        assert set(result) == {"distorted", "reference", "sync", "vmaf"}
        assert result["vmaf"] == dict(model="HD", output_file=calculation.ffmpegQos.vmafpath,
                                      **{key: round(value, 6) for key, value in SCORES.items()})
        calculation.getVmaf.assert_called_once_with()


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["sync_offset", "full_offset", "vmaf"])
def test_unsupported_nonfinite_result_fails_without_partial_json(
        calculation, capsys, value, field):
    if field == "vmaf":
        scores = dict(SCORES, vmaf_hd=value)
        Path(calculation.ffmpegQos.vmafpath).write_text(
            json.dumps({"frames": [{"metrics": scores}]}))
    else:
        calculation.syncOffset.return_value = (value, 42)
        if field == "sync_offset":
            sys.argv.append("-sync_only")

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err
    assert "JSON" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("sync_only", [False, True])
def test_batch_keeps_only_prior_success_on_serialization_failure(
        calculation, monkeypatch, capsys, sync_only):
    first = sys.argv[2]
    second = str(Path(first).with_name("second.mkv"))
    Path(second).touch()
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: [first, second])
    calculation.syncOffset.side_effect = [(0.1, 42), (float("inf"), 42)]
    if sync_only:
        sys.argv.append("-sync_only")

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    records = [strict_loads(line) for line in captured.out.splitlines()]
    assert len(records) == 1
    assert records[0]["distorted"] == first
    assert "JSON" in captured.err


@pytest.mark.parametrize("missing_index, message", [
    (2, "Distorted Video files not found"),
    (4, "Reference Video file not found"),
])
def test_missing_input_diagnostics_only_use_stderr(calculation, capsys, missing_index, message):
    Path(sys.argv[missing_index]).unlink()

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    calculation.getVmaf.assert_not_called()
    calculation.syncOffset.assert_not_called()


@pytest.mark.parametrize("failure, message", [
    ("missing", "missing FFmpeg"),
    ("meets_minimum", "requires FFmpeg"),
    ("builtin_models", "built-in models are not available"),
    ("cuda_vmaf", "libvmaf_cuda filter"),
])
def test_startup_diagnostics_only_use_stderr(monkeypatch, capsys, failure, message):
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", "dist", "-r", "ref", "-json", "-gpu"])
    capabilities = dict(CAPABILITIES, cuda_vmaf=True)
    if failure == "missing":
        check = Mock(side_effect=RuntimeError("missing FFmpeg"))
    else:
        capabilities[failure] = False
        check = Mock(return_value=capabilities)
    monkeypatch.setattr(cli, "check_ffmpeg", check)
    constructor = Mock()
    monkeypatch.setattr(cli, "vmaf", constructor)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    constructor.assert_not_called()


@pytest.mark.parametrize("arguments, code", [
    ([], 1),
    (["-json"], 2),
    (["-d", "dist", "-r", "ref", "-sw", "bad", "-json"], 2),
    (["-d", "dist", "-r", "ref", "-unknown", "-json"], 2),
])
def test_usage_errors_and_help_only_use_stderr(monkeypatch, capsys, arguments, code):
    monkeypatch.setattr(sys, "argv", ["easyvmaf"] + arguments)
    check = Mock()
    monkeypatch.setattr(cli, "check_ffmpeg", check)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    check.assert_not_called()


def test_explicit_help_still_uses_stdout(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-h"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert captured.err == ""


def test_interrupt_diagnostic_uses_stderr_and_preserves_exit_code(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.handler(None, None)
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SIGINT" in captured.err


def cli_environment(**overrides):
    return dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), **overrides)


def test_real_missing_ffmpeg_leaves_stdout_empty(tmp_path):
    result = subprocess.run([sys.executable, "-m", "easyvmaf", "-d", "dist", "-r", "ref",
                             "-json"], capture_output=True, text=True, cwd=tmp_path,
                            env=cli_environment(FFMPEG=str(tmp_path / "missing-ffmpeg")))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ERROR" in result.stderr
    assert "FFmpeg" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("sync_only, count", [(True, 1), (True, 2), (False, 1)])
def test_real_identical_clips_emit_strict_json_with_verbose_progress(tmp_path, sync_only, count):
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        pytest.skip("FFmpeg unavailable for CPU JSON reproduction")
    filters = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True,
                             text=True, cwd=tmp_path)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf for CPU JSON reproduction")
    reference = tmp_path / "reference.mkv"
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=64x64:r=10:d=1", "-c:v", "ffv1", str(reference)],
                   check=True, capture_output=True, cwd=tmp_path)
    clips = [tmp_path / ("dist-%d.mkv" % index) for index in range(count)]
    for clip in clips:
        shutil.copyfile(reference, clip)
    command = [sys.executable, "-m", "easyvmaf", "-d", str(tmp_path / "dist-*.mkv"),
               "-r", str(reference), "-sw", "0.2", "-fps", "10", "-threads", "1",
               "-json", "-verbose", "-progress"]
    if sync_only:
        command.append("-sync_only")

    result = subprocess.run(command, capture_output=True, text=True,
                            env=cli_environment(), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    records = [strict_loads(line) for line in result.stdout.splitlines()]
    assert len(records) == count
    assert {record["distorted"] for record in records} == {str(clip) for clip in clips}
    for record in records:
        assert record["sync"] == {"offset": 0.0, "psnr": None,
                                   "psnr_status": "positive_infinity"}
        assert ("vmaf" not in record) == sync_only
    assert "FFmpeg" in result.stderr
