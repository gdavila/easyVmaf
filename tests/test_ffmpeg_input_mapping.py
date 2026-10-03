"""FFmpeg QoS commands must decode only the video the filtergraph consumes.

Explicit `-map 0:v -map 1:v` added unfiltered outputs that decoded both
inputs to EOF even when trim limited the comparison to 0.5 s.
"""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import ffmpeg, vmaf


def assert_video_only_inputs(cmd, main, ref):
    assert '-map' not in cmd
    inputs = [i for i, arg in enumerate(cmd) if arg == '-i']
    assert [cmd[i + 1] for i in inputs] == [main, ref]
    output = len(cmd) - 3
    assert cmd[output:] == ['-f', 'null', '-']
    for option in ('-an', '-sn', '-dn'):
        assert cmd.count(option) == 1
        # Output options: after every input, before the output they apply to.
        assert inputs[-1] + 1 < cmd.index(option) < output


def test_psnr_command_maps_only_filtergraph_output(monkeypatch):
    check_output = Mock(return_value=b"[Parsed_psnr_2] PSNR y:41.0 average:40.0 min:39.0")
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    qos = ffmpeg.FFmpegQos("distorted.mp4", "reference.mp4")
    qos.ref.setTrimFilter(0.2, 0.5)
    qos.main.setTrimFilter(0, 0.5)

    assert qos.getPsnr() == 40.0
    assert check_output.call_args.args[0] is qos._cmd
    assert_video_only_inputs(qos._cmd, "distorted.mp4", "reference.mp4")


@pytest.mark.parametrize("gpu", [False, True])
def test_vmaf_command_maps_only_filtergraph_output(tmp_path, monkeypatch, gpu):
    # Command inspection only: GPU construction needs no CUDA runtime.
    process = SimpleNamespace(returncode=0, communicate=Mock(return_value=(b"", None)))
    popen = Mock(return_value=process)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", popen)
    qos = ffmpeg.FFmpegQos("distorted.mp4", "reference.mp4", gpu_mode=gpu)

    qos.getVmaf(log_path=str(tmp_path / "out.json"), gpu=gpu)

    assert popen.call_args.args[0] is qos._cmd
    assert_video_only_inputs(qos._cmd, "distorted.mp4", "reference.mp4")
    lavfi = qos._cmd[qos._cmd.index('-lavfi') + 1]
    assert ('libvmaf_cuda=' in lavfi) == gpu
    assert ('hwupload_cuda' in lavfi) == gpu


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    binary = ffmpeg.FFmpegQos._executable
    probe = ffmpeg.FFprobe._executable
    if not binary or not probe or not shutil.which(binary) or not shutil.which(probe):
        pytest.skip("FFmpeg/FFprobe unavailable for input-mapping integration")
    filters = subprocess.run([binary, "-hide_banner", "-filters"],
                             capture_output=True, text=True, check=True)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf for input-mapping integration")
    directory = tmp_path_factory.mktemp("input_mapping")

    def run(*arguments):
        subprocess.run([binary, "-v", "error", "-y", *arguments], check=True,
                       capture_output=True, text=True, timeout=90)

    reference = directory / "reference.mkv"
    distorted = directory / "distorted.mkv"
    long_reference = directory / "long_reference.mkv"
    long_distorted = directory / "long_distorted.mkv"
    run("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=2.5", "-c:v", "ffv1",
        str(reference))
    # Distorted starts 0.2 s into the reference and carries an audio track.
    run("-i", str(reference), "-f", "lavfi", "-i", "sine=f=440:r=48000:d=2.3",
        "-filter:v", "trim=start=0.2,setpts=PTS-STARTPTS,gblur=sigma=1.5",
        "-c:v", "ffv1", "-c:a", "pcm_s16le", str(distorted))
    run("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=30", "-c:v", "ffv1",
        str(long_reference))
    run("-i", str(long_reference), "-f", "lavfi", "-i", "sine=f=440:r=48000:d=30",
        "-filter:v", "gblur=sigma=1.5", "-c:v", "ffv1", "-c:a", "pcm_s16le",
        str(long_distorted))
    return SimpleNamespace(binary=binary, directory=directory,
                           reference=str(reference), distorted=str(distorted),
                           long_reference=str(long_reference),
                           long_distorted=str(long_distorted))


def test_sync_psnr_matches_independent_ffmpeg_control(clips, monkeypatch):
    # PSNR writes a stats file in cwd; keep generated files in the clip directory.
    monkeypatch.chdir(clips.directory)
    calculation = vmaf(clips.distorted, clips.reference, "json", manual_fps=10, threads=1)
    results = {}
    for offset in (0.0, 0.2, 0.4):
        _, actual = calculation._computePsnrAtOffset(offset, reverse=False)
        # No easyVmaf builders; the psnr output is the only mapped stream.
        output = subprocess.run(
            [clips.binary, "-hide_banner", "-i", clips.distorted, "-i", clips.reference,
             "-lavfi",
             "[0:v]trim=start=0:duration=0.5,setpts=PTS-STARTPTS,"
             "scale=1920:1080:flags=bicubic,fps=fps=10[d];"
             f"[1:v]trim=start={offset}:duration=0.5,setpts=PTS-STARTPTS,"
             "scale=1920:1080:flags=bicubic,fps=fps=10[r];"
             "[d][r]psnr[o]",
             "-map", "[o]", "-f", "null", "-"],
            capture_output=True, text=True, check=True, timeout=90).stderr
        expected = float(re.search(r"average:(\S+)", output).group(1))
        assert actual == pytest.approx(expected, abs=1e-6)
        results[offset] = actual
    assert max(results, key=results.get) == 0.2


def test_final_vmaf_matches_independent_ffmpeg_control(clips, monkeypatch):
    monkeypatch.chdir(clips.directory)
    calculation = vmaf(clips.distorted, clips.reference, "json", manual_fps=10, threads=1)
    calculation.offset = 0.2
    calculation.getVmaf()
    actual = json.loads(open(calculation.ffmpegQos.vmafpath).read())

    subprocess.run(
        [clips.binary, "-v", "error", "-y", "-i", clips.distorted, "-i", clips.reference,
         "-lavfi",
         "[0:v]scale=1920:1080:flags=bicubic,fps=fps=10,"
         "trim=start=0,setpts=PTS-STARTPTS[d];"
         "[1:v]scale=1920:1080:flags=bicubic,fps=fps=10,"
         "trim=start=0.2,setpts=PTS-STARTPTS[r];"
         "[d][r]libvmaf=model=version=vmaf_v0.6.1:log_fmt=json:"
         "log_path=control.json:n_threads=1[o]",
         "-map", "[o]", "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=90)
    expected = json.loads((clips.directory / "control.json").read_text())

    assert len(actual["frames"]) == len(expected["frames"]) == 23
    assert (actual["pooled_metrics"]["vmaf_hd"]["mean"]
            == pytest.approx(expected["pooled_metrics"]["vmaf"]["mean"], abs=1e-6))


def test_sync_worker_stops_decoding_after_trim(clips, monkeypatch):
    # Decoder frame counts are deterministic in kind (not timing): the old
    # -map outputs decoded all 300 frames; audio was decoded without -an.
    monkeypatch.chdir(clips.directory)
    captured = []
    real_check_output = subprocess.check_output

    def check_output(cmd, **kwargs):
        output = real_check_output(cmd, **kwargs)
        captured.append(output.decode("utf-8", "replace"))
        return output

    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    qos = ffmpeg.FFmpegQos(clips.long_distorted, clips.long_reference, loglevel="verbose")
    qos.ref.setTrimFilter(0.2, 0.5)
    qos.main.setTrimFilter(0, 0.5)
    qos.getPsnr()

    decoded = re.findall(r"Input stream #(\d+):\d+ \((\w+)\): \d+ packets read .*?; "
                         r"(\d+) frames decoded", captured[0])
    if not decoded:
        pytest.skip("FFmpeg does not report per-stream decode statistics")
    video = {index: int(frames) for index, kind, frames in decoded if kind == "video"}
    assert sorted(video) == ["0", "1"]
    assert all(frames < 100 for frames in video.values()), video
    # Versions that list unselected streams must report them as undecoded.
    assert all(int(frames) == 0 for _, kind, frames in decoded if kind != "video")
