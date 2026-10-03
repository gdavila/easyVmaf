"""End-to-end checks against a real FFmpeg build with CPU libvmaf.

Each check compares easyVmaf with a plain FFmpeg command or with what a user
of the CLI observes; they are skipped when no suitable FFmpeg is installed.
"""

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

from conftest import ROOT, probe_libvmaf_model, run_cli, strict_loads
from easyvmaf import ffmpeg, vmaf
from easyvmaf.ffmpeg import FFmpegQos

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


def test_check_ffmpeg_accepts_real_build(ffmpeg_bin):
    result = ffmpeg.check_ffmpeg()
    assert result["meets_minimum"] is True
    assert result["builtin_models"] is True


@pytest.mark.requires_libvmaf_v1
@pytest.mark.parametrize("model", ["vmaf_v1.0.16_3d0h", "vmaf_v1.0.16_hfr_3d0h", "vmaf_v0.6.1"])
def test_built_in_model_scores_a_frame(model):
    assert probe_libvmaf_model(model) is None


def test_escaped_path_reaches_the_named_file(ffmpeg_bin, tmp_path):
    target = tmp_path / "my clips" / "take 1: video's copy [1080p];v2,final\\x.log"
    target.parent.mkdir()
    source = "testsrc2=s=16x16:d=0.2:r=10"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", source, "-f", "lavfi",
                    "-i", source, "-filter_complex",
                    "[0:v][1:v]psnr=stats_file=" + FFmpegQos._escape_filter_value(str(target)),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=90)
    assert target.stat().st_size > 0


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_sync_and_score_match_plain_ffmpeg(ffmpeg_bin, clips, monkeypatch, reverse):
    # Forward: the reference leads. Reverse: the distorted clip leads.
    distorted, reference = (clips.lead, clips.late) if reverse else (clips.late, clips.lead)
    monkeypatch.chdir(clips.directory)  # PSNR writes its stats file to cwd
    calculation = vmaf(distorted, reference, "json", manual_fps=10, threads=1)

    offset, psnr = calculation.syncOffset(0.4, reverse=reverse)

    assert offset == (-0.2 if reverse else 0.2)
    # Both directions compare `late` from 0 with `lead` from 0.2 s.
    stderr = plain_ffmpeg(ffmpeg_bin, clips.late, clips.lead,
                          f"[0:v]trim=start=0:duration=0.5,setpts=PTS-STARTPTS,{SCALE}[a];"
                          f"[1:v]trim=start=0.2:duration=0.5,setpts=PTS-STARTPTS,{SCALE}[b];"
                          "[a][b]psnr[o]")
    assert psnr == pytest.approx(float(re.search(r"average:(\S+)", stderr).group(1)), abs=1e-6)

    calculation.getVmaf()

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

    decoded = re.findall(r"Input stream #(\d+):\d+ \((\w+)\): \d+ packets read .*?; "
                         r"(\d+) frames decoded", captured[0])
    if not decoded:
        pytest.skip("FFmpeg does not report per-stream decode statistics")
    video = {index: int(frames) for index, kind, frames in decoded if kind == "video"}
    assert sorted(video) == ["0", "1"]
    assert all(frames < 100 for frames in video.values()), video
    # Versions that list unselected streams must report them as undecoded.
    assert all(int(frames) == 0 for _, kind, frames in decoded if kind != "video")


@pytest.mark.parametrize("sync_only", [True, False], ids=["sync_only-batch", "full"])
def test_cli_json_stdout_stays_ndjson_with_verbose_progress(encode, tmp_path, sync_only):
    reference = tmp_path / "reference.mkv"
    encode("-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=1", "-c:v", "ffv1", reference)
    shutil.copyfile(reference, tmp_path / "dist-same.mkv")
    # Not eq=brightness: eq is GPL-only and the Docker image's FFmpeg is not.
    encode("-i", reference, "-vf", "lutyuv=y=val+13", "-c:v", "ffv1",
           tmp_path / "dist-bright.mkv")
    pattern = "dist-*.mkv" if sync_only else "dist-same.mkv"

    result = run_cli("-d", str(tmp_path / pattern), "-r", str(reference), "-sw", "0.2",
                     "-fps", "10", "-threads", "1", "-json", "-verbose", "-progress",
                     *(["-sync_only"] if sync_only else []), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "FFmpeg" in result.stderr  # logging still happens, on stderr
    lines = result.stdout.splitlines()
    records = {Path(record["distorted"]).name: record for record in map(strict_loads, lines)}
    assert len(records) == len(lines) == (2 if sync_only else 1)
    # Identical frames: FFmpeg reports PSNR inf, which strict JSON cannot carry.
    assert records["dist-same.mkv"]["sync"] == {"offset": 0.0, "psnr": None,
                                                "psnr_status": "positive_infinity"}
    if sync_only:
        assert math.isfinite(records["dist-bright.mkv"]["sync"]["psnr"])
        assert all("vmaf" not in record for record in records.values())
        assert not list(tmp_path.glob("*_vmaf.*"))
    else:
        assert Path(records["dist-same.mkv"]["vmaf"]["output_file"]).is_file()


def test_cli_without_ffmpeg_fails_on_stderr(tmp_path):
    result = run_cli("-d", "dist", "-r", "ref", "-json", cwd=tmp_path,
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
        records = [strict_loads(line) for line in stdout.splitlines()]
        assert [record["distorted"] for record in records] == [str(tmp_path / "first.mp4")]
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
