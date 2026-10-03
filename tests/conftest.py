"""Shared stubs and real-FFmpeg prerequisites."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from easyvmaf import ffmpeg

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = dict(meets_minimum=True, builtin_models=True, version_str="9.0", cuda_vmaf=False)
SCORES = {"vmaf_hd": 91.1234567, "vmaf_hd_neg": 90.0, "vmaf_hd_phone": 95.0}
STREAM = {"width": 320, "height": 180, "r_frame_rate": "10/1", "duration": "1.2", "start_time": "0"}


def write_scores(path, scores=SCORES):
    Path(path).write_text(json.dumps({"frames": [{"metrics": scores}]}))


def run_cli(*arguments, cwd, **environment):
    """Run `python -m easyvmaf` as a user would, against this checkout."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), **environment)
    return subprocess.run([sys.executable, "-m", "easyvmaf", *arguments], capture_output=True,
                          text=True, env=env, cwd=cwd, timeout=120)


@pytest.fixture
def ffmpeg_ok(monkeypatch):
    """The CLI startup check passes without running FFmpeg."""
    from easyvmaf import cli
    monkeypatch.setattr(cli, "check_ffmpeg", lambda: dict(CAPABILITIES))


@pytest.fixture(scope="session")
def ffmpeg_bin():
    """Real FFmpeg/FFprobe with CPU libvmaf, or skip."""
    binary, probe = ffmpeg.FFmpegQos._executable, ffmpeg.FFprobe._executable
    if not binary or not probe or not shutil.which(binary) or not shutil.which(probe):
        pytest.skip("FFmpeg/FFprobe unavailable")
    filters = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True)
    if not any(line.split()[1:2] == ["libvmaf"] for line in filters.stdout.splitlines()):
        pytest.skip("FFmpeg lacks libvmaf")
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
