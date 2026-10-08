"""Layer 1: capability probe, filter escaping, command shape and process lifecycle."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import Vmaf, ffmpeg
from easyvmaf.ffmpeg import FFmpegQos
from easyvmaf.models import select_models


def response(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def probes(monkeypatch):
    """Stub the four check_ffmpeg commands: -version, model probe, -filters, -hwaccels."""
    monkeypatch.setattr(FFmpegQos, "_executable", "/test/ffmpeg")

    def install(version="ffmpeg version 8.1 Copyright", model=response(), filters=response(),
                hwaccels=response(stdout="Hardware acceleration methods:\ncuda\n\n")):
        if isinstance(version, str):
            version = response(stdout=version)
        run = Mock(side_effect=[version, model, filters, hwaccels])
        monkeypatch.setattr(ffmpeg.subprocess, "run", run)
        return run
    return install


@pytest.mark.parametrize("output, version, version_str, minimum", [
    ("ffmpeg version 8.1 Copyright", (8, 1, 0), "8.1", True),
    ("ffmpeg version 8.0.1 Copyright", (8, 0, 0), "8.0", False),
    ("ffmpeg version n9.0.1-20260301 Copyright", (9, 0, 0), "9.0", True),
    ("ffmpeg version N-111825-gabcdef123 Copyright", (0, 0, 0), "dev-build", True),
    ("ffmpeg version git-abcdef123 Copyright", (0, 0, 0), "dev-build", True),
])
def test_check_ffmpeg_parses_release_and_dev_builds(probes, output, version, version_str, minimum):
    run = probes(version=output)
    assert ffmpeg.check_ffmpeg() == {
        "version": version, "version_str": version_str, "meets_minimum": minimum,
        "libvmaf_v1": True, "cuda_vmaf": False, "hwaccels": ["cuda"],
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


def test_failed_v1_probe_reports_no_libvmaf_v1(probes):
    probes(model=response(1, stderr="problem during vmaf_use_features_from_model"))
    assert ffmpeg.check_ffmpeg()["libvmaf_v1"] is False


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
    qos.getVmaf(select_models("hd", ("0.6",)), log_path=str(tmp_path / "out.json"))

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
        FFmpegQos("dist", "ref").getVmaf(select_models("hd", ("0.6",)), print_progress=progress)

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
        FFmpegQos("dist", "ref").getVmaf(select_models("hd", ("0.6",)))

    assert raised.value is error
    child.stdout.close.assert_called_once_with()
    assert "Could not reap interrupted FFmpeg process" in caplog.text


# Captured from easyVmaf 3.x before layer 1 moved to ModelRun. The v0.6 model=
# string, the filter order and the libvmaf options must not change: a different
# command means different v0.6 scores or log paths for existing users. Only
# change (fps before scale, so deinterlacing runs before scaling) was authorized
# in 4.0: fps only picks frames, so the frames and the v0.6 scores are the same.
GOLDEN_CHAINS = {
    "HD": (r"[0:v]fps=fps=25.0[input0_0];[input0_0]scale=1920:1080:flags=bicubic[input0_1];"
           r"[input0_1]trim=start=0:duration=10.0, setpts=PTS-STARTPTS[input0_2];"
           r"[1:v]fps=fps=25.0[input1_0];"
           r"[input1_0]trim=start=1.5:duration=10.0, setpts=PTS-STARTPTS[input1_1];"
           r"[input0_2][input1_1]libvmaf=log_fmt=json:model="
           r"version=vmaf_v0.6.1\\:name=vmaf_hd|version=vmaf_v0.6.1neg\\:name=vmaf_hd_neg|"
           r"version=vmaf_v0.6.1\\:name=vmaf_hd_phone\\:enable_transform=true"
           r":n_subsample=1:log_path=dist_vmaf.json:n_threads=4:shortest=0:feature=name=psnr"),
    "4K": (r"[0:v]fps=fps=25.0[input0_0];[input0_0]scale=3840:2160:flags=bicubic[input0_1];"
           r"[input0_1]trim=start=0:duration=10.0, setpts=PTS-STARTPTS[input0_2];"
           r"[1:v]fps=fps=25.0[input1_0];[input1_0]scale=3840:2160:flags=bicubic[input1_1];"
           r"[input1_1]trim=start=1.5:duration=10.0, setpts=PTS-STARTPTS[input1_2];"
           r"[input0_2][input1_2]libvmaf=log_fmt=json:model=version=vmaf_4k_v0.6.1\\:name=vmaf_4k"
           r":n_subsample=1:log_path=dist_vmaf.json:n_threads=4:shortest=0:feature=name=psnr"),
}
GOLDEN_CAMBI = (r"|name=cambi\\:full_ref=true\\:enc_width=1280\\:enc_height=720"
                r"\\:src_width=1920\\:src_height=1080\\:heatmaps_path=dist_cambi_heatmap")


@pytest.mark.parametrize("model", ["HD", "4K"])
@pytest.mark.parametrize("cambi_heatmap", [False, True], ids=["plain", "cambi"])
def test_v06_vmaf_command_is_unchanged(monkeypatch, unscored, model, cambi_heatmap):
    """The v0.6 FFmpeg command changes, and with it v0.6 scores or output paths."""
    monkeypatch.setattr(FFmpegQos, "_executable", "ffmpeg")
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(
        width=1280, height=720, r_frame_rate="25/1", duration="10.0", start_time="0",
        pix_fmt="yuv420p") if self.videoSrc == "dist.mp4" else dict(
        width=1920, height=1080, r_frame_rate="25/1", duration="12.0", start_time="0",
        pix_fmt="yuv420p"))
    monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo",
                        lambda self: [{"interlaced_frame": 0, "pkt_size": 1}])
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
        return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
    calculation = Vmaf("dist.mp4", "ref.mp4", display=model.lower(), vmaf_versions=("0.6",),
                       threads=4, cambi_heatmap=cambi_heatmap, sync_offset=1.5)

    calculation.compute()

    graph = GOLDEN_CHAINS[model] + (GOLDEN_CAMBI if cambi_heatmap else "")
    assert calculation.ffmpegQos._cmd == [
        "ffmpeg", "-y", "-hide_banner", "-stats", "-loglevel", "info",
        "-i", "dist.mp4", "-i", "ref.mp4", "-an", "-sn", "-dn",
        "-lavfi", graph, "-f", "null", "-"]


# Captured at e02f2b4, before --enable-hwaccel: --gpu alone must keep this
# exact command (CPU decoding and filters, then hwupload_cuda). It also pins
# feature=name=psnr next to n_threads: without a CPU feature, libvmaf_cuda
# 3.2.1 segfaults on the first frame whenever n_threads is set.
GOLDEN_GPU = (
    r"[0:v]fps=fps=25.0[input0_0];[input0_0]scale=1920:1080:flags=bicubic[input0_1];"
    r"[input0_1]trim=start=0:duration=10.0, setpts=PTS-STARTPTS[input0_2];"
    r"[input0_2]format=yuv420p[input0_3];"
    r"[input0_3]setparams=colorspace=unknown:range=unknown[input0_4];"
    r"[input0_4]hwupload_cuda[input0_5];"
    r"[1:v]fps=fps=25.0[input1_0];"
    r"[input1_0]trim=start=1.5:duration=10.0, setpts=PTS-STARTPTS[input1_1];"
    r"[input1_1]format=yuv420p[input1_2];"
    r"[input1_2]setparams=colorspace=unknown:range=unknown[input1_3];"
    r"[input1_3]hwupload_cuda[input1_4];"
    r"[input0_5][input1_4]libvmaf_cuda=log_fmt=json:model="
    r"version=vmaf_v0.6.1\\:name=vmaf_hd|version=vmaf_v0.6.1neg\\:name=vmaf_hd_neg|"
    r"version=vmaf_v0.6.1\\:name=vmaf_hd_phone\\:enable_transform=true"
    r":n_subsample=1:log_path=dist_vmaf.json:n_threads=4:shortest=0:feature=name=psnr")


def test_gpu_vmaf_command_is_unchanged(monkeypatch, unscored):
    """The --gpu FFmpeg command changes, and with it --gpu scores, or libvmaf_cuda segfaults."""
    monkeypatch.setattr(FFmpegQos, "_executable", "ffmpeg")
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(
        width=1280, height=720, r_frame_rate="25/1", duration="10.0", start_time="0",
        pix_fmt="yuv420p") if self.videoSrc == "dist.mp4" else dict(
        width=1920, height=1080, r_frame_rate="25/1", duration="12.0", start_time="0",
        pix_fmt="yuv420p"))
    monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo",
                        lambda self: [{"interlaced_frame": 0, "pkt_size": 1}])
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
        return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
    calculation = Vmaf("dist.mp4", "ref.mp4", vmaf_versions=("0.6",), threads=4, gpu=True,
                       sync_offset=1.5)

    # vmaf.gpu in the summary tells these scores came from libvmaf_cuda.
    assert calculation.compute().gpu is True
    assert calculation.ffmpegQos._cmd == [
        "ffmpeg", "-y", "-hide_banner", "-stats", "-loglevel", "info",
        "-i", "dist.mp4", "-i", "ref.mp4", "-an", "-sn", "-dn",
        "-lavfi", GOLDEN_GPU, "-f", "null", "-"]
