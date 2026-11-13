"""Shared stubs and real-FFmpeg prerequisites."""

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from easyvmaf import ffmpeg, vmaf

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = dict(meets_minimum=True, libvmaf_v1=True, hdr_vmaf=False, version_str="9.0",
                    cuda_vmaf=False, hwaccels=["cuda"])
SCORES = {"vmaf_hd": 91.1234567, "vmaf_hd_neg": 90.0, "vmaf_hd_phone": 95.0}
STREAM = {"width": 320, "height": 180, "r_frame_rate": "10/1", "duration": "1.2", "start_time": "0",
          "codec_name": "h264", "pix_fmt": "yuv420p"}
LIBVMAF_V1_PROBE_MODEL = "vmaf_v1.0.16_3d0h"
HDR_VMAF_PROBE_MODEL = "hdr_vmaf_v1.1.8_3d0h"
PQ_CHAIN = ("setparams=range=tv:color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc,"
            "format=yuv420p10le")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires_libvmaf_v1: skip unless FFmpeg's libvmaf computes a VMAF v1 frame "
                   "(libvmaf >= 3.2.1 with built-in models)")
    config.addinivalue_line(
        "markers", "requires_hdr_vmaf: skip unless FFmpeg's libvmaf computes an HDR-VMAF frame "
                   "from PQ-tagged frames (libvmaf with zimg and the HDR models, FFmpeg passing "
                   "the frame colorimetry); a failure with EASYVMAF_REQUIRE_HDR=1")
    config.addinivalue_line(
        "markers", "requires_cuda: skip unless libvmaf_cuda computes a frame on a GPU; "
                   "never a failure: CI has no GPU runner")


def probe_libvmaf_model(model, chain=None):
    """Score one black 320x240 frame pair with a built-in model, each input
    through chain (a filter chain) first.

    Returns None when the frame is computed, otherwise the reason it was not:
    loading a model is not enough, since a default libvmaf 3.2.0 build loads
    the v1 models but cannot extract their features. v1's SpEED feature rejects
    frames below about 288x162 on any libvmaf (and crashes at 256x144), hence
    the size.
    """
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        return "FFmpeg unavailable"
    source = "color=black:s=320x240:r=1:d=1"
    inputs = "[0:v]%s[dis];[1:v]%s[ref];[dis][ref]" % (chain, chain) if chain else "[0:v][1:v]"
    result = subprocess.run([binary, "-hide_banner", "-nostdin", "-v", "error",
                             "-f", "lavfi", "-i", source, "-f", "lavfi", "-i", source,
                             "-lavfi", inputs + "libvmaf=model=version=" + model,
                             "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        return None
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    # FFmpeg's own last lines are generic ("Nothing was written..."); libvmaf's say why.
    detail = ([line for line in lines if line.startswith("libvmaf ERROR")]
              or [line for line in lines if "libvmaf" in line]
              or lines or ["exit code %d" % result.returncode])
    return re.sub(r"^\[[^]]* @ 0x[0-9a-f]+\] ", "", detail[-1])


def skip_unusable_ffmpeg(reason):
    """Skip, or fail with EASYVMAF_REQUIRE_FFMPEG=1: a CI job must not pass by skipping."""
    if os.environ.get("EASYVMAF_REQUIRE_FFMPEG") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@functools.lru_cache(maxsize=None)
def libvmaf_v1_skip_reason():
    reason = probe_libvmaf_model(LIBVMAF_V1_PROBE_MODEL)
    return reason and "libvmaf v1 probe (%s) failed: %s" % (LIBVMAF_V1_PROBE_MODEL, reason)


@functools.lru_cache(maxsize=None)
def hdr_vmaf_skip_reason():
    reason = probe_libvmaf_model(HDR_VMAF_PROBE_MODEL, PQ_CHAIN)
    return reason and "HDR-VMAF probe (%s) failed: %s" % (HDR_VMAF_PROBE_MODEL, reason)


@functools.lru_cache(maxsize=None)
def cuda_skip_reason():
    """Why libvmaf_cuda cannot score one frame pair here, or None."""
    binary = ffmpeg.FFmpegQos._executable
    if not binary or not shutil.which(binary):
        return "FFmpeg unavailable"
    source = "color=black:s=320x240:r=1:d=1"
    result = subprocess.run([binary, "-hide_banner", "-nostdin", "-v", "error",
                             "-f", "lavfi", "-i", source, "-f", "lavfi", "-i", source,
                             "-lavfi", "[0:v]hwupload_cuda[main];[1:v]hwupload_cuda[ref];"
                                       "[main][ref]libvmaf_cuda",
                             "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    lines = result.stderr.strip().splitlines()
    return None if result.returncode == 0 else "libvmaf_cuda unusable: %s" % (
        lines[-1] if lines else "exit code %d" % result.returncode)


def pytest_runtest_setup(item):
    if item.get_closest_marker("requires_libvmaf_v1"):
        reason = libvmaf_v1_skip_reason()
        if reason:
            skip_unusable_ffmpeg(reason)
    if item.get_closest_marker("requires_hdr_vmaf"):
        reason = hdr_vmaf_skip_reason()
        if reason:
            # Only the HDR image must compute it: BtbN 8.1 and Homebrew skip
            if os.environ.get("EASYVMAF_REQUIRE_HDR") == "1":
                pytest.fail(reason, pytrace=False)
            pytest.skip(reason)
    if item.get_closest_marker("requires_cuda") and cuda_skip_reason():
        pytest.skip(cuda_skip_reason())


def write_scores(path, scores=SCORES):
    Path(path).write_text(json.dumps({"frames": [{"metrics": scores}]}))


def run_cli(*arguments, cwd, **environment):
    """Run `python -m easyvmaf` as a user would, against this checkout."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), **environment)
    return subprocess.run([sys.executable, "-m", "easyvmaf", *arguments], capture_output=True,
                          text=True, env=env, cwd=cwd, timeout=120)


@pytest.fixture
def unscored(monkeypatch):
    """FFmpeg is stubbed and writes no libvmaf log: Vmaf.compute() reads zero scores."""
    monkeypatch.setattr(vmaf, "read_scores",
                        lambda log_path, output_fmt, names: dict.fromkeys(names, 0.0))


@pytest.fixture
def ffmpeg_ok(monkeypatch):
    """The CLI startup check passes without running FFmpeg."""
    from easyvmaf import cli
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(CAPABILITIES))


@pytest.fixture(scope="session")
def ffmpeg_bin():
    """Real FFmpeg/FFprobe with CPU libvmaf, or skip (see skip_unusable_ffmpeg)."""
    binary, probe = ffmpeg.FFmpegQos._executable, ffmpeg.FFprobe._executable
    if not binary or not probe or not shutil.which(binary) or not shutil.which(probe):
        skip_unusable_ffmpeg("FFmpeg/FFprobe unavailable")
    filters = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True)
    if not any(line.split()[1:2] == ["libvmaf"] for line in filters.stdout.splitlines()):
        skip_unusable_ffmpeg("FFmpeg lacks libvmaf")
    return binary


@pytest.fixture(scope="session")
def encode(ffmpeg_bin):
    def run(*arguments):
        subprocess.run([ffmpeg_bin, "-v", "error", "-y", *map(str, arguments)], check=True,
                       capture_output=True, text=True, timeout=90)
    return run


def strict_loads(line):
    """json.loads that rejects NaN/Infinity, like any strict NDJSON consumer."""
    def reject(token):
        raise ValueError("non-JSON constant: " + token)
    return json.loads(line, parse_constant=reject)
