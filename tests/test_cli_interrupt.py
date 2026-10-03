"""SIGINT must fail the CLI without orphaning its active VMAF child."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import cli, ffmpeg


def test_handler_exits_130_with_stderr_diagnostic(capsys):
    with pytest.raises(SystemExit) as error:
        cli.handler(signal.SIGINT, None)
    assert error.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SIGINT" in captured.err


@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, lambda: SystemExit(130)])
def test_interruption_reaps_child_and_propagates(monkeypatch, progress, interruption):
    error = interruption()
    child = SimpleNamespace(poll=Mock(return_value=None), kill=Mock(), wait=Mock(),
                            stdout=Mock(), stdin=Mock(), stderr=None)

    def interrupted_progress():
        yield 0
        raise error

    # Model older progress releases that leave .process alive on interruption.
    wrapper = SimpleNamespace(process=child, stderr="",
                              run_command_with_progress=interrupted_progress)
    child.communicate = Mock(side_effect=error)
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", lambda cmd: wrapper)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: child)
    with pytest.raises(type(error)) as raised:
        ffmpeg.FFmpegQos("dist", "ref").getVmaf(print_progress=progress)
    assert raised.value is error
    child.kill.assert_called_once_with()
    child.wait.assert_called_once()
    assert child.wait.call_args.kwargs["timeout"] > 0
    child.stdout.close.assert_called_once_with()
    child.stdin.close.assert_called_once_with()


def test_cleanup_timeout_preserves_interruption(monkeypatch, caplog):
    error = SystemExit(130)
    child = SimpleNamespace(
        poll=Mock(return_value=None), kill=Mock(),
        wait=Mock(side_effect=subprocess.TimeoutExpired("ffmpeg", 5)),
        communicate=Mock(side_effect=error), stdout=Mock(), stdin=None, stderr=None,
    )
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: child)
    with pytest.raises(SystemExit) as raised:
        ffmpeg.FFmpegQos("dist", "ref").getVmaf()
    assert raised.value is error
    child.wait.assert_called_once()
    assert child.wait.call_args.kwargs["timeout"] > 0
    child.stdout.close.assert_called_once_with()
    assert "Could not reap interrupted FFmpeg process" in caplog.text


_CLI_HARNESS = r'''
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from easyvmaf import cli, ffmpeg

root = Path(sys.argv[1])
progress = sys.argv[2] == "True"
binary = sys.argv[3]
children = []
original_popen = ffmpeg.subprocess.Popen

def tracked_popen(*args, **kwargs):
    child = original_popen(*args, **kwargs)
    children.append(child)
    (root / "child.pid").write_text(str(child.pid))
    return child

ffmpeg.subprocess.Popen = tracked_popen
cli.check_ffmpeg = lambda: dict(meets_minimum=True, builtin_models=True,
                              version_str="test", cuda_vmaf=False)
files = [str(root / "first.mp4"), str(root / "interrupted.mp4")]
cli.glob.glob = lambda pattern: files

class Calculation:
    def __init__(self, main, reference, **kwargs):
        self.main = main
        self.ffmpegQos = ffmpeg.FFmpegQos(main, reference)

    def getVmaf(self):
        output = str(root / "result.json")
        self.ffmpegQos.vmafpath = output
        if self.main == files[0]:
            Path(output).write_text(json.dumps({"frames": [{"metrics": {
                "vmaf_hd": 90, "vmaf_hd_neg": 89, "vmaf_hd_phone": 95}}]}))
            return
        # Exercise the real process lifecycle without media files or a long score.
        def commit():
            self.ffmpegQos._cmd = [binary, "-hide_banner", "-loglevel", "error",
                "-re", "-f", "lavfi", "-i", "color=s=64x64:r=10",
                "-progress", str(root / "ready"), "-f", "null", "-"]
        self.ffmpegQos._commit = commit
        return self.ffmpegQos.getVmaf(log_path=output, print_progress=progress)

cli.vmaf = Calculation
sys.argv = ["easyvmaf", "-d", "*.mp4", "-r", str(root / "ref.mp4"), "-json"]
try:
    cli.main()
finally:
    states = []
    for child in children:
        try:
            status = os.waitpid(child.pid, os.WNOHANG)
            states.append(str(status))
        except ChildProcessError:
            states.append("already-reaped")
    (root / "reaped.json").write_text(json.dumps(states))
'''


@pytest.mark.parametrize("progress", [False, True])
def test_real_sigint_preserves_completed_records_and_reaps_ffmpeg(tmp_path, progress):
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg is required for the real child cleanup regression")
    (tmp_path / "ref.mp4").touch()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [sys.executable, "-c", _CLI_HARNESS, str(tmp_path), str(progress), binary],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        cwd=tmp_path, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        ready = tmp_path / "ready"
        while not (ready.exists() and ready.stat().st_size):
            assert process.poll() is None, "CLI exited before FFmpeg readiness handshake"
            assert time.monotonic() < deadline, "FFmpeg readiness handshake timed out"
            time.sleep(0.02)
        # Target only the parent: terminal process-group delivery cannot mask leaks.
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        assert process.returncode == 130
        states = json.loads((tmp_path / "reaped.json").read_text())
        assert states == ["already-reaped"]
        stdout, stderr = process.communicate(timeout=3)
        records = [json.loads(line) for line in stdout.splitlines()]
        assert len(records) == 1
        assert records[0]["distorted"] == str(tmp_path / "first.mp4")
        assert records[0]["vmaf"]["vmaf_hd"] == 90
        assert "SIGINT" in stderr
        assert "Traceback" not in stderr
        assert "FFmpeg execution failed" not in stderr
    finally:
        # Isolated session: remove only this test's processes, including on failure.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
