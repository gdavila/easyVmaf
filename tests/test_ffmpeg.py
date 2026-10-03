"""Layer 1: capability probe, filter escaping, command shape and process lifecycle."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import ffmpeg
from easyvmaf.ffmpeg import FFmpegQos


def response(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def probes(monkeypatch):
    """Stub the three check_ffmpeg commands: -version, model probe, -filters."""
    monkeypatch.setattr(FFmpegQos, "_executable", "/test/ffmpeg")

    def install(version="ffmpeg version 7.1.1 Copyright", model=response(), filters=response()):
        if isinstance(version, str):
            version = response(stdout=version)
        run = Mock(side_effect=[version, model, filters])
        monkeypatch.setattr(ffmpeg.subprocess, "run", run)
        return run
    return install


@pytest.mark.parametrize("output, version, version_str, minimum", [
    ("ffmpeg version 7.1.1 Copyright", (7, 1, 0), "7.1", True),
    ("ffmpeg version 4.4.5 Copyright", (4, 4, 0), "4.4", False),
    ("ffmpeg version N-111825-gabcdef123 Copyright", (0, 0, 0), "dev-build", True),
    ("ffmpeg version git-abcdef123 Copyright", (0, 0, 0), "dev-build", True),
])
def test_check_ffmpeg_parses_release_and_dev_builds(probes, output, version, version_str, minimum):
    run = probes(version=output)
    assert ffmpeg.check_ffmpeg() == {
        "version": version, "version_str": version_str, "meets_minimum": minimum,
        "builtin_models": True, "cuda_vmaf": False,
    }
    assert all(isinstance(call.args[0], list) and not call.kwargs.get("shell")
               for call in run.call_args_list)


@pytest.mark.parametrize("version, match", [
    ("", "version could not be parsed"),
    ("ffmpeg version N-garbage", "version could not be parsed"),
    (response(23, "ffmpeg version 7.1", "broken loader"), "exit code 23.*broken loader"),
])
def test_check_ffmpeg_rejects_unusable_version_output(probes, version, match):
    run = probes(version=version)
    with pytest.raises(RuntimeError, match=match):
        ffmpeg.check_ffmpeg()
    assert run.call_count == 1


def test_check_ffmpeg_requires_resolved_binary(probes, monkeypatch):
    run = probes()
    monkeypatch.setattr(FFmpegQos, "_executable", None)
    with pytest.raises(RuntimeError, match="not found"):
        ffmpeg.check_ffmpeg()
    run.assert_not_called()


def test_failed_model_probe_reports_no_builtin_models(probes):
    probes(model=response(1, stderr="could not load libvmaf model"))
    assert ffmpeg.check_ffmpeg()["builtin_models"] is False


@pytest.mark.parametrize("listing, expected", [
    (" ... libvmaf_cuda      VV->V CUDA VMAF\n", True),
    (" ... libvmaf           VV->V VMAF\n", False),
    (" ... other_filter      VV->V Like libvmaf_cuda\n", False),
], ids=["cuda-build", "cpu-build", "name-in-description"])
def test_cuda_detection_requires_exact_filter_name(probes, listing, expected):
    probes(filters=response(stdout=listing))
    assert ffmpeg.check_ffmpeg()["cuda_vmaf"] is expected


# Escaping is per character, so each special character plus a passthrough
# case specify the whole function.
@pytest.mark.parametrize("raw, escaped", [
    ("\\", "\\" * 4),
    ("'", "\\" * 3 + "'"),
    (":", "\\" * 2 + ":"),
    (",", "\\,"),
    (";", "\\;"),
    ("[", "\\["),
    ("]", "\\]"),
    ("~/my clips/$take-1*?.mkv", "~/my clips/$take-1*?.mkv"),
], ids=["backslash", "quote", "colon", "comma", "semicolon", "lbracket", "rbracket", "inert"])
def test_escape_filter_value(raw, escaped):
    assert FFmpegQos._escape_filter_value(raw) == escaped


def test_qos_commands_decode_only_filtered_video(tmp_path, monkeypatch):
    # -map 0:v/1:v added unfiltered outputs that decoded both inputs to EOF
    # despite trim; -an/-sn/-dn stop auto-selection of other streams.
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", Mock(
        return_value=b"[Parsed_psnr_2] PSNR y:41.0 average:40.0 min:39.0"))
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
        return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
    qos = FFmpegQos("distorted.mp4", "reference.mp4")
    qos.ref.setTrimFilter(0.2, 0.5)
    qos.main.setTrimFilter(0, 0.5)

    assert qos.getPsnr() == 40.0
    psnr_cmd = qos._cmd
    qos.getVmaf(log_path=str(tmp_path / "out.json"))

    for cmd in (psnr_cmd, qos._cmd):
        assert "-map" not in cmd
        inputs = [i for i, arg in enumerate(cmd) if arg == "-i"]
        assert [cmd[i + 1] for i in inputs] == ["distorted.mp4", "reference.mp4"]
        assert cmd[-3:] == ["-f", "null", "-"]
        for option in ("-an", "-sn", "-dn"):
            # Output options: after every input, before the output.
            assert inputs[-1] + 1 < cmd.index(option) < len(cmd) - 3


@pytest.mark.parametrize("progress", [False, True])
def test_interrupted_vmaf_kills_and_reaps_child(monkeypatch, progress):
    # Library callers get KeyboardInterrupt; the CLI's SIGINT handler raises
    # SystemExit(130), which the real-process test in test_integration covers.
    error = KeyboardInterrupt()
    child = SimpleNamespace(poll=Mock(return_value=None), kill=Mock(), wait=Mock(),
                            communicate=Mock(side_effect=error),
                            stdout=Mock(), stdin=Mock(), stderr=None)

    def interrupted_progress():
        yield 0
        raise error

    # Older ffmpeg-progress-yield releases leave .process alive on interruption.
    monkeypatch.setattr(ffmpeg, "FfmpegProgress", lambda cmd: SimpleNamespace(
        process=child, stderr="", run_command_with_progress=interrupted_progress))
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: child)

    with pytest.raises(KeyboardInterrupt) as raised:
        FFmpegQos("dist", "ref").getVmaf(print_progress=progress)

    assert raised.value is error
    child.kill.assert_called_once_with()
    assert child.wait.call_args.kwargs["timeout"] > 0
    child.stdout.close.assert_called_once_with()
    child.stdin.close.assert_called_once_with()


def test_cleanup_timeout_preserves_interruption(monkeypatch, caplog):
    error = SystemExit(130)
    child = SimpleNamespace(
        poll=Mock(return_value=None), kill=Mock(),
        wait=Mock(side_effect=subprocess.TimeoutExpired("ffmpeg", 5)),
        communicate=Mock(side_effect=error), stdout=Mock(), stdin=None, stderr=None)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs: child)

    with pytest.raises(SystemExit) as raised:
        FFmpegQos("dist", "ref").getVmaf()

    assert raised.value is error
    child.stdout.close.assert_called_once_with()
    assert "Could not reap interrupted FFmpeg process" in caplog.text
