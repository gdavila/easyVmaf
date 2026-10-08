# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

easyVmaf is a Python CLI tool that wraps FFmpeg and FFprobe to compute VMAF video
quality scores. It handles the preprocessing that VMAF requires: pixel format
normalization, deinterlacing, scaling, frame rate normalization, and
frame-accurate time synchronization between reference and distorted video streams.

Since 4.0 the default models are VMAF v1 (`vmaf_v1.0.16_*`); the VMAF v0.6 models
of 3.x are computed with `--vmaf-version 0.6`.

## Setup

```bash
pip install -e .          # from source (editable)
# or once published to PyPI:
pip install easyvmaf
```

FFmpeg >= 8.1 built with `--enable-libvmaf`, against libvmaf >= 3.2.1 built with
`-Dbuilt_in_models=true`, must be on PATH, or override via env:

```bash
FFMPEG=/path/to/ffmpeg FFPROBE=/path/to/ffprobe python3 -m easyvmaf ...
```

## Running the Tool

```bash
# Installed CLI command
easyvmaf -d distorted.mp4 -r reference.mp4                      # VMAF v1 HD (vmaf_v1_hd, vmaf_v1_phone)
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 2      # with sync window
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-offset 1.5    # manual sync offset
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-offset 1.5 --start-frame 9000 --frame-count 9000  # frame range
easyvmaf -d distorted.mp4 -r reference.mp4 --display 4k         # VMAF v1 4K
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6   # v0.6 models, as in 3.x
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 1 0.6 # both generations in one pass
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6 --gpu  # GPU (CUDA), v0.6 only
easyvmaf -d distorted.mp4 -r reference.mp4 --enable-hwaccel     # NVDEC decoding, any model, same scores
easyvmaf -d "folder/*.mp4" -r reference.mp4                     # batch

# Module invocation (no install)
python3 -m easyvmaf -d distorted.mp4 -r reference.mp4
```

Every run writes a JSON summary of its results next to the libvmaf log
(`<distorted>_vmaf_summary.json`); see Output formats.

Removed flags (3.x `-sw`, `-model`, `-json`, ...; `--reverse`, `--json`) are
rejected with exit code 2 and how to replace them (`_REMOVED_FLAGS` in `cli.py`).

Sync flags: `--sync-window` enables the search; `--sync-start` (window start) and
`--sync-reverse` (search direction) only configure it and require it;
`--sync-offset` is the signed manual offset (positive trims the reference,
negative the distorted), mutually exclusive with `--sync-window`.

## Docker

```bash
# CPU build
docker build -t easyvmaf .
docker run --rm -v /path/to/videos:/videos easyvmaf \
  -d /videos/distorted.mp4 -r /videos/reference.mp4

# GPU build (requires CUDA 12.3, nvidia-container-toolkit on host)
docker build -f Dockerfile.cuda -t easyvmaf:cuda .
docker run --rm --gpus all -v /path/to/videos:/videos easyvmaf:cuda \
  -d /videos/distorted.mp4 -r /videos/reference.mp4 --vmaf-version 0.6 --gpu
docker run --rm --gpus all -v /path/to/videos:/videos easyvmaf:cuda \
  -d /videos/distorted.mp4 -r /videos/reference.mp4 --vmaf-version 0.6 --gpu --enable-hwaccel

# docker-compose
docker compose build
docker compose run easyvmaf -d /videos/dist.mp4 -r /videos/ref.mp4
```

Running pytest inside the image (pytest is not installed in it):

```bash
docker run --rm -v $(pwd):/src -w /src --entrypoint sh easyvmaf \
  -c 'pip install -q --root-user-action=ignore pytest && python3 -m pytest -q -rs -p no:cacheprovider'
# GPU host: the same with the CUDA image, which also runs the requires_cuda tests
docker run --rm --gpus all -v $(pwd):/src -w /src --entrypoint sh easyvmaf:cuda \
  -c 'pip install -q --root-user-action=ignore pytest && python3 -m pytest -q -rs -p no:cacheprovider'
```

`Dockerfile.cuda` sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`:
NVDEC (`--enable-hwaccel`) needs the `video` capability, and without it every
input falls back to software decoding (`Cannot load libnvcuvid.so.1`). A CUDA
image built before that line (e.g. `easyvmaf-bench:5.0.0-e02f2b42ec79-cuda`)
needs `-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video` in `docker run`,
or `test_enable_hwaccel_never_changes_a_score` fails.

---

## Three-Layer Architecture

Each layer must only talk to the layer directly below it.

```
easyvmaf/cli.py     ← Layer 3: CLI only (argparse, glob, JSON output, print results)
easyvmaf/vmaf.py    ← Layer 2: VMAF logic (pixel format, scaling, deinterlace, sync,
easyvmaf/results.py ←          model resolution, scoring, reading libvmaf logs)
easyvmaf/ffmpeg.py  ← Layer 1: FFmpeg/FFprobe subprocess wrappers
easyvmaf/models.py  ← model catalog: pure data, imported by layers 1-3
easyvmaf/config.py  ← binary path resolution (ffmpeg, ffprobe via shutil.which)
```

Supporting entry points:
- `easyvmaf/__init__.py` — the public API, exactly its `__all__`: `Vmaf`,
  `VmafResult`, `SyncResult`, `validate_model_config`, `validate_range_config`,
  `UnsupportedModelConfigError`, `UnsupportedRangeError`,
  `UnsupportedFramerateError`, `FFmpegExecutionError`, `ModelSpec`, `ModelRun`,
  `CATALOG`, `select_models`, `check_ffmpeg`, `__version__`. Its names follow the
  CLI flags (`--sync-offset` → `sync_offset=`). The submodules (`easyvmaf.vmaf`,
  `easyvmaf.ffmpeg`, ...) are internal
- `easyvmaf/__main__.py` — enables `python3 -m easyvmaf`

### easyvmaf/models.py — model catalog
Pure data module: no subprocess, no logging, no import of `ffmpeg.py` or `vmaf.py`.
- `DISPLAY_RESOLUTION = {'hd': (1920, 1080), '4k': (3840, 2160)}`
- `VMAF_VERSIONS = ('1', '0.6')`, `VIEW_ALIASES = {'phone': '5h'}`
- `ModelSpec` (frozen): `name` (score key), `libvmaf_model` (built-in id, passed as
  `version=`), `vmaf_version`, `display`, `view`, `score_range`, `options`,
  `hfr_model`, `default`
- `ModelRun` (frozen): `spec`, `libvmaf_model` (`spec.libvmaf_model` or
  `spec.hfr_model`), `options` (`spec.options` + layer 2 overrides)
- `CATALOG`: the 8 `ModelSpec` entries (see VMAF models below)
- `select_models(display, vmaf_versions=('1',), views=None, hfr=False)`: filters by
  display, then each version in the order received; v1 `views=None` takes the
  `default` models, explicit views are case-insensitive, follow catalog order, and
  raise `ValueError` if a view does not exist for the display; v0.6 always returns
  the full set of its display; `hfr=True` switches v1 ids to `hfr_model`. Returns
  `ModelRun` without overrides.
- `model_names(runs)`: score names, in order

Add a model by adding a catalog entry, not by branching on display names.

### Layer 1 — easyvmaf/ffmpeg.py
Thin subprocess wrappers around ffmpeg and ffprobe binaries.
- `HwAccel` (frozen) and `HW_ACCELS` (only `cuda`): pure data for hardware
  decoding: `name` and `output_format` (`-hwaccel cuda
  -hwaccel_output_format cuda`), `convert` (`scale_cuda=format={pix_fmt}`, exact
  only without resizing), `frame_formats` (native software pix_fmt → decoded
  frame format: `yuv420p` → `nv12`, `yuv420p10le` → `p010`) and `download`
  (`hwdownload,format={frame_format}`). When a step may run on the device is
  decided by layer 2
- `FFprobe`: runs ffprobe, returns stream/frame/packet/format info as dicts
- `FFmpegQos`: builds and runs the ffmpeg filter graph for PSNR and VMAF
  - `getVmaf(models, log_path=None, subsample=1, output_fmt='json', threads=0,
    print_progress=False, shortest=False, features=None, gpu=False)`: `models` is a
    resolved `Sequence[ModelRun]`; `features` is the complete libvmaf `feature=`
    value built by layer 2. Returns the FFmpeg process; raises
    `FFmpegExecutionError` on failure. Sets `self.vmafpath`.
  - `_build_model_string(models)`: per model `version=…`, `name=…`, then
    `options` in order, joined by `\\\\:`; models joined by `|`
  - `getFirstFrameTimestamps()`: runs the current chains until each outputs a
    frame (`showinfo@main`/`showinfo@ref`, one null output each) and returns
    `((pts, time_base), (pts, time_base))`; used for the frame range anchors.
    `showinfo` runs with `checksum=0`: its checksum fails (EINVAL) on CUDA
    frames, and the pts is the same
  - `_commitInputs()` emits each input's `extraOptions` (hardware decode, then
    the seek) before its `-i`, and `-copyts -start_at_zero` when an input has a
    seek (hardware decode alone does not add it); without a seek the command is
    unchanged
- `inputFFmpeg`: per-input filter chains (scale, trim, fps, deinterlace,
  `setFormatFilter(pix_fmt)`, hwupload_cuda). Frame range helpers:
  `setSeek(seconds)` (input `-ss`, stored in `extraOptions`, cleared by
  `clearFilters()`), `setPreTrimFilter(start)` / `setEndTrimFilter(end)` (trims
  that keep timestamps), `setPtsShiftFilter(pts)` (integer ticks) and
  `setRangeTrimFilter(start, end)` (trim + `setpts=PTS-STARTPTS`).
  `extraOptions` is read-only: `hwDecodeOptions` + `seekOptions`, so a seek and
  hardware decode coexist in either call order
- Hardware decode helpers on `inputFFmpeg`: `setHwDecode(hwaccel)` (input
  options in `hwDecodeOptions`, cleared by `clearFilters()`; sets `hwFrames`:
  the chain ends on device frames) and `setHwDownloadFilter(pix_fmt)`
  (`hwdownload,format=<nv12|p010>,format=<pix_fmt>`, `pix_fmt` the native
  software format: the software decoder's frames; clears `hwFrames`).
  `_insertHwupload()` on an input with `hwFrames` appends the hwaccel's
  `scale_cuda=format=yuv420p` and the same `setparams`, without `format=` or
  `hwupload_cuda`; every other input is unchanged
- `probe_hw_decode(path, hwaccel)`: decodes one frame with the hwaccel's
  `-hwaccel`/`-hwaccel_output_format` through its `convert` filter, which only
  accepts device frames; `True` if the frame reached the GPU. Fails where FFmpeg
  would fall back to software (silently with `-hwaccel` alone): AV1 on Turing,
  H.264 High 10 / 4:2:2, ProRes, FFV1
- `check_ffmpeg()`: returns `version`, `version_str`, `meets_minimum` (FFmpeg >= 8.1;
  `n8.1…` tags and `N-…`/`git-…` dev builds accepted), `libvmaf_v1`, `cuda_vmaf`
  and `hwaccels` (the methods listed by `ffmpeg -hwaccels`: compiled in, not
  necessarily usable).
  `libvmaf_v1` comes from a probe that **computes one frame** with
  `vmaf_v1.0.16_3d0h` on two `color=black:s=320x240` inputs. It never reads the
  libvmaf version: 3.2.1 reports itself as 3.2.0, and a default 3.2.0 build loads
  the v1 models but cannot compute them. The probe must stay >= 320x240: the v1
  SpEED feature rejects frames below about 288x162.

Must NOT contain any VMAF business logic or user-facing print statements.

### Layer 2 — easyvmaf/vmaf.py and easyvmaf/results.py
VMAF computation orchestration.
- `video`: parses stream metadata via FFprobe (lazy loading), detects interlacing
- `Vmaf(distorted, reference, *, display='hd', vmaf_versions=('1',), views=None,
  hfr='auto', bitdepth='auto', enc_size=None, enc_bitdepth=None, model_options=(),
  output_format='json', loglevel='info', subsample=1, threads=0, progress=False,
  shortest=False, fps=0, cambi_heatmap=False, gpu=False, sync_offset=0.0,
  start_frame=None, frame_count=None, enable_hwaccel=None)`: pixel format,
  auto-scaling, auto-deinterlace, parallel sync offset search, model resolution,
  optional frame range, optional hardware decoding and final VMAF scoring.
  `sync(window, start=0, reverse=False)` searches the offset, stores it in
  `self.sync_offset` and returns `SyncResult(offset, psnr)`; `compute()` applies
  `sync_offset` and returns a `VmafResult`.
- `resolve_hwaccel(enable_hwaccel)`: `None`, or the `HW_ACCELS` name (`'auto'`
  → `'cuda'`, the only one); `ValueError` for an unknown value. Used by the
  constructor and by the CLI check against `check_ffmpeg()['hwaccels']`
- `validate_range_config(start_frame, frame_count, subsample, gpu,
  labels=None)`: run by the constructor and once by the CLI. Raises
  `UnsupportedRangeError(ValueError)` for a non-integer or negative start, a count
  below 1, or a range with `subsample > 1` (`gpu` is accepted). With a range,
  the constructor also rejects containers outside `_RANGE_FORMATS` (mp4/mov,
  matroska/webm) via `formatInfo['format_name']`.
- `validate_model_config(display, vmaf_versions, views, hfr, bitdepth, enc_size,
  enc_bitdepth, model_options, gpu, labels=None)`: the single source of model
  option validation. Run by the `Vmaf` constructor and once by the CLI before the
  batch (`labels` maps argument names to flag names in messages). Raises
  `UnsupportedModelConfigError` for: GPU with v1; GPU with bitdepth 10; `views`,
  `hfr='on'`, `enc_size`, `enc_bitdepth` or `model_options` without v1; a
  `model_options` entry not matching `_MODEL_OPTION_RE`. Unknown display, version or
  view raise `ValueError` from `select_models()`.
- The constructor also rejects, with v1 + `cambi_heatmap`, a heatmap path containing
  `:`, `|`, `\` or `'` (`_HEATMAP_PATH_FORBIDDEN`): it cannot be escaped inside a
  `model=` override. It runs `validate_range_config()` first, so the check sees the
  path actually written (with the range suffix).
- `UnsupportedFramerateError`: raised when no deinterlace filter covers the fps combination
- `UnsupportedModelConfigError(ValueError)`: options the selected models cannot honour
- `FeatureConfig`: dataclass for building the libvmaf `feature=` parameter string
- `results.py`: `SyncResult` NamedTuple (`offset`, `psnr`), `VmafResult` dataclass
  (`scores`, `models`, `display`, `pix_fmt`, `hfr`, `log_path`,
  `cambi_heatmap_path`, `sync_offset`, and for a range `start_frame`,
  `frame_count`, `frames_scored`; `gpu`, true when `libvmaf_cuda` computed it;
  `hwaccel`, `None` without `enable_hwaccel`, else `{'api': 'cuda', 'decode':
  {'distorted': 'hw'|'sw', 'reference': 'hw'|'sw'}}`) and
  `read_scores(log_path, output_fmt, names)`, which averages the per-frame values of
  each model name in a json, xml or csv libvmaf log. It reads only model names:
  feature columns differ between v0.6 and v1. `read_frames(log_path, output_fmt)`
  returns `(frameNum, metrics)` per frame; `trim_log(log_path, output_fmt, start,
  count, first_frame)` keeps `count` frames from `start`, renumbers them and
  recomputes the pooled metrics (libvmaf harmonic mean: `n / Σ 1/(x+1) − 1`), in
  place and in the same format. `heatmap_files(path)` lists the
  `cambi_heatmap_scale_*.gray` files of a heatmaps directory, sorted;
  `trim_heatmaps(path, start, count, frames)` keeps pictures `start..start+count-1`
  of each, in place, with picture size = file size / `frames` (frames measured),
  and raises `ValueError` when a file does not hold one picture per measured frame.

Reading libvmaf logs is a layer 2 responsibility since 4.0.

Must NOT contain CLI argument parsing or result formatting.

### Layer 3 — easyvmaf/cli.py
CLI entry point only. Argparse, glob pattern expansion for batch processing,
printing results and writing the JSON summary file.
- Flags are `--kebab-case`, grouped in `--help` as input, synchronization, frame
  range, models, VMAF v1 parameters, output and execution. Only `-d`/`-r` have short forms.
  `allow_abbrev=False`. `--enable-hwaccel [API]` (execution) takes `auto` (also
  without a value) or `cuda`, and is passed resolved as `enable_hwaccel=`.
- `_REMOVED_FLAGS`: removed flag → how to replace it. `MyParser.parse_known_args()`
  checks it **before** parsing (argparse would read `-reverse` as `-r everse`) and
  exits with code 2: `error: -sw was removed, use --sync-window`.
- `get_args()` calls `validate_model_config(..., labels=_FLAG_LABELS)` and
  `validate_range_config(..., labels=_FLAG_LABELS)` and turns their `ValueError`
  into `parser.error()` (exit code 2), before `check_ffmpeg()`. It also rejects
  `--sync-start`/`--sync-reverse` without `--sync-window` (naming the `--sync-offset`
  equivalent), `--sync-offset` with `--sync-window`, and a range with `--sync-only`.
- `_build_result()`: constructs the JSON schema 2 dict from a `VmafResult`
  (`JSON_SCHEMA_VERSION = 2`)
- `_summary_path()` / `_write_summary()`: every successful input writes its
  `_build_result()` dict to the summary file (see Output formats), serialized
  strictly (`allow_nan=False`) before the file is opened; logging goes to stderr
- `_print_text_result()`: one line per score with value, libvmaf model and range,
  plus the log and summary paths
- `check_ffmpeg()` called at startup: exits 1 if `meets_minimum` or `libvmaf_v1` is
  false, if `--gpu` is set and `cuda_vmaf` is false, or if the `--enable-hwaccel`
  backend is not in `hwaccels`

Must NOT contain FFmpeg filter logic, VMAF computation or libvmaf log parsing.

---

## FFprobe Call Map — Critical Reference

Understand this before touching easyvmaf/vmaf.py or easyvmaf/ffmpeg.py.

| Data               | Method            | Consumers in vmaf.py                          | Cost  |
|--------------------|-------------------|-----------------------------------------------|-------|
| `streamInfo`       | `getStreamInfo()` | `_applyScaleFilters` (width, height)          | low   |
|                    |                   | `_applyPixelFormat` / `_measurementPixFmt`    |       |
|                    |                   |   (pix_fmt of both inputs)                    |       |
|                    |                   | `_applyDeinterlaceFilters` (r_frame_rate)     |       |
|                    |                   | `_deinterlaceFrame/Field` (r_frame_rate)      |       |
|                    |                   | `sync` (r_frame_rate, width, height)          |       |
|                    |                   | `_resolveModels` (distorted width, height,    |       |
|                    |                   |   pix_fmt → `cambi.enc_*`)                    |       |
|                    |                   | `_build_feature_string` (v0.6-only CAMBI:     |       |
|                    |                   |   width, height of both inputs)               |       |
|                    |                   | `compute` (r_frame_rate, width, height logs)  |       |
|                    |                   | `getDuration` (primary: duration, start_time) |       |
| `formatInfo`       | `getFormatInfo()` | `getDuration` fallback (KeyError path)        | low   |
|                    |                   | `_checkRangeInputs` (format_name), range only |       |
| `framesInfo` /     | `getFramesInfo()` | `_applyDeinterlaceFilters` (final calculation | HIGH  |
|                    |                   |   and sync workers), via                      |       |
| `interlaced`       |                   |   `self.interlaced` only                      |       |
|                    |                   | `sync` probes once per input before           |       |
|                    |                   |   starting its worker pool                    |       |
|                    |                   | Skipped entirely when `fps != 0` (--fps)      |       |

`getFramesInfo()` uses `-read_intervals %+5` — it decodes 5 seconds of frames
per input to sample interlacing. This flag must never be changed.

All VMAF v1 parameters (measurement format, CAMBI encoding size and bit depth) come
from `streamInfo`, which is already fetched: v1 adds no FFprobe call.

**Lazy loading**: `interlaced` and `formatInfo` on the `video` class are lazy
properties — they trigger FFprobe only on first access and cache the result.
`streamInfo` is eager (fetched in `__init__`). The properties are not locked, so
`sync()` reads `interlaced` before creating workers; otherwise each worker
would run its own frames probe.

---

## Key Behavioral Contracts

### Sync loop (`sync()`)
- Runs PSNR at each frame offset in the sync window **in parallel** via `ThreadPoolExecutor`
- Each worker creates its own `FFmpegQos` instance with `gpu_mode=False` — sync is always CPU-only even when `--gpu` is set
- Each worker sets the private `FFmpegQos._single_thread` switch: FFmpeg runs single-threaded (`-threads 1` before each `-i`, global `-filter_complex_threads 1`), since the pool already runs one process per CPU. The final VMAF command keeps FFmpeg's default threading
- Workers build their chains with the same `_normalizeChains()` as the final
  calculation (deinterlace or `--fps`, then scale; no pixel format normalization)
  and trim **after** it, like `_applyOffset()`: the offset a worker scores selects the
  same frame or field in the final calculation. Trimming before deinterlacing made
  offsets one field apart tie on interlaced inputs
- Before filtering, the searched input (ref slot) gets `setPreTrimFilter(offset -
  SYNC_PREROLL)`: a `trim` that keeps timestamps, so the frames well before the
  offset are only decoded, not deinterlaced or scaled, and `fps`/`yadif` see the
  original timeline
- Filters are placed by role, not by slot: `_chains(qos)` returns the
  (distorted, reference) chains, swapped in reverse workers (`invertedSrc=True`)
- Reverse-search workers construct their own QoS instances with swapped paths and `invertedSrc=True`
- The shared `ffmpegQos` retains main=distorted, ref=reference and its `invertedSrc` state after every search; no restoration swap is needed
- Reverse search returns a negative offset so the final calculation trims distorted and names its output after distorted
- Each worker starts with fresh filter chains on its own QoS instance

### Filter application order (`Vmaf.compute()`, always this sequence)
1. `clearFilters()` — reset state (also resets `_hwupload_done`, the seek and
   the hardware decode)
2. `_applyHwDecode()` — only with `enable_hwaccel` (see Hardware decoding):
   `setHwDecode()` on each input the GPU decodes, and `setHwDownloadFilter(<native
   pix_fmt>)` as its **first** filter unless it stays on the GPU. The download
   gives the software decoder's frames in the native `pix_fmt`, so every later
   step, step 3's comparison included, is unchanged
3. `_applyPixelFormat()` — resolve the measurement `pix_fmt` and add
   `format=<pix_fmt>` as the **first** filter of each chain whose native format
   differs (after the download, if any; CPU only; in GPU mode `pix_fmt =
   'yuv420p'` and no filter is added)
4. `_normalizeChains()` — `_applyDeinterlaceFilters()` (yadif and/or fps), or the
   `--fps` filter on both chains, **then** `_applyScaleFilters()` to
   `DISPLAY_RESOLUTION[display]`; records the effective distorted frame rate in
   `self.output_fps`. Deinterlacing goes before scaling: `scale` treats the picture
   as progressive and would blend the two fields (−22 VMAF on a 720i input)
5. `_applyOffset()` — apply trim filters for `sync_offset`; with a frame range,
   `_applyRange()` instead (see Frame range)
6. `_resolveModels()` — HFR decision from `output_fps`, `select_models()`, v1 overrides
7. `ffmpegQos.getVmaf(models, ...)` — if `gpu=True`, auto-inserts `hwupload_cuda` on both chains as the last CPU→GPU step before `libvmaf_cuda` (on a chain still on the GPU, `scale_cuda=format=yuv420p` instead)

`sync()` runs before `compute()`, on its own worker chains, and only sets the
offset of step 5: `_applyOffset()` is private because `compute()` rebuilds every
chain.

### Hardware decoding (`enable_hwaccel`, `--enable-hwaccel`)
Rule: it never changes a score. Each input is decoded on the GPU when it can be,
and **stays on the GPU only while every step of its chain is bit-exact there**;
otherwise its frames are downloaded right after decoding and run today's CPU
chain. Decided per input, never from the other input:
- **Decode** (`_canHwDecode()`, once per input, cached in `_hwDecodable`): an
  allow-list from `streamInfo` (`_HW_DECODE_EXACT`, validated bit-exact on NVDEC:
  `h264` `yuv420p`, `hevc` `yuv420p`/`yuv420p10le`, `vp9` `yuv420p`), then
  `probe_hw_decode()`, since what decodes depends on the GPU (AV1 on Turing).
  Anything else (MPEG-2 decodes differently on NVDEC; AV1 and VP9 10-bit are not
  validated; ProRes, FFV1, H.264 High 10 / 4:2:2, `yuvj*`, ...) is decoded in
  software with a `logger.warning` naming the input and the reason, and
  reported as `"sw"`
- **Stay on the GPU** (`_staysOnDevice()`): only with `gpu`, and only an input
  in `yuv420p` (no bit depth reduction), at the display resolution (no scaling)
  and not deinterlaced (any interlaced input without `--fps` is downloaded).
  `fps`, `trim`, `setpts` and the range filters are exact on CUDA frames.
  Without `gpu`, libvmaf runs on the CPU and every input is downloaded
- **Not on the GPU**: `scale_cuda`/`scale_npp` scaling (−2.2 VMAF v1 720p →
  1080p), 10 → 8 bit conversion and `yadif_cuda` all differ from the CPU
- The sync workers never decode on the GPU. A frame range keeps the hardware
  decode options next to its seek (`extraOptions`), and its anchor probe runs on
  the same chains (`showinfo=checksum=0`)
- `VmafResult.hwaccel` / `vmaf.hwaccel`: `{'api': 'cuda', 'decode':
  {'distorted': 'hw'|'sw', 'reference': 'hw'|'sw'}}`, `None` (absent from the
  summary) without the flag

The format conversion goes first on purpose: with `scale` in the chain FFmpeg
negotiates the output format into the scaler, but without scaling and with `yadif`
FFmpeg inserts the conversion at the end and deinterlaces at 8 bits.

### Frame range (`start_frame` / `frame_count`)
Contract: a range returns exactly frames `start..start+count-1` of the full
calculation's log (frames numbered as libvmaf receives them, at `output_fps`),
with the same `frameNum` and identical metric values, so consecutive ranges join
into the full calculation; with `cambi_heatmap`, concatenating each heatmap file
of consecutive ranges, in range order, gives the full calculation's file byte for
byte with `threads=1`; with more threads libvmaf may zero part of the first
pictures (Netflix/vmaf#1676), so that case only warns. Pinned by
`test_frame_ranges_join_into_the_full_calculation`.
`_applyRange()` replaces `_applyOffset()` and keeps every other step of the chain
identical:
- **Anchor**: the only part of the full chain that depends on seeing its first
  frame is `setpts=PTS-STARTPTS` of the sync trim. A deep copy of `ffmpegQos`
  seeks to `RANGE_PREROLL` before each sync start, adds `setPreTrimFilter(start)`
  (only with an offset) and runs `getFirstFrameTimestamps()`: FFmpeg picks the
  first measured frame (with `fps`, trim rounds the offset to the converted grid:
  1.5 s at 25 fps starts at 1.52 s). Do not replace the probe with a computed value
- **Chain** per input: seek to `anchor + first / fps - RANGE_PREROLL` →
  normalized chain → `setPreTrimFilter(sync start)` → `setPtsShiftFilter(anchor
  pts)` → `setEndTrimFilter(sync duration)` → `setRangeTrimFilter(...)`. The sync
  trims only with an offset; `trim=end` reproduces `trim=duration` of the full
  calculation exactly once the timestamps are shifted to the anchor
- **Timestamps**: the seek implies `-copyts -start_at_zero`, so every frame keeps
  its full-calculation timestamp and `fps`/`yadif` pick the same frames
- **Context**: `RANGE_CONTEXT_FRAMES` (2) extra frames on each side, dropped by
  `trim_log()` and, from the CAMBI heatmaps, by `trim_heatmaps()`; motion features
  use the previous and the next frame (1 measured as enough); CAMBI is intra-frame
- **Bounds** a quarter frame (`_RANGE_BOUND_MARGIN`) before each frame: trim
  rounds to the nearest tick, so half a frame can round either way
- **Without an offset** both inputs must start within a quarter frame of each
  other (`UnsupportedRangeError`): unsynced, libvmaf pairs by timestamp
- **Guards** (`_trimRangeLog`): fewer frames than requested before the end of the
  inputs (two frames of duration tolerance), a range past the last frame and a
  heatmap file not holding one picture per measured frame raise
  `UnsupportedRangeError`. libvmaf writes no log when no frame reaches it, so
  `compute()` deletes an existing log at the range path before running (and the
  old heatmaps, as for every calculation: see Output formats)
- **Heatmap pictures**: one raw 16-bit file per scale (5), distorted picture n at
  n × picture size. The file name gives the scale size from the encoding size,
  not the picture size: v1 models set `cambi_high_res_speedup: 1080`, which halves
  every scale from a 1920x1080 encoding size (the v0.6-only CAMBI feature has no
  speedup). Never compute the picture size from the name
- Paths: `_rangeSuffix()` (`_f<start>-<last>`, `-end` without a count) names the
  log `<distorted>_vmaf_f<start>-<last>.<ext>` and the heatmap directory
  `<distorted>_cambi_heatmap_f<start>-<last>` (`VmafResult.cambi_heatmap_path`);
  the full calculation keeps `<distorted>_cambi_heatmap`
- The interlace decision still reads the first 5 s of each file, so every range
  takes the same decision as the full calculation

### Measurement pixel format (`_measurementPixFmt`)
- Chroma subsampling of the reference (`yuvj*`, `nv12`, `nv21` count as 4:2:0 8-bit,
  `p010` as 4:2:0 10-bit; an unrecognized format falls back to 4:2:0 with a warning)
- Bit depth: `bitdepth` override; else `max(10, reference depth)` with any v1 model;
  else the reference depth (v0.6 only)
- Never converts the reference to a lower format. Reported as `VmafResult.pix_fmt`

### Model resolution (`_resolveModels`)
- HFR: `hfr='auto'` → `output_fps >= HFR_MIN_FPS` (47); warning if
  `output_fps > HFR_MAX_CALIBRATED_FPS` (60). `VmafResult.hfr` is true only when a
  v1 model used its `_hfr` variant
- Each v1 `ModelRun` gets `cambi.enc_width`, `cambi.enc_height`
  (`enc_size` or the distorted `streamInfo` size) and `cambi.enc_bitdepth`
  (`enc_bitdepth` or the distorted `pix_fmt` digits, 8 without digits), then
  `model_options`, appended to `run.options` with `dataclasses.replace`
- CAMBI minimum encoding size: libvmaf 3.2.1 rejects < 180x150 or both sides < 216
  (`CAMBI_MIN_ENC_*`). `_cambiEncodingSize()` raises the size to the smallest accepted
  one with the same aspect ratio (256x144 → 267x150) and logs a warning; it applies
  to `enc_size` overrides and to the v0.6-only CAMBI feature too
- `cambi_heatmap`: with v1, `cambi.heatmaps_path` only on the first v1 run; with
  v0.6 only, a separate `cambi` feature in the `feature=` string (as in 3.x)
- Known libvmaf 3.2.1 bug (Netflix/vmaf#1676, README Known bugs): with
  `n_threads > 1` each thread's CAMBI context opens the heatmaps with
  `fopen("w")` and may zero part of the first pictures; scores are unaffected.
  Accepted: do not force one thread with `cambi_heatmap` (measured ~3.5x slower;
  reverted in 94b59bb)
- Never add `enable_transform` to a v1 model: v1 model JSONs already enable
  `score_transform`

### GPU filter pipeline
`--gpu` only supports v0.6 models: libvmaf 3.2.x has no CUDA extractors for the
v1 features (`cambi`, `speed_chroma`, `adm3`, `motion3`).
When `--gpu` is used, `FFmpegQos.getVmaf()` calls `_insertHwupload()` on both `main` and `ref`
inputs **after** all CPU filters have been appended:
```
[scale (CPU)] → [fps (CPU)] → [trim (CPU)] → [format=yuv420p] → [setparams=colorspace=unknown:range=unknown] → [hwupload_cuda] → [libvmaf_cuda]
```
`format=yuv420p` normalises the pixel format but does **not** strip color-space metadata.
`setparams=colorspace=unknown:range=unknown` resets the csp/range tags so both
inputs present identical properties to `libvmaf_cuda`. Without it, inputs tagged
`bt709/tv` cause `libvmaf_cuda` to auto-insert a CPU `auto_scale` for color
normalization, which crashes because `auto_scale` cannot accept CUDA frames.
`_insertHwupload()` is idempotent — the `_hwupload_done` flag prevents double insertion.
`clearFilters()` resets this flag so the sequence is repeatable.

With `--enable-hwaccel`, an input that stays on the GPU (see Hardware decoding)
never leaves it: NVDEC frames (`nv12`), the exact steps on CUDA frames, then
`_insertHwupload()`'s conversion instead of the upload:
```
[NVDEC] → [fps] → [trim] → [scale_cuda=format=yuv420p] → [setparams=colorspace=unknown:range=unknown] → [libvmaf_cuda]
```
A downloaded input runs the chain above unchanged after
`hwdownload,format=<nv12|p010>,format=<native pix_fmt>`. Without
`--enable-hwaccel` the `--gpu` command is unchanged (`GOLDEN_GPU`).

### Duration calculation
`getDuration()` tries `streamInfo['duration']` first. On KeyError (common with MKV,
some TS streams) falls back to `formatInfo['duration']`. Both values subtract
`start_time` and apply `math.floor` to millisecond precision.
Duration is used by `_applyOffset()` (via `_syncTrims()`) to compute trim length.

### VMAF models
`CATALOG` in `easyvmaf/models.py` (all built-in libvmaf models):

| `name`          | `libvmaf_model`                         | Version | Display | View    | Range    | Default |
|-----------------|-----------------------------------------|---------|---------|---------|----------|---------|
| `vmaf_v1_hd`    | `vmaf_v1.0.16_3d0h`                     | 1       | hd      | 3h      | [0, 100] | yes     |
| `vmaf_v1_phone` | `vmaf_v1.0.16_5d0h`                     | 1       | hd      | 5h      | [0, 100] | yes     |
| `vmaf_v1_4k`    | `vmaf_v1.0.16_1d5h_2160`                | 1       | 4k      | 1.5h    | [0, 100] | yes     |
| `vmaf_v1_4k_3h` | `vmaf_v1.0.16_3d0h_2160`                | 1       | 4k      | 3h      | [0, 110] | no      |
| `vmaf_hd`       | `vmaf_v0.6.1`                           | 0.6     | hd      | default | [0, 100] | yes     |
| `vmaf_hd_neg`   | `vmaf_v0.6.1neg`                        | 0.6     | hd      | neg     | [0, 100] | yes     |
| `vmaf_hd_phone` | `vmaf_v0.6.1` + `enable_transform=true` | 0.6     | hd      | phone   | [0, 100] | yes     |
| `vmaf_4k`       | `vmaf_4k_v0.6.1`                        | 0.6     | 4k      | default | [0, 100] | yes     |

- Every v1 model has `hfr_model = vmaf_v1.0.16_hfr_<suffix>` (e.g.
  `vmaf_v1.0.16_hfr_3d0h`); HFR changes the id, never the score name
- v1 score names always start with `vmaf_v1_`: v1 and v0.6 scores are not comparable
- Catalog order matters: the v0.6 HD order reproduces the 3.x `model=` string
- Built-in models only; external model JSONs (`path=`) are not supported

Example `model=` for the v1 HD defaults with a 1280x720 8-bit distorted input:
```text
version=vmaf_v1.0.16_3d0h\\:name=vmaf_v1_hd\\:cambi.enc_width=1280\\:cambi.enc_height=720\\:cambi.enc_bitdepth=8|version=vmaf_v1.0.16_5d0h\\:name=vmaf_v1_phone\\:cambi.enc_width=1280\\:cambi.enc_height=720\\:cambi.enc_bitdepth=8
```

### Feature string
`_build_feature_string()` in vmaf.py always includes PSNR; adds a separate CAMBI
feature only when `--cambi-heatmap` is passed and no v1 model is computed.
That PSNR only goes to the libvmaf log (the sync uses its own `psnr` filter). With
`--gpu` it is required: without a CPU feature, `libvmaf_cuda` 3.2.1 segfaults
whenever `n_threads` is set. Never remove it from the GPU command. Built via
`FeatureConfig` dataclass — add new features there, not by editing the string directly.

### Output formats
VMAF results written to file: json (default), xml, csv.
File path: same directory as distorted input, same base name + `_vmaf.{ext}`
(`_vmaf_f<start>-<last>.{ext}` for a frame range). CAMBI heatmaps:
`<distorted>_cambi_heatmap/` (`_cambi_heatmap_f<start>-<last>/` for a frame range).
`compute()` deletes the `cambi_heatmap_scale_*.gray` files of that directory
before every calculation with `cambi_heatmap`, range or not: libvmaf truncates
only the files named after the current encoding size, so a run at another size
(`enc_size`, v1 vs v0.6-only CAMBI) would leave its files next to the new ones

Summary file (always written, one per input, schema 2), named after the log:
`<log>_summary.json` (`<distorted>_vmaf_summary.json`,
`<distorted>_vmaf_f<start>-<last>_summary.json`); `--sync-only` has no log and
writes `<distorted>_sync_summary.json`:
```
{ schema_version: 2, distorted, reference, sync: { offset, psnr[, psnr_status] },
  vmaf: { display, pix_fmt, hfr, gpu, scores: { name: mean }, models: [ { name,
  libvmaf_model, vmaf_version, view, range } ], output_file[, cambi_heatmap_path]
  [, hwaccel: { api, decode: { distorted, reference } }]
  [, range: { start_frame, frame_count, frames_scored }] } }
```
`--sync-only` summaries have `schema_version` and no `vmaf` block. `vmaf.hwaccel`
is written only with `--enable-hwaccel`; `decode` is `"hw"` or `"sw"` per input.

---

## Coding Rules

- **subprocess**: `shell=False` with argument lists always. Never `shell=True`.
- **Filter strings**: `\\\\:` in Python source is required for the libvmaf model and feature
  parameter strings. In Python source `\\\\` becomes the two-character literal `\\`,
  which FFmpeg's filter graph parser requires to treat `:` as a literal inside option values.
  Do not change these separators.
- **No silent failures**: if a deinterlace/fps combination is unsupported, raise
  `UnsupportedFramerateError`; if the model options cannot be honoured, raise
  `UnsupportedModelConfigError`; if a frame range cannot reproduce the full
  calculation, raise `UnsupportedRangeError`. Do not print and continue.
- **No print() in Layer 1 or 2**: use `logging` module with `%s`-style format args.
  `print()` belongs in Layer 3 (CLI) only.
- **Logging destination**: `basicConfig(stream=sys.stderr)` — keeps the results on stdout separate from logs.
- **Python >= 3.10**. Built-in generics (`list[str]`), `X | None` and `match` are
  allowed; existing `typing.List`/`Tuple`/`Optional` code is not rewritten. Use
  `functools.cached_property` or lazy `@property` where appropriate.
- **Public API**: the names exported by `easyvmaf/__init__.py` and their
  signatures are the public API. Do not rename or change them without explicit
  instruction. The submodules are internal: `ffmpeg.py` names may change, but the
  never-change items about it below (separators, `_hwupload_done` guard,
  `-read_intervals`) still hold.
- **Versioning (SemVer from 5.0.0)**, as in README "Versioning". The contract:
  CLI flags and exit codes, output file names (log, summary, CAMBI heatmap
  directory), the summary JSON format (`schema_version`), score names and
  `easyvmaf.__all__`. Breaking it is a major release. A new default that changes
  scores is major; a bug fix that changes scores is minor, with a prominent
  CHANGELOG note saying which inputs change. Deprecate in a minor release (the
  old name keeps working and warns), remove in the next major. Every change to
  the contract gets a CHANGELOG entry under the next version. Before 5.0.0 there
  were no compatibility guarantees; until 5.0.0 is published, breaking changes
  still go into its `(unreleased)` CHANGELOG entry.
- **Test clips for VMAF v1**: frames passed straight to a v1 model need at least
  ~320x240 (`3d0h`), ~400x300 (`5d0h`) or ~576x324 (`3d0h_2160`), or SpEED fails.
  Through easyVmaf the inputs are always scaled to 1080p/2160p first.

## Tests

`.venv/bin/python -m pytest -q` from the repo root. Tests are consolidated by layer:

- `tests/test_models.py` — catalog selection
- `tests/test_ffmpeg.py` — layer 1, `check_ffmpeg()`, and the v0.6 and `--gpu`
  golden tests
- `tests/test_sync.py` — layer 2: sync, filter chains, pixel format, HFR, CAMBI
  overrides, `read_scores()`
- `tests/test_cli.py` — layer 3: arguments, removed flags, JSON schema 2, batch
- `tests/test_integration.py` — real FFmpeg runs
- `tests/conftest.py` — shared stubs (`STREAM`, `CAPABILITIES`), fixtures, and the
  `requires_libvmaf_v1` marker, which skips a test unless FFmpeg's libvmaf
  computes a v1 frame (`probe_libvmaf_model()`), and the `requires_cuda` marker,
  which skips a test unless `libvmaf_cuda` scores a frame (`cuda_skip_reason()`)

`EASYVMAF_REQUIRE_FFMPEG=1` turns those skips (FFmpeg/FFprobe unavailable, no
libvmaf, failed v1 probe) into failures; CI sets it so that an integration job
cannot pass by skipping. Other skips (Windows-only UNC, ...) are unchanged, and
so are the `requires_cuda` skips: CI has no GPU runner. The `gpu-*` cases of
`test_frame_ranges_join_into_the_full_calculation` run only on a GPU host
(`Dockerfile.cuda` image, Docker section); run them before changing the range
or the GPU pipeline. `test_enable_hwaccel_never_changes_a_score` (`requires_cuda`)
also needs NVDEC in the container (`NVIDIA_DRIVER_CAPABILITIES` with `video`,
set by the CUDA image) and encodes its clips with `h264_nvenc`; it fails,
rather than passing, if an input is decoded in software.

CI (`.github/workflows/test.yml`: pushes to master, pull requests, manual runs,
and `workflow_call`) runs the full suite in four jobs:
- `unit`: Python 3.10 to 3.14 on Ubuntu, with `FFMPEG`/`FFPROBE` pointing to
  nonexistent paths, so the integration tests skip
- `integration-docker`: inside the `Dockerfile` image, the minimum FFmpeg 8.1
  and libvmaf 3.2.1 (the pytest command of the Docker section). The build stage
  is cached with the GitHub Actions cache, written only by pushes to master
- `integration-btbn`: pip users on Linux x86_64, BtbN `n8.1-latest` FFmpeg and
  Python 3.10
- `integration-macos`: pip users on macOS, Homebrew FFmpeg and Python 3.12

The integration jobs set `EASYVMAF_REQUIRE_FFMPEG=1`.

Each test must protect against a real user-visible failure or a forbidden
change; no tests of implementation details, duplicated cases or unrealistic inputs.

## Releasing

`.github/workflows/release.yml` builds and publishes with PyPI Trusted Publishing
(no tokens). Its first job calls `test.yml` (`workflow_call`): nothing is built
or published unless every test job, integration included, passes on that commit.

1. Bump `__version__` in `easyvmaf/__init__.py` (the only place the version
   lives; `pyproject.toml` reads it) and rename the CHANGELOG `(unreleased)`
   heading to the release date.
2. Recommended: run the full test suite locally, with FFmpeg and libvmaf v1
   available. CI runs it again before publishing.
3. Push a tag `v<version>` equal to `__version__` → PyPI (environment `pypi`).
   A tag that differs from `__version__` fails the build before anything is
   published.
- `workflow_dispatch` publishes to TestPyPI (environment `testpypi`) as
  `<version>.dev<run number>`, so every run uploads a new version.
- A version on PyPI is immutable: it can never be uploaded again, even after
  deleting it. Never move or reuse a published tag; fix forward with a new
  version.

---

## Known Tech Debt

Deinterlacing in `_applyDeinterlaceFilters()` is wrong for these combinations,
which are documented as unsupported in the README (numbers refer to the
combination table of the 4.0 deinterlace review):

- **Case 5, both inputs interlaced** (1080i25 vs 1080i25): the "same interlacing"
  branch only normalizes fps, so VMAF scores woven frames.
- **Case 11, interlaced reference reported at its field rate vs progressive
  distorted at that rate** (25i reported as 50 vs 50p): `round(ref) == round(main)`
  picks `_deinterlaceFrame(1, ref)`; the reference ends at 25 frames/s against a
  50p distorted, so every reference frame is paired twice and half of the pairs
  are half a frame apart.
- **Case 13, progressive reference vs interlaced distorted reported at its field
  rate** (50p vs 25i reported as 50): `_deinterlaceFrame(1, main)` leaves the
  distorted at 25 frames/s, so only its first field is scored, while
  `output_fps` reports 50 and VMAF v1 picks the HFR models.

Root cause of 11 and 13: decisions use `r_frame_rate`, and an interlaced stream
reported at its field rate (H.264 PAFF) cannot be told from a 50 frames/s one.
A candidate fix, not verified on a real PAFF file, is to normalize an interlaced
stream to its frame rate first (e.g. use `avg_frame_rate` when `r_frame_rate` is
twice it). Fixed in 4.0, keep fixed: progressive reference at 2x an interlaced
distorted (`_deinterlaceField(2, main)`) and progressive reference vs interlaced
distorted reported at 2x (`_deinterlaceFrame(0.5, main)`), pinned by
`test_interlaced_distorted_scores_each_reference_frame_once`.

## What to Never Change Without Explicit Instruction

- The `-read_intervals %+5` flag in FFprobe `getFramesInfo` command
- The `getDuration()` fallback chain (streamInfo → formatInfo KeyError fallback)
- The `sync()` PSNR-based sync algorithm logic
- The libvmaf filter parameter names: `n_subsample`, `n_threads`, `log_fmt`,
  `log_path`, `shortest`, `feature`
- The `\\\\:` separators in `_build_model_string()` and feature strings — FFmpeg filter graph parser syntax
- The `invertSrcs()` / `invertedSrc` flag logic in sync handling
- The names and signatures of the public API (`easyvmaf.__all__`)
- The `_hwupload_done` guard in `_insertHwupload()` and the reset in `clearFilters()`
- The v0.6 FFmpeg command: `test_v06_vmaf_command_is_unchanged` in
  `tests/test_ffmpeg.py` (`GOLDEN_CHAINS`, `GOLDEN_CAMBI`) pins the full command for
  v0.6 HD/4K with and without CAMBI heatmap. Never edit its expected strings to
  make a change pass. Its only authorized change (4.0) put `fps` before `scale`,
  so deinterlacing runs before scaling; `fps` only picks frames, so the frames
  and the v0.6 scores are identical
- The `--gpu` FFmpeg command: `test_gpu_vmaf_command_is_unchanged` in
  `tests/test_ffmpeg.py` (`GOLDEN_GPU`) pins it, including `n_threads` and
  `feature=name=psnr`. Never edit its expected string to make a change pass
- Sync workers and the final calculation build their chains with the same
  `_normalizeChains()` and trim after it; a different order lets the sync offset
  select a different frame or field than the final calculation
- The v0.6 score names (`vmaf_hd`, `vmaf_hd_neg`, `vmaf_hd_phone`, `vmaf_4k`) and
  the `vmaf_v1_` prefix of v1 score names
- JSON schema 2 field names (`schema_version`, `vmaf.scores`, `vmaf.models`, ...);
  a breaking change needs a new `schema_version`
- Anything in the versioning contract (CLI flags, exit codes, output file
  names, summary format, score names, `easyvmaf.__all__`) except as the
  Versioning rule allows: a breaking change waits for a major release
- The `check_ffmpeg()` v1 probe computing a frame (not only loading the model) at
  >= 320x240
- The frame range contract: `test_frame_ranges_join_into_the_full_calculation`
  compares ranges with the full calculation exactly (logs, and CAMBI heatmaps byte
  for byte with one thread; a warning with two, Netflix/vmaf#1676); never loosen
  it to a tolerance or drop cases to make a change pass
- `--enable-hwaccel` never changes a score: only the steps validated bit-exact
  run on the GPU (Hardware decoding). `test_enable_hwaccel_never_changes_a_score`
  compares the logs exactly; never loosen it, and never move scaling,
  deinterlacing or a bit depth reduction to the GPU under this flag

---

## Environment

- Linux / macOS only (current)
- Python >= 3.10
- FFmpeg >= 8.1 built with `--enable-libvmaf`
- libvmaf >= 3.2.1 built with `-Dbuilt_in_models=true` (3.2.0 is not enough: its
  default build cannot compute the v1 `speed_chroma` feature). 3.2.1 reports its
  version as 3.2.0; only the frame probe tells them apart
- GPU VMAF (`--gpu`): FFmpeg built with `--enable-libvmaf --enable-ffnvcodec --enable-cuda-nvcc --enable-nonfree`, libvmaf 3.2.1 built with `-Denable_cuda=true`; CUDA 12.3+ with nvidia-container-toolkit on host. VMAF v0.6 only
- GPU decoding (`--enable-hwaccel`): FFmpeg with NVDEC (`--enable-ffnvcodec`;
  `ffmpeg -hwaccels` lists `cuda`) and `scale_cuda` (`--enable-cuda-nvcc`);
  any model. In Docker, the `video` driver capability (set by `Dockerfile.cuda`)
- Dependency: `ffmpeg-progress-yield >= 0.7.0` (pip)

### Docker image versions (pinned)
| Component  | Version |
|------------|---------|
| easyVmaf   | 5.0.0   |
| FFmpeg     | 8.1     |
| libvmaf    | 3.2.1   |
| dav1d      | 1.4.3   |
| nv-codec-headers (CUDA) | 13.0.19.1 |
| Python     | 3.12    |
| CUDA base  | 12.3.2  |
