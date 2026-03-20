# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

easyVmaf is a Python CLI tool that wraps FFmpeg and FFprobe to compute VMAF video
quality scores. It handles the preprocessing that VMAF requires: deinterlacing,
scaling, frame rate normalization, and frame-accurate time synchronization between
reference and distorted video streams.

## Setup

```bash
pip install -e .          # from source (editable)
# or once published to PyPI:
pip install easyvmaf
```

FFmpeg >= 5.0 built with `--enable-libvmaf` must be on PATH, or override via env:

```bash
FFMPEG=/path/to/ffmpeg FFPROBE=/path/to/ffprobe python3 -m easyvmaf ...
```

## Running the Tool

```bash
# Installed CLI command
easyvmaf -d distorted.mp4 -r reference.mp4
easyvmaf -d distorted.mp4 -r reference.mp4 -sw 2      # with sync window
easyvmaf -d distorted.mp4 -r reference.mp4 --gpu      # GPU-accelerated (CUDA)
easyvmaf -d distorted.mp4 -r reference.mp4 -json      # structured JSON output
easyvmaf -d "folder/*.mp4" -r reference.mp4           # batch

# Module invocation (no install)
python3 -m easyvmaf -d distorted.mp4 -r reference.mp4
```

## Docker

```bash
# CPU build
docker build -t easyvmaf .
docker run --rm -v $(pwd)/video_samples:/videos easyvmaf \
  -d /videos/distorted.mp4 -r /videos/reference.mp4

# GPU build (requires CUDA 12.3, nvidia-container-toolkit on host)
docker build -f Dockerfile.cuda -t easyvmaf:cuda .
docker run --rm --gpus all -v $(pwd)/video_samples:/videos easyvmaf:cuda \
  -d /videos/distorted.mp4 -r /videos/reference.mp4 --gpu

# docker-compose
docker compose build
docker compose run easyvmaf -d /videos/dist.mp4 -r /videos/ref.mp4
```

---

## Three-Layer Architecture

Each layer must only talk to the layer directly below it.

```
easyvmaf/cli.py     ← Layer 3: CLI only (argparse, glob, JSON output, print results)
easyvmaf/vmaf.py    ← Layer 2: VMAF logic (scaling, deinterlace, sync, scoring)
easyvmaf/ffmpeg.py  ← Layer 1: FFmpeg/FFprobe subprocess wrappers
easyvmaf/config.py  ← binary path resolution (ffmpeg, ffprobe via shutil.which)
```

Supporting entry points:
- `easyvmaf/__init__.py` — public API surface
- `easyvmaf/__main__.py` — enables `python3 -m easyvmaf`

### Layer 1 — easyvmaf/ffmpeg.py
Thin subprocess wrappers around ffmpeg and ffprobe binaries.
- `FFprobe`: runs ffprobe, returns stream/frame/packet/format info as dicts
- `FFmpegQos`: builds and runs ffmpeg filter graph for PSNR and VMAF computation
- `inputFFmpeg`: manages per-input filter chains (scale, trim, fps, deinterlace, hwupload_cuda)
- `check_ffmpeg()`: probes FFmpeg version, built-in model availability, and `libvmaf_cuda` support
- `VMAF_MODELS`: structured dict defining HD and 4K model configurations
- `_build_model_string()`: builds the libvmaf `model=` parameter string

Must NOT contain any VMAF business logic or user-facing print statements.

### Layer 2 — easyvmaf/vmaf.py
VMAF computation orchestration.
- `video`: parses stream metadata via FFprobe (lazy loading), detects interlacing
- `vmaf`: auto-scaling, auto-deinterlace, parallel sync offset search, final VMAF scoring
- `UnsupportedFramerateError`: raised when no deinterlace filter covers the fps combination
- `FeatureConfig`: dataclass for building the libvmaf `feature=` parameter string

Must NOT contain CLI argument parsing or result formatting.

### Layer 3 — easyvmaf/cli.py
CLI entry point only. Argparse, glob pattern expansion for batch processing,
reading VMAF output files (json/xml/csv), printing or emitting structured JSON results.
- `-json` flag: emits NDJSON to stdout (one object per file in batch); logging goes to stderr
- `_build_result()`: constructs the result dict for JSON output and human-readable display
- `check_ffmpeg()` called at startup for version/model/CUDA validation

Must NOT contain FFmpeg filter logic or VMAF computation directly.

---

## FFprobe Call Map — Critical Reference

Understand this before touching easyvmaf/vmaf.py or easyvmaf/ffmpeg.py.

| Data               | Method            | Consumers in vmaf.py                          | Cost  |
|--------------------|-------------------|-----------------------------------------------|-------|
| `streamInfo`       | `getStreamInfo()` | `_autoScale` (width, height)                  | low   |
|                    |                   | `_autoDeinterlace` (r_frame_rate)             |       |
|                    |                   | `_deinterlaceFrame/Field` (r_frame_rate)      |       |
|                    |                   | `syncOffset` (r_frame_rate, width, height)    |       |
|                    |                   | `getVmaf` (r_frame_rate, width, height,       |       |
|                    |                   |   cambi feature string)                       |       |
|                    |                   | `getDuration` (primary: duration, start_time) |       |
| `formatInfo`       | `getFormatInfo()` | `getDuration` fallback only (KeyError path)   | low   |
| `framesInfo` /     | `getFramesInfo()` | `_autoDeinterlace` via `self.interlaced` only | HIGH  |
| `interlaced`       |                   | Skipped entirely when `manual_fps != 0`       |       |
|                    |                   | Skipped entirely when `--sync_only` is used   |       |

`getFramesInfo()` uses `-read_intervals %+5` — it decodes 5 seconds of frames
per input to sample interlacing. This flag must never be changed.

**Lazy loading**: `interlaced` and `formatInfo` on the `video` class are lazy
properties — they trigger FFprobe only on first access and cache the result.
`streamInfo` is eager (fetched in `__init__`).

---

## Key Behavioral Contracts

### Sync loop (syncOffset)
- Runs PSNR at each frame offset in the sync window **in parallel** via `ThreadPoolExecutor`
- Each worker creates its own `FFmpegQos` instance with `gpu_mode=False` — sync is always CPU-only even when `--gpu` is set
- `ffmpegQos.invertSrcs()` swaps main/ref when `--reverse` is set
- After inversion, `invertSrcs()` must be called again to restore original order
- `clearFilters()` is called for each worker's own QoS instance

### Filter application order (always this sequence)
1. `clearFilters()` — reset state (also resets `_hwupload_done`)
2. `_autoScale()` — scale to model resolution (CPU `scale` filter; warns if called without preceding `clearFilters()`)
3. `_autoDeinterlace()` OR `_forceFps()` — normalize frame rate (mutually exclusive)
4. `setOffset()` — apply trim filters for sync
5. `getVmaf()` — if `gpu=True`, auto-inserts `hwupload_cuda` on both chains as the last CPU→GPU step before `libvmaf_cuda`

### GPU filter pipeline
When `--gpu` is used, `getVmaf()` calls `_insertHwupload()` on both `main` and `ref`
inputs **after** all CPU filters have been appended:
```
[scale (CPU)] → [fps (CPU)] → [trim (CPU)] → [hwupload_cuda] → [libvmaf_cuda]
```
`_insertHwupload()` is idempotent — the `_hwupload_done` flag prevents double insertion.
`clearFilters()` resets this flag so the sequence is repeatable.

### Duration calculation
`getDuration()` tries `streamInfo['duration']` first. On KeyError (common with MKV,
some TS streams) falls back to `formatInfo['duration']`. Both values subtract
`start_time` and apply `math.floor` to millisecond precision.
Duration is passed to `setOffset()` to compute trim length.

### VMAF models
```python
VMAF_MODELS = {
    'HD': [                                                       # default
        ('vmaf_v0.6.1',     'vmaf_hd',       {}),
        ('vmaf_v0.6.1neg',  'vmaf_hd_neg',   {}),
        ('vmaf_v0.6.1',     'vmaf_hd_phone', {'enable_transform': 'true'}),
    ],
    '4K': [
        ('vmaf_4k_v0.6.1',  'vmaf_4k',       {}),
    ],
}
```
- HD model: computes vmaf_hd, vmaf_hd_neg, vmaf_hd_phone in a single FFmpeg pass
- 4K model: computes vmaf_4k only
- Built-in models used (FFmpeg >= 5.0 required — models bundled in FFmpeg build)
- `_build_model_string()` produces the pipe/colon-separated `model=` parameter

### Feature string
`_build_feature_string()` in vmaf.py always includes PSNR; adds CAMBI only when
`--cambi_heatmap` is passed. Built via `FeatureConfig` dataclass — add new features
there, not by editing the string directly.

### Output formats
VMAF results written to file: json (default), xml, csv.
File path: same directory as distorted input, same base name + `_vmaf.{ext}`

JSON to stdout (`-json` flag): NDJSON, one object per file.
Schema: `{ distorted, reference, sync: { offset, psnr }, vmaf: { model, scores…, output_file } }`

---

## Coding Rules

- **subprocess**: `shell=False` with argument lists always. Never `shell=True`.
- **Filter strings**: `\\\\:` in Python source is required for the libvmaf model and feature
  parameter strings. In Python source `\\\\` becomes the two-character literal `\\`,
  which FFmpeg's filter graph parser requires to treat `:` as a literal inside option values.
  Do not change these separators.
- **No silent failures**: if a deinterlace/fps combination is unsupported, raise
  `UnsupportedFramerateError`. Do not print and continue.
- **No print() in Layer 1 or 2**: use `logging` module with `%s`-style format args.
  `print()` belongs in Layer 3 (CLI) only.
- **Logging destination**: `basicConfig(stream=sys.stderr)` — keeps stdout clean for `-json` output.
- **Python >= 3.8**. Use `functools.cached_property` or lazy `@property` where appropriate.
- **Public method signatures in ffmpeg.py**: do not change without explicit instruction.

## What to Never Change Without Explicit Instruction

- The `-read_intervals %+5` flag in FFprobe `getFramesInfo` command
- The `getDuration()` fallback chain (streamInfo → formatInfo KeyError fallback)
- The `syncOffset()` PSNR-based sync algorithm logic
- The libvmaf filter parameter names: `n_subsample`, `n_threads`, `log_fmt`,
  `log_path`, `shortest`, `feature`
- The `\\\\:` separators in `_build_model_string()` and feature strings — FFmpeg filter graph parser syntax
- The `invertSrcs()` / `invertedSrc` flag logic in sync handling
- Any public method name in `ffmpeg.py`
- The `_hwupload_done` guard in `_insertHwupload()` and the reset in `clearFilters()`

---

## Environment

- Linux / macOS only (current)
- Python >= 3.8
- FFmpeg >= 5.0 built with `--enable-libvmaf` (built-in models required)
- GPU: FFmpeg built with `--enable-libvmaf --enable-ffnvcodec --enable-cuda-nvcc --enable-nonfree`, libvmaf 3.0.0 built with `-Denable_cuda=true`; CUDA 12.3+ with nvidia-container-toolkit on host
- Dependency: `ffmpeg-progress-yield >= 0.7.0` (pip)

### Docker image versions (pinned)
| Component  | Version |
|------------|---------|
| FFmpeg     | 8.1     |
| libvmaf    | 3.0.0   |
| dav1d      | 1.4.3   |
| Python     | 3.12    |
| CUDA base  | 12.3.2  |
