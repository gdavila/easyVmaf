"""Layer 2: parallel sync search and the final filter pipeline, with FFmpeg stubbed."""

import re
import threading
import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import STREAM
from easyvmaf import ffmpeg, vmaf


def inputs(cmd):
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """A 320x180 rendition of a 1080p reference; PSNR peaks when the trimmed input
    starts at 0.2 s. Probes and commands are recorded."""
    probes, commands, lock = Counter(), [], threading.Lock()

    def get_frames_info(self):
        with lock:
            probes[self.videoSrc] += 1
        # Slow enough for concurrent workers to race on the unlocked lazy cache.
        time.sleep(0.05)
        return [{"interlaced_frame": 0, "pkt_size": 100}] * 10

    def check_output(cmd, **kwargs):
        with lock:
            commands.append(list(cmd))
        graph = cmd[cmd.index("-lavfi") + 1]
        return b"average:50.0" if "trim=start=0.2:" in graph else b"average:20.0"

    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(
        STREAM, **(dict(width=1920, height=1080) if "reference" in self.videoSrc else {})))
    monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo", get_frames_info)
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
        return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
    return SimpleNamespace(probes=probes, commands=commands)


def test_repeated_searches_keep_shared_sources_and_roles(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json", manual_fps=10,
                       threads=1, gpu_mode=True)
    qos = calculation.ffmpegQos
    shared = (qos, qos.main, qos.ref, qos.main.videoSrc, qos.ref.videoSrc, qos.invertedSrc)

    for reverse in (True, True, False):
        fake_ffmpeg.commands.clear()
        assert calculation.syncOffset(0.4, reverse=reverse) == [-0.2 if reverse else 0.2, 50.0]
        qos = calculation.ffmpegQos
        assert (qos, qos.main, qos.ref, qos.main.videoSrc, qos.ref.videoSrc,
                qos.invertedSrc) == shared
        assert len(fake_ffmpeg.commands) == 4
        for cmd in fake_ffmpeg.commands:
            # Reverse workers swap inputs; sync stays on CPU even in GPU mode.
            assert inputs(cmd) == (["reference.mkv", "distorted.mkv"] if reverse
                                   else ["distorted.mkv", "reference.mkv"])
            graph = cmd[cmd.index("-lavfi") + 1]
            # Only the distorted input needs upscaling, whichever slot it takes.
            assert re.findall(r"\[input(\d)_\d+\]scale=", graph) == ["1" if reverse else "0"]
            assert "cuda" not in graph


@pytest.mark.parametrize("manual_fps", [0, 10])
def test_interlace_is_probed_once_per_input_before_workers(fake_ffmpeg, manual_fps):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json", threads=4,
                       manual_fps=manual_fps)

    calculation.syncOffset(1.0)

    assert len(fake_ffmpeg.commands) == 10
    # -fps skips the interlace probe entirely.
    expected = {"distorted.mkv": 1, "reference.mkv": 1} if manual_fps == 0 else {}
    assert dict(fake_ffmpeg.probes) == expected


def test_only_sync_workers_run_ffmpeg_single_threaded(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json", threads=2, manual_fps=10)

    calculation.syncOffset(0.4)
    calculation.getVmaf()

    assert len(fake_ffmpeg.commands) == 4
    for cmd in fake_ffmpeg.commands:
        positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
        # Decoder threads are an input option: immediately before each -i.
        assert [cmd[i - 2:i] for i in positions] == [["-threads", "1"]] * 2
        assert cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    final = calculation.ffmpegQos._cmd
    assert "-threads" not in final
    assert "-filter_complex_threads" not in final


def test_gpu_vmaf_uploads_after_cpu_filters_and_trims_distorted_on_reverse(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json", manual_fps=10,
                       threads=1, gpu_mode=True)
    calculation.syncOffset(0.4, reverse=True)
    # A repeated run rebuilds the chains: clearFilters() resets the hwupload guard.
    calculation.getVmaf()
    calculation.getVmaf()

    qos = calculation.ffmpegQos
    assert inputs(qos._cmd) == ["distorted.mkv", "reference.mkv"]
    assert qos.vmafpath == "distorted_vmaf.json"
    upload = ["format", "setparams", "hwupload_cuda"]
    for chain, steps, trim in (
            (qos.main, ["scale", "fps", "trim"] + upload, "trim=start=0.2:duration=1.0"),
            (qos.ref, ["fps", "trim"] + upload, "trim=start=0:duration=1.0")):
        assert [re.match(r"\[[^]]+\](\w+)", f).group(1) for f in chain.filtersList] == steps
        assert trim in chain.filtersList[steps.index("trim")]
        assert chain.filtersList[-1].endswith("[{}]".format(chain.lastOutputID))
    assert qos.vmafFilter[0].startswith("[{}][{}]libvmaf_cuda=".format(
        qos.main.lastOutputID, qos.ref.lastOutputID))
