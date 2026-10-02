"""VMAF execution must fail before consumers can use a previous result."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import ffmpeg


@pytest.mark.parametrize("existing_output", [False, True])
def test_nonzero_exit_raises_before_returning(tmp_path, monkeypatch, existing_output):
    output = tmp_path / "previous.json"
    if existing_output:
        output.write_text('{"frames": []}')
    process = SimpleNamespace(returncode=23, communicate=Mock(return_value=(b"", None)))
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(return_value=process))
    qos = ffmpeg.FFmpegQos("distorted.mp4", "reference.mp4")

    with pytest.raises(RuntimeError, match="FFmpeg") as error:
        qos.getVmaf(log_path=str(output))

    assert type(error.value).__name__ == "FFmpegExecutionError"
    assert error.value.returncode == 23
    assert error.value.cmd == qos._cmd
    process.communicate.assert_called_once_with()


@pytest.mark.parametrize("progress", [False, True])
def test_success_preserves_process_return_type(monkeypatch, progress):
    process = SimpleNamespace(returncode=0, communicate=Mock(), stderr="",
                              run_command_with_progress=lambda: iter([0, 100]))
    factory = Mock(return_value=process)
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", factory)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", factory)
    qos = ffmpeg.FFmpegQos("distorted.mp4", "reference.mp4")
    assert qos.getVmaf(print_progress=progress) is process


@pytest.mark.parametrize("retained_process", [None, SimpleNamespace(returncode=9)])
def test_progress_failure_normalized_without_requiring_popen_attributes(monkeypatch, retained_process):
    # 1.1.3 clears .process in finally; older releases may retain it.
    def failed_progress():
        yield 0
        raise RuntimeError("Error running command: controlled failure")

    process = SimpleNamespace(stderr="controlled failure", process=retained_process,
                              run_command_with_progress=failed_progress)
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", lambda cmd: process)
    qos = ffmpeg.FFmpegQos("distorted.mp4", "reference.mp4")
    with pytest.raises(RuntimeError, match="FFmpeg") as error:
        qos.getVmaf(print_progress=True)
    assert type(error.value).__name__ == "FFmpegExecutionError"
    assert error.value.cmd == qos._cmd
    assert error.value.returncode == (9 if retained_process else None)
    assert isinstance(error.value.__cause__, RuntimeError)
