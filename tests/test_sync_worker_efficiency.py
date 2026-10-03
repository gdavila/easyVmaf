"""Sync workers: one interlace probe per input, single-threaded FFmpeg processes.

The pool already runs one FFmpeg process per worker, so each worker runs FFmpeg
with one thread. The final VMAF command keeps FFmpeg's default threading.
"""

import shutil
import subprocess
import threading
import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import ffmpeg, vmaf


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Replace FFprobe/FFmpeg; record interlace probes and PSNR commands."""
    probes, commands, lock = Counter(), [], threading.Lock()

    def get_frames_info(self):
        with lock:
            probes[self.videoSrc] += 1
        # Slow enough for concurrent workers to race on the unlocked lazy cache.
        time.sleep(0.05)
        return [{"interlaced_frame": 0, "pkt_size": 100}] * 10

    def check_output(cmd, **kwargs):
        commands.append(list(cmd))
        return b"average:20.0"

    process = SimpleNamespace(returncode=0, communicate=Mock(return_value=(b"", None)))
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: {
        "width": 320, "height": 180, "r_frame_rate": "10/1",
        "duration": "2.0", "start_time": "0",
    })
    monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo", get_frames_info)
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(return_value=process))
    return SimpleNamespace(probes=probes, commands=commands)


@pytest.mark.parametrize("manual_fps", [0, 10])
def test_interlace_is_probed_once_per_input_before_workers(fake_ffmpeg, manual_fps):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json",
                       threads=4, manual_fps=manual_fps)

    calculation.syncOffset(1.0)

    assert len(fake_ffmpeg.commands) == 10
    expected = {"distorted.mkv": 1, "reference.mkv": 1} if manual_fps == 0 else {}
    assert dict(fake_ffmpeg.probes) == expected


def test_only_sync_workers_run_ffmpeg_single_threaded(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", "json", threads=2, manual_fps=10)

    calculation.syncOffset(0.4)
    calculation.getVmaf()

    assert len(fake_ffmpeg.commands) == 4
    for cmd in fake_ffmpeg.commands:
        inputs = [i for i, arg in enumerate(cmd) if arg == "-i"]
        # Decoder threads are an input option: immediately before each -i.
        assert [cmd[i - 2:i] for i in inputs] == [["-threads", "1"]] * 2
        assert cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    final = calculation.ffmpegQos._cmd
    assert "-threads" not in final
    assert "-filter_complex_threads" not in final


def test_single_thread_psnr_matches_default_threading(tmp_path, monkeypatch):
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        pytest.skip("FFmpeg unavailable for single-thread PSNR integration")
    # PSNR writes a stats file in cwd; keep it in this test directory.
    monkeypatch.chdir(tmp_path)

    reference = tmp_path / "reference.mp4"
    distorted = tmp_path / "distorted.mp4"
    for arguments in (
        ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1", "-c:v", "mpeg4", str(reference)],
        ["-i", str(reference), "-vf", "gblur=sigma=1.5", "-c:v", "mpeg4", str(distorted)],
    ):
        subprocess.run([binary, "-v", "error", "-y", *arguments], check=True,
                       capture_output=True, timeout=90)

    def psnr(single_thread):
        qos = ffmpeg.FFmpegQos(str(distorted), str(reference))
        qos._single_thread = single_thread
        for stream in (qos.main, qos.ref):
            stream.setTrimFilter(0.2, 0.5)
            stream.setScaleFilter(1920, 1080)
        return qos.getPsnr()

    assert psnr(True) == pytest.approx(psnr(False), abs=1e-6)
