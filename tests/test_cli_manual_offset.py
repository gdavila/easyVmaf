"""Manual offsets must describe the alignment actually used for scoring (F10)."""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from easyvmaf import cli, ffmpeg, vmaf


@pytest.fixture
def calculation(tmp_path, monkeypatch):
    reference = tmp_path / "reference.mkv"
    reference.touch()
    distorted = tmp_path / "distorted.mkv"
    distorted.touch()
    instances = []
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(
        meets_minimum=True, builtin_models=True, version_str="9.0", cuda_vmaf=False))
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: {
        "width": 320, "height": 180, "r_frame_rate": "10/1",
        "duration": "1.2", "start_time": "0",
    })

    def score(qos, **kwargs):
        qos.vmafpath = str(Path(qos.main.videoSrc).with_suffix(".json"))
        Path(qos.vmafpath).write_text(json.dumps({"frames": [{"metrics": {
            "vmaf_hd": 91, "vmaf_hd_neg": 90, "vmaf_hd_phone": 95,
        }}]}))

    def construct(*args, **kwargs):
        instance = vmaf(*args, **kwargs)
        instance.syncOffset = Mock(side_effect=AssertionError("Unexpected sync search"))
        instances.append(instance)
        return instance

    # Keep real preprocessing and trim construction, replacing only probing/scoring.
    monkeypatch.setattr(ffmpeg.FFmpegQos, "getVmaf", score)
    monkeypatch.setattr(cli, "vmaf", construct)
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(distorted), "-r",
                                      str(reference), "-fps", "10"])
    return instances


def reported_offsets(output, use_json):
    if use_json:
        return [json.loads(line)["sync"]["offset"] for line in output.splitlines()]
    return [float(line.split("|", 1)[0].split(":", 1)[1])
            for line in output.splitlines() if line.startswith("offset:")]


@pytest.mark.parametrize("use_json", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("seconds", ["0.2", "0", "-0.0"])
def test_manual_offset_matches_report_and_trim(calculation, capsys, seconds, reverse, use_json):
    sys.argv.extend(["-ss", seconds])
    if reverse:
        sys.argv.append("-reverse")
    if use_json:
        sys.argv.append("-json")

    cli.main()

    instance, = calculation
    instance.syncOffset.assert_not_called()
    expected = (-0.2 if reverse else 0.2) if float(seconds) else 0
    assert instance.offset == expected
    output = capsys.readouterr().out
    offsets = reported_offsets(output, use_json)
    assert offsets == [expected]
    qos = instance.ffmpegQos
    main_filters, ref_filters = ";".join(qos.main.filtersList), ";".join(qos.ref.filtersList)
    if expected:
        trimmed, untrimmed = (main_filters, ref_filters) if reverse else (ref_filters, main_filters)
        assert "trim=start=0.2:duration=1.0" in trimmed
        assert "trim=start=0:duration=1.0" in untrimmed
    else:
        assert "trim=" not in main_filters + ref_filters
        assert math.copysign(1, instance.offset) == 1
        assert math.copysign(1, offsets[0]) == 1


@pytest.mark.parametrize("use_json", [False, True])
def test_reverse_manual_batch_uses_same_sign_for_each_distinct_instance(
        calculation, monkeypatch, capsys, use_json):
    first = sys.argv[2]
    second = str(Path(first).with_name("second.mkv"))
    Path(second).touch()
    monkeypatch.setattr(cli.glob, "glob", lambda pattern: [first, second])
    sys.argv.extend(["-ss", "0.2", "-reverse"])
    if use_json:
        sys.argv.append("-json")

    cli.main()

    output = capsys.readouterr().out
    assert reported_offsets(output, use_json) == [-0.2, -0.2]
    assert len(calculation) == 2
    assert calculation[0] is not calculation[1]
    assert [instance.main.videoSrc for instance in calculation] == [first, second]
    assert [instance.offset for instance in calculation] == [-0.2, -0.2]
    if use_json:
        records = [json.loads(line) for line in output.splitlines()]
        assert [record["distorted"] for record in records] == [first, second]


def test_real_manual_reverse_matches_automatic_alignment(tmp_path):
    binary, probe = ffmpeg.FFmpegQos._executable, ffmpeg.FFprobe._executable
    if not binary or not probe or not shutil.which(binary) or not shutil.which(probe):
        pytest.skip("FFmpeg/FFprobe unavailable for CPU manual-offset integration")
    filters = subprocess.run([binary, "-hide_banner", "-filters"],
                             capture_output=True, text=True, check=True)
    if " libvmaf " not in filters.stdout:
        pytest.skip("FFmpeg lacks libvmaf for CPU manual-offset integration")

    def run(*arguments):
        subprocess.run([binary, "-v", "error", "-y", *arguments], check=True,
                       capture_output=True, text=True, timeout=90, cwd=tmp_path)

    distorted, reference = tmp_path / "distorted.mkv", tmp_path / "reference.mkv"
    run("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1.2", "-c:v", "ffv1", str(distorted))
    run("-i", str(distorted), "-vf", "trim=start=0.2,setpts=PTS-STARTPTS",
        "-c:v", "ffv1", str(reference))
    command = [sys.executable, "-m", "easyvmaf", "-d", str(distorted), "-r", str(reference),
               "-fps", "10", "-threads", "1", "-json", "-reverse"]
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    records = []
    for option in (["-ss", "0.2"], ["-sw", "0.4"]):
        result = subprocess.run(command + option, capture_output=True, text=True,
                                env=environment, cwd=tmp_path, timeout=90)
        assert result.returncode == 0, result.stderr
        records.append(json.loads(result.stdout))
    manual, automatic = records
    assert manual["sync"]["offset"] == automatic["sync"]["offset"] == -0.2
    assert manual["sync"]["psnr"] is None
    for metric in ("vmaf_hd", "vmaf_hd_neg", "vmaf_hd_phone"):
        assert manual["vmaf"][metric] == pytest.approx(automatic["vmaf"][metric], abs=0.01)
    assert manual["vmaf"]["vmaf_hd"] > 99
