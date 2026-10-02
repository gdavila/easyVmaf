"""Capability checks require successful FFmpeg commands, not missing errors."""

import shutil
import subprocess
from unittest.mock import Mock

import pytest

from easyvmaf import ffmpeg


def response(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def mock_commands(monkeypatch, model=None, filters=None, version=None):
    monkeypatch.setattr(ffmpeg.FFmpegQos, "_executable", "/test/ffmpeg")
    run = Mock(side_effect=[
        version if version is not None else response(stdout="ffmpeg version 7.1.1 Copyright"),
        model if model is not None else response(),
        filters if filters is not None else response(),
    ])
    monkeypatch.setattr(ffmpeg.subprocess, "run", run)
    return run


@pytest.mark.parametrize("detail", [
    "No such filter: 'libvmaf'",
    "could not load libvmaf model",
    "Error initializing filter: Invalid argument",
    "",
])
def test_failed_model_probe_never_claims_models(monkeypatch, detail):
    mock_commands(monkeypatch, model=response(1, stderr=detail))
    assert ffmpeg.check_ffmpeg()["builtin_models"] is False


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), PermissionError("denied")])
def test_unexecutable_binary_raises_runtime_error(monkeypatch, error):
    run = mock_commands(monkeypatch, version=error)
    with pytest.raises(RuntimeError, match="FFmpeg.*(/test/ffmpeg|binary)"):
        ffmpeg.check_ffmpeg()
    assert run.call_count == 1


def test_unresolved_binary_raises_before_subprocess(monkeypatch):
    run = mock_commands(monkeypatch)
    monkeypatch.setattr(ffmpeg.config, "ffmpeg", None)
    monkeypatch.setattr(ffmpeg.FFmpegQos, "_executable", None)
    with pytest.raises(RuntimeError, match="FFmpeg.*not found"):
        ffmpeg.check_ffmpeg()
    run.assert_not_called()


def test_failed_version_command_raises_with_diagnostic(monkeypatch):
    run = mock_commands(monkeypatch, version=response(23, "ffmpeg version 7.1", "broken loader"))
    with pytest.raises(RuntimeError, match="FFmpeg.*23.*broken loader"):
        ffmpeg.check_ffmpeg()
    assert run.call_count == 1


@pytest.mark.parametrize("output", ["", "garbage", "ffmpeg version unknown", "ffmpeg version N-garbage"])
def test_malformed_version_is_not_a_dev_build(monkeypatch, output):
    run = mock_commands(monkeypatch, version=response(stdout=output))
    with pytest.raises(RuntimeError, match="FFmpeg.*version"):
        ffmpeg.check_ffmpeg()
    assert run.call_count == 1


@pytest.mark.parametrize("output,version,version_str,minimum", [
    ("ffmpeg version 7.1.1 Copyright", (7, 1, 0), "7.1", True),
    ("ffmpeg version 4.4.5 Copyright", (4, 4, 0), "4.4", False),
    ("ffmpeg version N-111825-gabcdef123 Copyright", (0, 0, 0), "dev-build", True),
    ("ffmpeg version git-abcdef123 Copyright", (0, 0, 0), "dev-build", True),
])
def test_successful_release_and_dev_probes(monkeypatch, output, version, version_str, minimum):
    run = mock_commands(monkeypatch, version=response(stdout=output))
    assert ffmpeg.check_ffmpeg() == {
        "version": version, "version_str": version_str, "meets_minimum": minimum,
        "builtin_models": True, "cuda_vmaf": False,
    }
    assert run.call_count == 3
    for call in run.call_args_list:
        assert isinstance(call.args[0], list)
        assert call.kwargs.get("shell", False) is False


@pytest.mark.parametrize("listing,returncode,expected", [
    (" ... libvmaf_cuda      VV->V CUDA VMAF\n", 0, True),
    (" ... libvmaf_cuda      VV->V CUDA VMAF\n", 1, False),
    (" ... libvmaf_cuda_extra VV->V Another filter\n", 0, False),
    (" ... other_filter      VV->V Like libvmaf_cuda\n", 0, False),
    (" ... libvmaf           VV->V VMAF\n", 0, False),
])
def test_cuda_requires_successful_exact_filter_listing(monkeypatch, listing, returncode, expected):
    mock_commands(monkeypatch, filters=response(returncode, stdout=listing))
    assert ffmpeg.check_ffmpeg()["cuda_vmaf"] is expected


@pytest.mark.parametrize("stage", ["model", "filters"])
def test_probe_os_error_is_conservative(monkeypatch, stage):
    mock_commands(monkeypatch, **{stage: OSError("cannot execute probe")})
    result = ffmpeg.check_ffmpeg()
    assert result["builtin_models" if stage == "model" else "cuda_vmaf"] is False


def test_real_cpu_capability_smoke(monkeypatch):
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("FFmpeg is not installed")
    listing = subprocess.run([executable, "-hide_banner", "-filters"],
                             capture_output=True, text=True, shell=False)
    if listing.returncode != 0 or not any(
        len(line.split()) >= 2 and line.split()[1] == "libvmaf"
        for line in listing.stdout.splitlines()
    ):
        pytest.skip("CPU libvmaf filter is not compiled in")
    monkeypatch.setattr(ffmpeg.FFmpegQos, "_executable", executable)
    result = ffmpeg.check_ffmpeg()
    assert result["meets_minimum"] is True
    assert result["builtin_models"] is True
