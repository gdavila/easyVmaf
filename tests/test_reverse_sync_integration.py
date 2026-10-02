"""CPU reverse-sync regression against an independent FFmpeg HD control."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from easyvmaf import ffmpeg, vmaf


def test_reverse_sync_matches_independent_ffmpeg_control(tmp_path, monkeypatch):
    binary = ffmpeg.FFmpegQos._executable
    probe = ffmpeg.FFprobe._executable
    if not binary or not probe or not shutil.which(binary) or not shutil.which(probe):
        pytest.skip("FFmpeg/FFprobe unavailable for reverse-sync CPU integration")
    filters = subprocess.run([binary, "-hide_banner", "-filters"],
                             capture_output=True, text=True, check=True)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf for reverse-sync CPU integration")
    # PSNR writes a stats file in cwd; keep all generated files in this test directory.
    monkeypatch.chdir(tmp_path)

    def run(*arguments):
        subprocess.run([binary, "-v", "error", "-y", *arguments], check=True,
                       capture_output=True, text=True, timeout=90)

    distorted = tmp_path / "distorted.mkv"
    reference = tmp_path / "reference.mkv"
    run("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1.2", "-c:v", "ffv1",
        str(distorted))
    run("-i", str(distorted), "-vf", "trim=start=0.2,setpts=PTS-STARTPTS",
        "-c:v", "ffv1", str(reference))

    calculation = vmaf(str(distorted), str(reference), "json", manual_fps=10, threads=1)
    offset, _ = calculation.syncOffset(0.4, reverse=True)
    assert offset == pytest.approx(-0.2, abs=0.1)
    calculation.getVmaf()
    actual_path = Path(calculation.ffmpegQos.vmafpath)
    actual = json.loads(actual_path.read_text())["pooled_metrics"]["vmaf_hd"]["mean"]

    # No easyVmaf filter/model builders: D starts at frame C, R starts at frame 0.
    # Both streams use the same bicubic scaling, fps and duration as the API.
    run("-i", str(distorted), "-i", str(reference), "-lavfi",
        "[0:v]scale=1920:1080:flags=bicubic,fps=10,"
        "trim=start=0.2:duration=1,setpts=PTS-STARTPTS[d];"
        "[1:v]scale=1920:1080:flags=bicubic,fps=10,"
        "trim=start=0:duration=1,setpts=PTS-STARTPTS[r];"
        "[d][r]libvmaf=model=version=vmaf_v0.6.1:log_fmt=json:"
        "log_path=oracle.json:n_threads=1", "-f", "null", "-")
    expected = json.loads((tmp_path / "oracle.json").read_text())["pooled_metrics"]["vmaf"]["mean"]
    print("reverse-sync offset={}, automatic={}, direct={}, output={}".format(
        offset, actual, expected, actual_path))
    # Allow only floating-point/log rounding differences within the same runtime.
    assert actual == pytest.approx(expected, abs=0.01)
    assert actual_path == tmp_path / "distorted_vmaf.json"
    assert not (tmp_path / "reference_vmaf.json").exists()
