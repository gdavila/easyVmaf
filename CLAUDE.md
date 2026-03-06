# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

easyVmaf is a Python CLI tool that wraps FFmpeg and FFprobe to compute VMAF video
quality scores. It handles the preprocessing that VMAF requires: deinterlacing,
scaling, frame rate normalization, and frame-accurate time synchronization between
reference and distorted video streams.

## Setup

```bash
pip install ffmpeg-progress-yield
```

FFmpeg >= 5.0 built with `--enable-libvmaf` must be on PATH, or override via env:

```bash
FFMPEG=/path/to/ffmpeg FFPROBE=/path/to/ffprobe python3 easyVmaf.py ...
```

## Running the Tool

```bash
python3 easyVmaf.py -d distorted.mp4 -r reference.mp4
python3 easyVmaf.py -d distorted.mp4 -r reference.mp4 -sw 2   # with sync
python3 easyVmaf.py -d "folder/*.mp4" -r reference.mp4        # batch
```

## Three-Layer Architecture

Each layer must only talk to the layer directly below it.

```
easyVmaf.py   ← Layer 3: CLI only (argparse, glob, print results)
Vmaf.py       ← Layer 2: VMAF logic (scaling, deinterlace, sync, scoring)
FFmpeg.py     ← Layer 1: FFmpeg/FFprobe subprocess wrappers
config.py     ← binary path resolution (ffmpeg, ffprobe via shutil.which)
```

### Layer 1 — FFmpeg.py
Thin subprocess wrappers around ffmpeg and ffprobe binaries.
- `FFprobe`: runs ffprobe, returns stream/frame/packet/format info as dicts
- `FFmpegQos`: builds and runs ffmpeg filter graph for PSNR and VMAF computation
- `inputFFmpeg`: manages per-input filter chains (scale, trim, fps, deinterlace)

Must NOT contain any VMAF business logic or user-facing print statements.

### Layer 2 — Vmaf.py
VMAF computation orchestration.
- `video`: parses stream metadata via FFprobe, detects interlacing
- `vmaf`: auto-scaling, auto-deinterlace, sync offset search, final VMAF scoring

Must NOT contain CLI argument parsing or result formatting.

### Layer 3 — easyVmaf.py
CLI entry point only. Argparse, glob pattern expansion for batch processing,
reading VMAF output files (json/xml/csv), printing final scores.

Must NOT contain FFmpeg filter logic or VMAF computation directly.

---

## FFprobe Call Map — Critical Reference

Understand this before touching Vmaf.py or FFmpeg.py.

| Data               | Method            | Consumers in Vmaf.py                          | Cost  |
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

---

## Key Behavioral Contracts

### Sync loop (syncOffset)
- Iterates over frame offsets in the sync window, running one PSNR computation per offset
- `ffmpegQos.invertSrcs()` swaps main/ref when `--reverse` is set
- After inversion, `invertSrcs()` must be called again to restore original order
- `clearFilters()` is called inside the loop on each iteration

### Filter application order (always this sequence)
1. `clearFilters()` — reset state
2. `_autoScale()` — scale to model resolution
3. `_autoDeinterlace()` OR `_forceFps()` — normalize frame rate
4. `setOffset()` — apply trim filters for sync

### Duration calculation
`getDuration()` tries `streamInfo['duration']` first. If KeyError (common with
MKV, some TS streams), falls back to `formatInfo['duration']`. Both subtract
`start_time` and round. Duration is used by `setOffset()` to compute trim length.

### VMAF models
- HD model: computes vmaf_hd, vmaf_hd_neg, vmaf_hd_phone — all three by default
- 4K model: computes vmaf_4k only
- Built-in models used (FFmpeg >= 5.0 required — models bundled in FFmpeg build)

### Output formats
VMAF results written to file: json (default), xml, csv.
File path: same directory as distorted input, same base name + `_vmaf.{ext}`

---

## Known Bugs (see tasks/ for fixes)

1. **XML output parsing crash** — `easyVmaf.py` accesses ElementTree elements as
   dicts. Will crash at runtime when `-output_fmt xml` is used.

2. **Score print outside loop** — Final score summary is outside the
   `for main in mainFiles` loop. Only last file's scores printed in batch mode.

3. **sys.exit(1) on success** — `--sync_only` exits with code 1 (error signal)
   instead of 0.

4. **subprocess shell=True** — All FFmpeg/FFprobe calls use `shell=True`. Brittle
   on filenames with spaces. Inconsistent with the `print_progress` branch which
   already uses `shell=False` via `shlex.split`. Note: the `\\\\:` escaping in
   filter strings is **not** a shell artifact — it is what FFmpeg's own filter
   graph parser requires to treat `:` as a literal within option values (the
   original `_commitFilters` used single-quote shell quoting, so FFmpeg always
   received `\\:` directly; this must be preserved under `shell=False` too).

5. **getFormatInfo() stores into wrong attribute** — `FFprobe.getFormatInfo()`
   (FFmpeg.py:97) assigns its result to `self.packetsInfo` instead of
   `self.formatInfo`. The return value is correct but the instance attribute is wrong.

6. **_forceFps() applies filter to main twice** — `vmaf._forceFps()` (Vmaf.py:292–293)
   calls `self.ffmpegQos.main.setFpsFilter(self.manual_fps)` twice instead of
   calling it once on main and once on ref.

---

## Coding Rules

- **subprocess**: use `shell=False` with argument lists. Never `shell=True`.
- **Filter strings**: no manual backslash escaping. Build clean strings, pass
  as list elements — subprocess handles quoting.
- **No silent failures**: if a deinterlace/fps combination is unsupported,
  raise a typed exception, do not print and continue.
- **No print() in Layer 1 or 2**: use `logging` module. print() belongs in
  Layer 3 (CLI) only.
- **Python >= 3.8**. Use `functools.cached_property` where appropriate.
- **Public method signatures in FFmpeg.py**: do not change without explicit
  instruction — external users may depend on them.

## What to Never Change Without Explicit Instruction

- The `-read_intervals %+5` flag in FFprobe getFramesInfo command
- The `getDuration()` fallback chain (streamInfo → formatInfo KeyError fallback)
- The `syncOffset()` PSNR-based sync algorithm logic
- The libvmaf filter parameter names: `n_subsample`, `n_threads`, `log_fmt`,
  `log_path`, `shortest`, `feature`
- The `\\\\:` separators in model strings and feature strings — these are required
  by FFmpeg's filter graph parser to treat `:` as a literal within option values
- The `invertSrcs()` / `invertedSrc` flag logic in sync handling
- Any public method name in FFmpeg.py

---

## Environment

- Linux / macOS only (current)
- Python >= 3.8
- FFmpeg >= 5.0 built with `--enable-libvmaf`
- Optional: FFmpeg >= 7.0 for in-loop decoding
- Optional: VMAF 3.0 + FFmpeg >= 6.1 for `libvmaf_cuda` GPU acceleration
- Dependency: `ffmpeg-progress-yield` (pip)
