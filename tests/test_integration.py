"""End-to-end checks against a real FFmpeg build with CPU libvmaf.

Each check compares easyVmaf with a plain FFmpeg command or with what a user
of the CLI observes; they are skipped when no suitable FFmpeg is installed.
"""

import dataclasses
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ROOT, run_cli, strict_loads
from easyvmaf import UnsupportedRangeError, Vmaf, ffmpeg
from easyvmaf.ffmpeg import FFmpegQos
from easyvmaf.models import CATALOG, ModelRun
from easyvmaf.results import read_frames

SCALE = "scale=1920:1080:flags=bicubic,fps=fps=10"


@pytest.fixture(scope="module")
def clips(encode, tmp_path_factory):
    """`lead` starts 0.2 s before `late`, which is blurred and carries audio."""
    directory = tmp_path_factory.mktemp("clips")
    lead, late = directory / "lead.mkv", directory / "late.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1.2", "-c:v", "ffv1", lead)
    encode("-i", lead, "-f", "lavfi", "-i", "sine=f=440:r=48000:d=1",
           "-filter:v", "trim=start=0.2,setpts=PTS-STARTPTS,gblur=sigma=1.5",
           "-c:v", "ffv1", "-c:a", "pcm_s16le", late)
    return SimpleNamespace(directory=directory, lead=str(lead), late=str(late))


def plain_ffmpeg(binary, first, second, graph):
    return subprocess.run([binary, "-hide_banner", "-y", "-i", first, "-i", second,
                           "-lavfi", graph, "-map", "[o]", "-f", "null", "-"],
                          capture_output=True, text=True, check=True, timeout=90).stderr


@pytest.mark.requires_libvmaf_v1
def test_check_ffmpeg_accepts_real_build(ffmpeg_bin):
    """A build that computes VMAF v1 is rejected, e.g. by a probe frame too small for v1."""
    result = ffmpeg.check_ffmpeg()
    assert result["meets_minimum"] is True
    assert result["libvmaf_v1"] is True


def write_psnr_stats(binary, target):
    source = "testsrc2=s=16x16:d=0.2:r=10"
    subprocess.run([binary, "-v", "error", "-f", "lavfi", "-i", source, "-f", "lavfi",
                    "-i", source, "-filter_complex",
                    "[0:v][1:v]psnr=stats_file=" + FFmpegQos._escape_filter_value(str(target)),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=90)
    assert target.stat().st_size > 0


def test_escaped_path_reaches_the_named_file(ffmpeg_bin, tmp_path):
    # Windows forbids ':' in file names and separates directories with '\';
    # there the drive letter of tmp_path ("C:\...") carries both.
    if os.name == "nt":
        name = "Take 1 video's copy [1080p];v2,final.log"
    else:
        name = "Take 1: video's copy [1080p];v2,final\\x.log"
    target = tmp_path / "my clips" / name
    target.parent.mkdir()
    write_psnr_stats(ffmpeg_bin, target)
    # stat() also succeeds on case-insensitive file systems (macOS, Windows).
    assert name in os.listdir(target.parent)


@pytest.mark.skipif(os.name != "nt", reason="UNC paths exist only on Windows")
def test_escaped_unc_path_reaches_the_named_file(ffmpeg_bin, tmp_path):
    # tmp_path through the admin share: C:\Users\... -> \\localhost\c$\Users\...
    local = str(tmp_path)
    unc = Path(f"\\\\localhost\\{local[0].lower()}$" + local[2:])
    try:
        (unc / "write_probe").write_text("x")
    except OSError:
        pytest.skip("admin share is not writable")
    write_psnr_stats(ffmpeg_bin, unc / "stats_unc.log")


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_sync_and_score_match_plain_ffmpeg(ffmpeg_bin, clips, monkeypatch, reverse):
    # Forward: the reference leads. Reverse: the distorted clip leads.
    distorted, reference = (clips.lead, clips.late) if reverse else (clips.late, clips.lead)
    monkeypatch.chdir(clips.directory)  # PSNR writes its stats file to cwd
    calculation = Vmaf(distorted, reference, vmaf_versions=("0.6",), fps=10, threads=1)

    offset, psnr = calculation.sync(0.4, reverse=reverse)

    assert offset == (-0.2 if reverse else 0.2)
    # Both directions compare `late` from 0 with `lead` from 0.2 s.
    stderr = plain_ffmpeg(ffmpeg_bin, clips.late, clips.lead,
                          f"[0:v]trim=start=0:duration=0.5,setpts=PTS-STARTPTS,{SCALE}[a];"
                          f"[1:v]trim=start=0.2:duration=0.5,setpts=PTS-STARTPTS,{SCALE}[b];"
                          "[a][b]psnr[o]")
    assert psnr == pytest.approx(float(re.search(r"average:(\S+)", stderr).group(1)), abs=1e-6)

    calculation.compute()

    output = Path(calculation.ffmpegQos.vmafpath)
    assert output == Path(distorted).with_name(Path(distorted).stem + "_vmaf.json")
    starts = (0.2, 0) if reverse else (0, 0.2)
    plain_ffmpeg(ffmpeg_bin, distorted, reference,
                 f"[0:v]{SCALE},trim=start={starts[0]},setpts=PTS-STARTPTS[d];"
                 f"[1:v]{SCALE},trim=start={starts[1]},setpts=PTS-STARTPTS[r];"
                 "[d][r]libvmaf=model=version=vmaf_v0.6.1:log_fmt=json:"
                 "log_path=plain.json:n_threads=1[o]")
    actual = json.loads(output.read_text())
    expected = json.loads(Path("plain.json").read_text())
    assert len(actual["frames"]) == len(expected["frames"]) == 10
    assert (actual["pooled_metrics"]["vmaf_hd"]["mean"]
            == pytest.approx(expected["pooled_metrics"]["vmaf"]["mean"], abs=1e-6))


@pytest.mark.requires_libvmaf_v1
def test_every_catalog_model_scores_in_one_pass(ffmpeg_bin, tmp_path):
    """A misspelt model id or a broken override syntax would only fail in production."""
    runs = []
    for spec in CATALOG:
        cambi = (("cambi.enc_width", "320"), ("cambi.enc_height", "180"),
                 ("cambi.enc_bitdepth", "8")) if spec.vmaf_version == "1" else ()
        runs.append(ModelRun(spec, spec.libvmaf_model, spec.options + cambi))
        if spec.hfr_model:
            hfr_spec = dataclasses.replace(spec, name=spec.name + "_hfr")
            runs.append(ModelRun(hfr_spec, spec.hfr_model, spec.options + cambi))
    log = tmp_path / "catalog.json"
    # v1 SpEED rejects small frames; 3d0h_2160 needs about 576x324.
    source = "testsrc2=s=640x360:r=5:d=0.4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", source, "-f", "lavfi",
                    "-i", source + ",gblur=sigma=1", "-lavfi",
                    "[0:v][1:v]libvmaf=log_fmt=json:log_path={}:model={}".format(
                        FFmpegQos._escape_filter_value(str(log)),
                        FFmpegQos._build_model_string(runs)),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=120)

    frames = json.loads(log.read_text())["frames"]
    assert frames
    for run in runs:
        low, high = run.spec.score_range
        assert all(low <= frame["metrics"][run.spec.name] <= high for frame in frames), run


@pytest.mark.requires_libvmaf_v1
def test_vmaf_v1_scores_a_scaled_rendition(encode, tmp_path):
    """Default v1 run end to end: 10-bit measurement, CAMBI and chroma features, heatmaps."""
    reference, distorted = tmp_path / "ref.mkv", tmp_path / "dist.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=10:d=1", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", reference)
    encode("-i", reference, "-vf", "scale=1280:720,gblur=sigma=1", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", distorted)

    result = Vmaf(str(distorted), str(reference), cambi_heatmap=True, threads=2).compute()

    assert set(result.scores) == {"vmaf_v1_hd", "vmaf_v1_phone"}
    assert all(0 <= score <= 100 for score in result.scores.values())
    assert result.pix_fmt == "yuv420p10le"
    metrics = json.loads(Path(result.log_path).read_text())["frames"][0]["metrics"]
    assert any(key.startswith("cambi") for key in metrics)
    assert any(key.startswith("speed_chroma") for key in metrics)
    assert result.cambi_heatmap_path == str(tmp_path / "dist_cambi_heatmap")
    assert any(Path(result.cambi_heatmap_path).iterdir())


@pytest.mark.requires_libvmaf_v1
def test_vmaf_v1_scores_a_144p_rendition(encode, tmp_path):
    """The lowest ABR rungs fail in libvmaf: CAMBI rejects encoding sizes below 180x150."""
    reference, distorted = tmp_path / "ref.mkv", tmp_path / "dist.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=10:d=1", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", reference)
    encode("-i", reference, "-vf", "scale=256:144", "-pix_fmt", "yuv420p", "-c:v", "ffv1",
           distorted)

    result = Vmaf(str(distorted), str(reference), threads=2).compute()

    assert set(result.scores) == {"vmaf_v1_hd", "vmaf_v1_phone"}
    assert all(0 <= score <= 100 for score in result.scores.values())


# tinterlace=mode=interleave_top from LGPL filters (the Docker image builds FFmpeg
# without --enable-gpl): each 10i frame weaves one field of two 20p frames.
INTERLACE_20P_TO_10I = ("separatefields,select='not(mod(n-1\\,4))+not(mod(n-2\\,4))',"
                        "weave=first_field=top,setpts=N/(10*TB),setfield=tff")

@pytest.mark.parametrize("reference_fps, reported_distorted_fps", [
    # 20p master vs its 10i (20 fields/s) broadcast: every field must be scored.
    (20, None),
    # Same 10i reported at its field rate, as ffprobe does for H.264 PAFF (which
    # free encoders cannot produce), vs a 10p reference: one frame per frame.
    (10, "20/1"),
], ids=["reference-at-field-rate", "distorted-reported-at-field-rate"])
def test_interlaced_distorted_scores_each_reference_frame_once(encode, tmp_path, reference_fps,
                                                               reported_distorted_fps):
    """Field deinterlacing scores only the first field, or pairs fields with the wrong instant."""
    source, reference, distorted = (tmp_path / name for name in ("src.mkv", "ref.mkv", "dist.ts"))
    encode("-f", "lavfi", "-i", "testsrc2=s=320x240:r=20:d=1", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", source)
    encode("-i", source, "-vf", "fps=%d" % reference_fps, "-c:v", "ffv1", reference)
    encode("-i", source, "-vf", INTERLACE_20P_TO_10I, "-r", "10",
           "-c:v", "mpeg2video", "-q:v", "2", "-flags", "+ilme+ildct", distorted)
    calculation = Vmaf(str(distorted), str(reference), vmaf_versions=("0.6",), threads=2)
    if reported_distorted_fps:
        calculation.main.streamInfo["r_frame_rate"] = reported_distorted_fps

    result = calculation.compute()

    assert len(json.loads(Path(result.log_path).read_text())["frames"]) == reference_fps
    assert calculation.output_fps == reference_fps


def test_reverse_sync_is_field_accurate_on_interlaced_distorted(encode, tmp_path):
    """Reverse sync trimmed the interlaced distorted video before deinterlacing:
    the right offset tied with the one a field earlier, which won, and the final
    calculation compared every picture with the previous field."""
    source, reference, distorted = (tmp_path / name for name in ("src.mkv", "ref.mkv", "dist.ts"))
    encode("-f", "lavfi", "-i", "testsrc2=s=320x240:r=20:d=3", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", source)
    # The broadcast recording (10i, one field per source frame) started 8 fields
    # (0.4 s) before the 20p reference clip.
    encode("-ss", "0.4", "-i", source, "-c:v", "ffv1", reference)
    encode("-i", source, "-vf", INTERLACE_20P_TO_10I, "-r", "10",
           "-c:v", "mpeg2video", "-q:v", "2", "-flags", "+ilme+ildct", distorted)

    offset, _ = Vmaf(str(distorted), str(reference), vmaf_versions=("0.6",),
                     threads=2).sync(0.5, start=0.1, reverse=True)

    assert offset == pytest.approx(-0.4)


@pytest.fixture(scope="module")
def gop_clips(encode, tmp_path_factory):
    """Long-GOP MP4 clips, with keyframes at different frames in each one:
    `lead` starts 0.3 s before `late` and `aligned`, both blurred. `interlaced`
    is a 10i version (Matroska) of the 20p `master`."""
    directory = tmp_path_factory.mktemp("gop")
    source, source_20p = directory / "source.mkv", directory / "source_20p.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=2.8", "-c:v", "ffv1", source)
    encode("-f", "lavfi", "-i", "testsrc2=s=320x240:r=20:d=2.5", "-pix_fmt", "yuv420p",
           "-c:v", "ffv1", source_20p)
    clips = SimpleNamespace(**{name: str(directory / name) for name in (
        "lead.mp4", "late.mp4", "aligned.mp4", "master.mp4", "interlaced.mkv")})
    mpeg4 = ("-c:v", "mpeg4", "-q:v", "4", "-bf", "2")
    encode("-i", source, *mpeg4, "-g", "8", getattr(clips, "lead.mp4"))
    encode("-i", source, "-vf", "trim=start=0.3,setpts=PTS-STARTPTS,gblur=sigma=1.5",
           *mpeg4, "-g", "7", getattr(clips, "late.mp4"))
    encode("-i", source, "-vf", "gblur=sigma=1.5", *mpeg4, "-g", "6",
           getattr(clips, "aligned.mp4"))
    encode("-i", source_20p, *mpeg4, "-g", "9", getattr(clips, "master.mp4"))
    encode("-i", source_20p, "-vf", INTERLACE_20P_TO_10I, "-r", "10", "-c:v", "mpeg2video",
           "-q:v", "4", "-g", "6", "-flags", "+ilme+ildct", getattr(clips, "interlaced.mkv"))
    return clips


@pytest.mark.requires_libvmaf_v1  # one case scores the default v1 models
@pytest.mark.parametrize("distorted, reference, offset, options, output_fmt", [
    ("late.mp4", "lead.mp4", 0.3, dict(vmaf_versions=("0.6",)), "json"),
    ("lead.mp4", "late.mp4", -0.3, {}, "xml"),
    # 0.3 s is 2.1 frames at 7 fps: the first measured reference frame is not
    # at the offset, but at the next frame of the converted rate.
    ("late.mp4", "lead.mp4", 0.3, dict(vmaf_versions=("0.6",), fps=7), "csv"),
    ("aligned.mp4", "lead.mp4", 0, dict(vmaf_versions=("0.6",)), "json"),
    # Field deinterlacing doubles the distorted frame rate (yadif=1).
    ("interlaced.mkv", "master.mp4", 0, dict(vmaf_versions=("0.6",)), "json"),
    # A 1080p encoding size halves the heatmap pictures (cambi_high_res_speedup):
    # their size is not the one in the file names.
    ("aligned.mp4", "late.mp4", -0.3,
     dict(views=("3h",), enc_size=(1920, 1080), cambi_heatmap=True), "json"),
], ids=["reference-trimmed", "distorted-trimmed-v1", "frame-rate-conversion", "no-offset",
        "interlaced-distorted", "cambi-heatmaps-v1"])
def test_frame_ranges_join_into_the_full_calculation(gop_clips, distorted, reference, offset,
                                                     options, output_fmt):
    """The contract an orchestrator relies on: consecutive ranges, cut between
    keyframes, give the frames of the full calculation, with the same frame
    numbers and identical scores; each CAMBI heatmap is the concatenation of
    the ranges' heatmaps."""
    def calculate(**frame_range):
        calculation = Vmaf(getattr(gop_clips, distorted), getattr(gop_clips, reference),
                           output_format=output_fmt, threads=2, sync_offset=offset,
                           **options, **frame_range)
        result = calculation.compute()
        return result, read_frames(result.log_path, output_fmt)

    def heatmaps(result):
        if not result.cambi_heatmap_path:
            return {}
        return {path.name: path.read_bytes()
                for path in sorted(Path(result.cambi_heatmap_path).iterdir())}

    full, frames = calculate()
    size = math.ceil(len(frames) / 3)
    joined, logs, joined_heatmaps = [], set(), {}
    for start in range(0, len(frames), size):
        result, chunk = calculate(start_frame=start, frame_count=size)
        assert result.frames_scored == len(chunk)
        joined += chunk
        logs.add(result.log_path)
        for name, data in heatmaps(result).items():
            joined_heatmaps[name] = joined_heatmaps.get(name, b"") + data

    assert len(frames) >= 15  # three ranges of several frames, cut between keyframes
    assert joined == frames
    assert len(logs) == 3 and full.log_path not in logs
    assert joined_heatmaps == heatmaps(full)
    if options.get("cambi_heatmap"):
        assert len(joined_heatmaps) == 5 and all(joined_heatmaps.values())


def test_frame_range_past_the_end_fails_instead_of_reading_a_stale_log(gop_clips, tmp_path):
    """An orchestrator plans ranges from an estimated frame count; the range
    past the end measures nothing, and libvmaf then writes no log at all."""
    distorted = tmp_path / "late.mp4"
    shutil.copyfile(getattr(gop_clips, "late.mp4"), distorted)
    stale = tmp_path / "late_vmaf_f1000-1009.json"
    # What an earlier run of the same range left, e.g. on a longer video with the same name.
    stale.write_text(json.dumps({"frames": [
        {"frameNum": n, "metrics": dict.fromkeys(("vmaf_hd", "vmaf_hd_neg", "vmaf_hd_phone"), 99.0)}
        for n in range(1000, 1010)], "pooled_metrics": {}}))
    calculation = Vmaf(str(distorted), getattr(gop_clips, "lead.mp4"), vmaf_versions=("0.6",),
                       threads=2, start_frame=1000, frame_count=10, sync_offset=0.3)

    with pytest.raises(UnsupportedRangeError, match="after the last measured frame"):
        calculation.compute()


def test_frame_range_rejects_mpegts(gop_clips, encode, tmp_path):
    """MPEG-TS seeks to the next keyframe: a range would silently score other frames."""
    ts = tmp_path / "lead.ts"
    encode("-i", getattr(gop_clips, "lead.mp4"), "-c", "copy", ts)

    with pytest.raises(UnsupportedRangeError, match="mpegts"):
        Vmaf(str(ts), getattr(gop_clips, "late.mp4"), start_frame=10, frame_count=10)


def test_sync_worker_stops_decoding_after_trim(encode, tmp_path, monkeypatch):
    # With -map 0:v/1:v every 300-frame input was decoded to EOF, and audio
    # was decoded without -an. Frame counts are deterministic, not timing.
    clip = tmp_path / "long.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=30", "-f", "lavfi",
           "-i", "sine=f=440:r=48000:d=30", "-c:v", "ffv1", "-c:a", "pcm_s16le", clip)
    monkeypatch.chdir(tmp_path)
    captured = []
    real_check_output = subprocess.check_output

    def check_output(cmd, **kwargs):
        output = real_check_output(cmd, **kwargs)
        captured.append(output.decode("utf-8", "replace"))
        return output

    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    qos = FFmpegQos(str(clip), str(clip), loglevel="verbose")
    qos.ref.setTrimFilter(0.2, 0.5)
    qos.main.setTrimFilter(0, 0.5)
    qos.getPsnr()
    # Sync runs from the user's directory, which may not be writable.
    assert list(tmp_path.iterdir()) == [clip]

    decoded = re.findall(r"Input stream #(\d+):\d+ \((\w+)\): \d+ packets read .*?; "
                         r"(\d+) frames decoded", captured[0])
    if not decoded:
        pytest.skip("FFmpeg does not report per-stream decode statistics")
    video = {index: int(frames) for index, kind, frames in decoded if kind == "video"}
    assert sorted(video) == ["0", "1"]
    assert all(frames < 100 for frames in video.values()), video
    # Versions that list unselected streams must report them as undecoded.
    assert all(int(frames) == 0 for _, kind, frames in decoded if kind != "video")


@pytest.mark.requires_libvmaf_v1  # the real CLI startup check requires VMAF v1
@pytest.mark.parametrize("sync_only", [True, False], ids=["sync_only-batch", "full"])
def test_cli_writes_a_strict_json_summary_per_input(encode, tmp_path, sync_only):
    reference = tmp_path / "reference.mkv"
    # VMAF v1 (the default) rejects CAMBI encoding sizes below 180x150.
    encode("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=1", "-c:v", "ffv1", reference)
    shutil.copyfile(reference, tmp_path / "dist-same.mkv")
    # Not eq=brightness: eq is GPL-only and the Docker image's FFmpeg is not.
    encode("-i", reference, "-vf", "lutyuv=y=val+13", "-c:v", "ffv1",
           tmp_path / "dist-bright.mkv")
    pattern = "dist-*.mkv" if sync_only else "dist-same.mkv"

    result = run_cli("-d", str(tmp_path / pattern), "-r", str(reference),
                     "--sync-window", "0.2", "--fps", "10", "--threads", "1",
                     "--verbose", "--progress",
                     *(["--sync-only"] if sync_only else []), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "FFmpeg" in result.stderr  # logging still happens, on stderr
    summaries = sorted(tmp_path.glob("*_summary.json"))
    assert [path.name for path in summaries] == (
        ["dist-bright_sync_summary.json", "dist-same_sync_summary.json"] if sync_only
        else ["dist-same_vmaf_summary.json"])
    records = {Path(record["distorted"]).name: record
               for record in (strict_loads(path.read_text()) for path in summaries)}
    # Identical frames: FFmpeg reports PSNR inf, which strict JSON cannot carry.
    assert records["dist-same.mkv"]["sync"] == {"offset": 0.0, "psnr": None,
                                                "psnr_status": "positive_infinity"}
    if sync_only:
        assert math.isfinite(records["dist-bright.mkv"]["sync"]["psnr"])
        assert all("vmaf" not in record for record in records.values())
        assert not list(tmp_path.glob("*_vmaf*"))
    else:
        assert Path(records["dist-same.mkv"]["vmaf"]["output_file"]).is_file()


def test_cli_without_ffmpeg_fails_on_stderr(tmp_path):
    result = run_cli("-d", "dist", "-r", "ref", cwd=tmp_path,
                     FFMPEG=str(tmp_path / "missing-ffmpeg"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ERROR" in result.stderr and "FFmpeg" in result.stderr
    assert "Traceback" not in result.stderr


_SIGINT_HARNESS = r'''
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from easyvmaf import cli, ffmpeg
from easyvmaf.models import select_models
from easyvmaf.results import VmafResult

root = Path(sys.argv[1])
progress = sys.argv[2] == "True"
binary = sys.argv[3]
children = []
original_popen = ffmpeg.subprocess.Popen

def tracked_popen(*args, **kwargs):
    child = original_popen(*args, **kwargs)
    children.append(child)
    return child

ffmpeg.subprocess.Popen = tracked_popen
cli.check_ffmpeg = lambda: dict(meets_minimum=True, libvmaf_v1=True,
                              version_str="test", cuda_vmaf=False)
files = [str(root / "first.mp4"), str(root / "interrupted.mp4")]
cli.glob.glob = lambda pattern: files

class Calculation:
    def __init__(self, main, reference, **kwargs):
        self.main = main
        self.ffmpegQos = ffmpeg.FFmpegQos(main, reference)

    def compute(self):
        output = str(root / (Path(self.main).stem + "_vmaf.json"))
        self.ffmpegQos.vmafpath = output
        if self.main == files[0]:
            return VmafResult(scores={"vmaf_hd": 90, "vmaf_hd_neg": 89, "vmaf_hd_phone": 95},
                              models=select_models("hd", ("0.6",)), display="hd",
                              pix_fmt="yuv420p", hfr=False, log_path=output)
        # Exercise the real process lifecycle without media files or a long score.
        def commit():
            self.ffmpegQos._cmd = [binary, "-hide_banner", "-loglevel", "error",
                "-re", "-f", "lavfi", "-i", "color=s=64x64:r=10",
                "-progress", str(root / "ready"), "-f", "null", "-"]
        self.ffmpegQos._commit = commit
        return self.ffmpegQos.getVmaf(select_models("hd", ("0.6",)), log_path=output,
                                      print_progress=progress)

cli.Vmaf = Calculation
sys.argv = ["easyvmaf", "-d", "*.mp4", "-r", str(root / "ref.mp4")]
try:
    cli.main()
finally:
    states = []
    for child in children:
        try:
            states.append(str(os.waitpid(child.pid, os.WNOHANG)))
        except ChildProcessError:
            states.append("already-reaped")
    (root / "reaped.json").write_text(json.dumps(states))
'''


@pytest.mark.parametrize("progress", [False, True])
def test_sigint_keeps_completed_records_and_reaps_ffmpeg(ffmpeg_bin, tmp_path, progress):
    (tmp_path / "ref.mp4").touch()
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [sys.executable, "-c", _SIGINT_HARNESS, str(tmp_path), str(progress), ffmpeg_bin],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        cwd=tmp_path, start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        ready = tmp_path / "ready"
        while not (ready.exists() and ready.stat().st_size):
            assert process.poll() is None, "CLI exited before FFmpeg readiness handshake"
            assert time.monotonic() < deadline, "FFmpeg readiness handshake timed out"
            time.sleep(0.02)
        # Target only the parent: terminal process-group delivery cannot mask leaks.
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130
        assert json.loads((tmp_path / "reaped.json").read_text()) == ["already-reaped"]
        summaries = sorted(tmp_path.glob("*_summary.json"))
        assert [strict_loads(path.read_text())["distorted"] for path in summaries] == [
            str(tmp_path / "first.mp4")]
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
