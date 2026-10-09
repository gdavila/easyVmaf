# easyVmaf

Video Multi-Method Assessment Fusion (VMAF) is one of the most widely used
metrics to assess the quality of encoded video. It is a *full reference*
metric: it compares a *distorted* video (e.g., an encode) with its *reference*
frame by frame. However, computing it correctly is tricky. Both videos must
have the same resolution, frame rate, scan type and pixel format, and their
first frames must be aligned in time; otherwise, the score measures the
mismatch instead of the encoding.

easyVmaf is a Python tool, built on FFmpeg and FFprobe, that does this
preprocessing for you:

- Deinterlacing
- Upscaling and downscaling to the display resolution
- Frame-accurate synchronization
- Frame rate adaptation
- Pixel format normalization

Since 4.0, easyVmaf computes [VMAF v1](https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v1.md)
by default. The VMAF v0.6 models of easyVmaf 3.x are still available with
`--vmaf-version 0.6`. Upgrading from 3.x? See [Migrating from 3.x](#migrating-from-3x).

On GitHub, this README follows the `master` branch, which can document changes
not released yet (see the `unreleased` entry of the
[CHANGELOG](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md)). For
the version you installed (`pip show easyvmaf`), read its page on
[PyPI](https://pypi.org/project/easyvmaf/) or the README of its `v<version>` tag.

We explain how it works in [this article](https://gdavila.github.io/video/Vmaf/2020-03-05-Vmaf/),
written for an earlier version of easyVmaf.

## Contents

- [Requirements](#requirements)
  - [Why libvmaf 3.2.1](#why-libvmaf-321)
  - [Building FFmpeg from source](#building-ffmpeg-from-source)
  - [Verifying your build](#verifying-your-build)
  - [GPU requirements](#gpu-requirements)
- [Installation](#installation)
  - [easyVmaf](#easyvmaf-1)
  - [FFmpeg](#ffmpeg)
  - [From source (development)](#from-source-development)
- [Usage](#usage)
  - [Input](#input)
  - [Synchronization](#synchronization)
  - [Frame range](#frame-range)
  - [Models](#models)
  - [VMAF v1 parameters](#vmaf-v1-parameters)
  - [Output](#output)
  - [Execution](#execution)
  - [Validation](#validation)
- [Models](#models-1)
- [Automatic VMAF v1 parameters](#automatic-vmaf-v1-parameters)
  - [Pixel format](#pixel-format)
  - [CAMBI encoding resolution](#cambi-encoding-resolution)
  - [High frame rate](#high-frame-rate)
- [Interlaced sources](#interlaced-sources)
- [Frame ranges](#frame-ranges)
- [Examples](#examples)
  - [Basic VMAF (no sync)](#basic-vmaf-no-sync)
  - [Model selection](#model-selection)
  - [CAMBI encoding parameters](#cambi-encoding-parameters)
  - [With automatic sync](#with-automatic-sync)
  - [Sync measurement only](#sync-measurement-only)
  - [Batch processing](#batch-processing)
  - [GPU](#gpu)
- [Summary file](#summary-file)
- [Python API](#python-api)
- [Docker](#docker)
  - [Running with Docker](#running-with-docker)
  - [Building the images](#building-the-images)
  - [Build arguments](#build-arguments)
  - [Docker Compose](#docker-compose)
- [Sync examples explained](#sync-examples-explained)
  - [Reference delayed relative to distorted](#reference-delayed-relative-to-distorted)
  - [Distorted delayed relative to reference](#distorted-delayed-relative-to-reference)
- [Known bugs](#known-bugs)
- [Versioning](#versioning)
- [Migrating from 3.x](#migrating-from-3x)
  - [Flag equivalences](#flag-equivalences)

## Requirements

- Linux / macOS
- Python >= 3.10
- FFmpeg >= 8.1 built with `--enable-libvmaf`
- libvmaf >= 3.2.1 built with `-Dbuilt_in_models=true`
- Python package: [`ffmpeg-progress-yield`](https://github.com/slhck/ffmpeg-progress-yield)

easyVmaf checks FFmpeg and libvmaf at startup. It exits with an error if FFmpeg
is older than 8.1, or if its libvmaf cannot compute a frame with the VMAF v1
model `vmaf_v1.0.16_3d0h`.

### Why libvmaf 3.2.1

The VMAF v1 models ship with [libvmaf 3.2.0](https://github.com/Netflix/vmaf/releases/tag/v3.2.0),
but 3.2.0 is not enough:

- In 3.2.0 the `speed_chroma` feature extractor, used by every v1 model, is only
  compiled with `-Denable_float=true`, which is off by default. A default 3.2.0
  build loads the v1 models but fails to compute them
  (`problem during vmaf_use_features_from_model`). Fixed in
  [3.2.1](https://github.com/Netflix/vmaf/releases/tag/v3.2.1) by [PR #1558](https://github.com/Netflix/vmaf/pull/1558).
- 3.2.0 can grow memory without bound when decoding is faster than feature
  extraction ([issue #1587](https://github.com/Netflix/vmaf/issues/1587)). v1 is
  slower to extract than v0.6, so the risk is higher. Fixed in 3.2.1.
- 3.2.1 also fixes wrong CUDA scores with inputs above 8 bits. `--gpu` is not
  affected: it computes VMAF on the GPU only at 8 bits.

libvmaf 3.2.1 still reports itself as **3.2.0**: `pkg-config --modversion libvmaf`
and the `version` field of the libvmaf log both say `3.2.0`, because the upstream
`v3.2.1` tag kept that version string. No version check can tell the two apart.
Hence, in practice, the only reliable test is to compute a frame with a v1 model, which is what
easyVmaf does at startup (see [Verifying your build](#verifying-your-build)).

### Building FFmpeg from source

Packaged builds that pass the check are listed in [Installation](#ffmpeg).
Otherwise, build libvmaf 3.2.1 with meson, then build FFmpeg >= 8.1 against it:

```bash
curl -LO https://github.com/Netflix/vmaf/archive/v3.2.1.tar.gz
tar -xzf v3.2.1.tar.gz
cd vmaf-3.2.1/libvmaf
meson setup build --buildtype release -Dbuilt_in_models=true
ninja -C build
sudo ninja -C build install

# then, in the FFmpeg >= 8.1 source tree
./configure --enable-libvmaf --enable-version3 && make && sudo make install
```

`-Denable_float=true` is not needed with 3.2.1.

### Verifying your build

This command computes one frame with a VMAF v1 model. It is the same probe
easyVmaf runs at startup:

```bash
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i color=black:s=320x240:r=1:d=1 \
  -f lavfi -i color=black:s=320x240:r=1:d=1 \
  -lavfi libvmaf=model=version=vmaf_v1.0.16_3d0h:log_fmt=json:log_path=/dev/null \
  -f null -
```

The build is valid when the command exits with code 0 and prints nothing. The
probe frames are 320x240 because the Spatial Efficient Entropic Differencing
(SpEED) feature of v1 rejects frames smaller
than about 288x162. That limit does not affect normal use: easyVmaf always scales
the inputs to 1920x1080 or 3840x2160 before libvmaf.

### GPU requirements

For VMAF on the GPU (`--gpu` with VMAF v0.6 at 8 bits, see [GPU](#gpu)):

- NVIDIA GPU with CUDA support
- FFmpeg built with `--enable-ffnvcodec --enable-libvmaf`
- libvmaf built with `-Denable_cuda=true`
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (for Docker GPU usage)
- For the CUDA image: NVIDIA driver >= 570 on the host. Its libvmaf is built
  with CUDA 12.8, whose PTX (ISA 8.7) needs that driver

For GPU decoding (`--gpu` with any model, see
[Hardware decoding](#hardware-decoding)):

- NVIDIA GPU with NVDEC, its hardware video decoder
- FFmpeg built with `--enable-ffnvcodec --enable-cuda-llvm`. To check it,
  `ffmpeg -hwaccels` must list `cuda` and `ffmpeg -filters` must list
  `scale_cuda`; otherwise every input is decoded on the CPU.
  `--enable-cuda-nvcc` also works, but it requires `--enable-nonfree`, which
  makes the FFmpeg build not redistributable
- libvmaf does not need CUDA for GPU decoding, only for VMAF on the GPU
- In Docker, the `video` driver capability
  (`NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`), which the CUDA image sets

## Installation

easyVmaf needs Python >= 3.10 and an FFmpeg build that passes the startup
check. Install both.

### easyVmaf

```bash
pipx install easyvmaf
# or
uv tool install easyvmaf
```

Both install the `easyvmaf` command in its own environment and put it on `PATH`,
so it runs from any directory. A plain `pip install easyvmaf` outside a virtual
environment fails with `externally-managed-environment` (PEP 668) on Homebrew
Python and on Debian 12 / Ubuntu 23.04 and later.

To use the [Python API](#python-api), install the package in a virtual
environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install easyvmaf
```

### FFmpeg

easyVmaf needs FFmpeg >= 8.1 with libvmaf >= 3.2.1 and its built-in models (see
[Requirements](#requirements)). After installing it, run the probe in
[Verifying your build](#verifying-your-build).

**macOS.** Homebrew's FFmpeg is built against libvmaf 3.2.1:

```bash
brew update && brew install ffmpeg
# already installed: brew update && brew upgrade libvmaf ffmpeg
```

`brew update` matters when Homebrew does not update itself, e.g. with
`HOMEBREW_NO_AUTO_UPDATE` set: an old Homebrew index installs libvmaf 3.2.0,
which cannot compute VMAF v1.

**Linux.** Distribution packages are older than 8.1 (Ubuntu 24.04 ships FFmpeg
6.1). Use a static build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases),
which includes libvmaf with its built-in models:

```bash
# x86_64; on arm64 use ffmpeg-n8.1-latest-linuxarm64-gpl-8.1
curl -LO https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
tar -xf ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
export PATH="$PWD/ffmpeg-n8.1-latest-linux64-gpl-8.1/bin:$PATH"
```

**Docker.** The [Docker](#docker) images bundle easyVmaf, FFmpeg 8.1 and
libvmaf 3.2.1 (`linux/amd64`). They are published on the GitHub Container
Registry (GHCR) from the 5.0.0
release on (`ghcr.io/gdavila/easyvmaf:5.0.0`); before that, clone the repository
and build them with `docker build -t easyvmaf .`.

**Other platforms or builds.** See [Building FFmpeg from source](#building-ffmpeg-from-source).

easyVmaf runs the `ffmpeg` and `ffprobe` found on `PATH`, or the ones given in
environment variables:

```bash
FFMPEG=/path/to/ffmpeg FFPROBE=/path/to/ffprobe easyvmaf ...
```

### From source (development)

```bash
git clone https://github.com/gdavila/easyVmaf.git
cd easyVmaf
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"     # or: uv venv && uv pip install -e ".[dev]"
python -m pytest -q
```

The integration tests run the FFmpeg on `PATH`; they are skipped when it has no
libvmaf, and the VMAF v1 ones when its libvmaf cannot compute a v1 frame.

## Usage

```
easyvmaf -d <distorted> -r <reference> [options]
```

`easyvmaf --help` groups the options in the same sections as the tables below.

### Input

| Flag | Default | Description |
|------|---------|-------------|
| `-d`, `--distorted` | required | Distorted video path. Accepts a glob pattern for batch processing. |
| `-r`, `--reference` | required | Reference video path. |
| `--fps FPS` | `0` | Force frame rate conversion. Disables auto-deinterlace when set. |

### Synchronization

| Flag | Default | Description |
|------|---------|-------------|
| `--sync-window SW` | `0` | Sync window size in seconds. Enables automatic sync search: the first frames of the distorted video are looked for inside this window of the reference (of the distorted video with `--sync-reverse`). `0` disables sync. |
| `--sync-start SS` | `0` | Time into the reference (into the distorted video with `--sync-reverse`) where the sync window begins. Requires `--sync-window`. |
| `--sync-reverse` | off | Reverse the sync search: match the first frames of the reference against the distorted video. Requires `--sync-window`. |
| `--sync-only` | off | Measure the sync offset for every input and skip VMAF. Requires an explicit, finite `--sync-window` greater than zero. |
| `--sync-offset S` | `0` | Manual sync offset in seconds, instead of a sync search: positive trims the reference, negative trims the distorted video. Same sign as the reported offset. Cannot be combined with `--sync-window`. |
| `--shortest` | off | Stop when the shorter video ends instead of repeating its last frame. Use it when the inputs have different durations. |

### Frame range

| Flag | Default | Description |
|------|---------|-------------|
| `--start-frame N` | `0` | First frame to measure, numbered as in the log of the full calculation. See [Frame ranges](#frame-ranges). |
| `--frame-count N` | to the end | Number of frames to measure. The last range of a video can have fewer. |

### Models

| Flag | Default | Description |
|------|---------|-------------|
| `--display {hd,4k}` | `hd` | Target display. Inputs are scaled to 1920x1080 (`hd`) or 3840x2160 (`4k`). |
| `--vmaf-version {1,0.6} [...]` | `1` | VMAF generations to compute. `--vmaf-version 1 0.6` computes both in one pass. |
| `--bitdepth {auto,8,10}` | `auto` | Measurement bit depth. `auto`: 10 with any v1 model, otherwise the reference bit depth. See [Pixel format](#pixel-format). |

### VMAF v1 parameters

These flags apply only to VMAF v1 models. Using them without `1` in
`--vmaf-version` is an error, except `--hfr auto` and `--hfr off`.

| Flag | Default | Description |
|------|---------|-------------|
| `--view {3h,5h,phone,1.5h} [...]` | hd: `3h 5h`; 4k: `1.5h` | Viewing distance, as a multiple of the screen height (`3h`: the viewer sits 3 screen heights away). The farther the viewer, the fewer artifacts are visible and the higher the score. `phone` is another name for `5h`. With `--display 4k`, `3h` uses a model whose scores go from 0 to 110 instead of 0 to 100. |
| `--hfr {auto,on,off}` | `auto` | High frame rate models. `auto`: when the effective frame rate is >= 47 fps. See [High frame rate](#high-frame-rate). |
| `--enc-size WxH` | distorted video size | Encoding resolution passed to CAMBI, the v1 banding feature. |
| `--enc-bitdepth {8,10,12}` | from the distorted `pix_fmt` | Encoding bit depth passed to CAMBI. |
| `--model-option FEATURE.OPTION=VALUE` | — | Advanced override of a v1 model option, e.g. `cambi.topk=0.5`. Repeatable. |

### Output

| Flag | Default | Description |
|------|---------|-------------|
| `--output-format {json,xml,csv}` | `json` | Format of the per-frame libvmaf log. The [summary file](#summary-file) is always JSON. |
| `--cambi-heatmap` | off | Compute and save CAMBI banding heatmaps. |
| `--verbose` | off | Enable verbose log level. |
| `--progress` | off | Show FFmpeg progress during the VMAF computation. |

Every run writes two files next to the distorted video: the per-frame libvmaf
log (`<distorted>_vmaf.json`) and a [summary file](#summary-file) with the
final results (`<distorted>_vmaf_summary.json`).

### Execution

| Flag | Default | Description |
|------|---------|-------------|
| `--threads N` | `0` | Parallel single-threaded sync workers and libvmaf threads (0 = CPU count). |
| `--subsample N` | `1` | Frame subsampling factor to speed up the computation. |
| `--gpu` | off | Use the NVIDIA GPU where it can: decode on the GPU (NVDEC) each input it decodes exactly, never changing a score, and compute VMAF with `libvmaf_cuda` when the models allow it (`--vmaf-version 0.6`, 8 bits), otherwise on the CPU. Requires a CUDA build of FFmpeg (such as the CUDA [Docker](#docker) image). See [GPU](#gpu). |
| `--disable-hw-decode` | off | With `--gpu`: decode every input on the CPU. |
| `--disable-vmaf-cuda` | off | With `--gpu`: compute VMAF on the CPU (`libvmaf`). |

### Validation

Invalid arguments are usage errors (exit code 2), reported on stderr before
checking FFmpeg or probing videos. They are never converted to valid values or
replaced with defaults.

- `--sync-window`, `--sync-start` and `--fps` accept finite numbers greater than
  or equal to zero. Zero keeps the defaults: no sync search, a sync window that
  starts at the beginning, and automatic frame rate handling. `--sync-offset`
  accepts any finite number.
- `--sync-start` and `--sync-reverse` only configure the sync search: without
  `--sync-window` they are an error that names the `--sync-offset` equivalent,
  e.g. `--sync-start 1.5 --sync-reverse` → `use --sync-offset -1.5`.
  `--sync-offset` and `--sync-window` are mutually exclusive.
- `--subsample` accepts integers of at least 1; `--threads` accepts integers of
  at least 0.
- Option values are case-insensitive (`--display 4K`, `--output-format XML`,
  `--model-option CAMBI.TOPK=0.5` work). Flag names are not: write them in
  lowercase.
- `--view`, `--hfr on`, `--enc-size`, `--enc-bitdepth` and `--model-option`
  require a VMAF v1 model in `--vmaf-version`.
- A view that does not exist for the display is an error, e.g. `--view 5h`
  with `--display 4k`.
- `--disable-hw-decode` and `--disable-vmaf-cuda` require `--gpu`.
- `--model-option` must match `feature.option=value`, with lowercase names and a
  value made of letters, digits, `_`, `.` and `-`. Nothing else can reach the
  FFmpeg filter graph.
- A sync window that ends after the video it searches (`--sync-start` +
  `--sync-window` longer than the reference, or than the distorted video with
  `--sync-reverse`) is an error (exit code 1), reported before running FFmpeg.
- `--start-frame` accepts integers of at least 0 and `--frame-count` integers of
  at least 1. A frame range cannot be combined with `--sync-only`, and, for now,
  not with `--subsample`.
- Removed flags are rejected with the name of their replacement, e.g.
  `error: -sw was removed, use --sync-window` or
  `error: --reverse was removed, use --sync-reverse`.

## Models

| Score name | libvmaf model | Version | Display | View | Range | Default |
|---|---|---|---|---|---|---|
| `vmaf_v1_hd` | `vmaf_v1.0.16_3d0h` | 1 | hd | 3h | [0, 100] | yes |
| `vmaf_v1_phone` | `vmaf_v1.0.16_5d0h` | 1 | hd | 5h | [0, 100] | yes |
| `vmaf_v1_4k` | `vmaf_v1.0.16_1d5h_2160` | 1 | 4k | 1.5h | [0, 100] | yes |
| `vmaf_v1_4k_3h` | `vmaf_v1.0.16_3d0h_2160` | 1 | 4k | 3h | [0, 110] | no (`--view 3h`) |
| `vmaf_hd` | `vmaf_v0.6.1` | 0.6 | hd | — | [0, 100] | yes |
| `vmaf_hd_neg` | `vmaf_v0.6.1neg` | 0.6 | hd | — | [0, 100] | yes |
| `vmaf_hd_phone` | `vmaf_v0.6.1` + `enable_transform=true` | 0.6 | hd | — | [0, 100] | yes |
| `vmaf_4k` | `vmaf_4k_v0.6.1` | 0.6 | 4k | — | [0, 100] | yes |

All models are libvmaf built-in models. Each v1 model also has a high frame rate
(HFR) variant with `_hfr_` in its id (e.g. `vmaf_v1.0.16_hfr_3d0h`). The HFR variant
keeps the score name; only `libvmaf_model` changes.

VMAF v1 differs from v0.6 in ways that change the scores:

- v1 uses the `cambi` (Contrast Aware Multiscale Banding Index), `speed_chroma_uv`,
  `adm3` and `motion3` features. VIF is gone. v1 measures chroma and banding; v0.6 measures luma only.
- The v1 phone model is a separate 5H model, not the score transform of v0.6.
  The v1 models already enable their own score transform.
- The 4K model at 3H (`vmaf_v1_4k_3h`) ranges from 0 to 110.

> **v1 and v0.6 scores are not comparable.** A v1 score of 85 is not a v0.6 score
> of 85. That is why every v1 score name starts with `vmaf_v1_` and never reuses
> `vmaf_hd` or `vmaf_4k`. To recalibrate quality thresholds, compute both
> generations in one pass with `--vmaf-version 1 0.6`.

## Automatic VMAF v1 parameters

easyVmaf resolves the v1 parameters from the stream information it already
probes, and from the effective frame rate after deinterlacing or `--fps`.

| Parameter | Automatic value | Override |
|---|---|---|
| Measurement pixel format | Chroma subsampling of the reference; 10 bits with any v1 model | `--bitdepth` |
| CAMBI encoding resolution | Width and height of the distorted video | `--enc-size WxH` |
| CAMBI encoding bit depth | From the distorted `pix_fmt` (`yuv420p` → 8, `yuv420p10le` → 10; no digits → 8) | `--enc-bitdepth` |
| HFR models | Effective frame rate >= 47 fps | `--hfr on/off` |
| Other model options | — | `--model-option` |

### Pixel format

libvmaf needs the same pixel format on both inputs. Without an explicit format,
FFmpeg picks one and may convert the reference down, for example from 4:2:2 to
4:2:0, or from 10 to 8 bits. v1 measures chroma, so that conversion would erase
exactly the chroma loss v1 is meant to measure.

easyVmaf measures both inputs in one format. We prefer to leave the reference
unmodified, so the measurement format is never lower than the reference's:

- Chroma subsampling: the reference's (4:2:0, 4:2:2 or 4:4:4). An unrecognized
  reference format is measured as 4:2:0, with a warning in the log.
- Bit depth: 10 when any v1 model is computed (or the reference bit depth, if
  higher); the reference bit depth with only v0.6 models; or `--bitdepth`.
  Netflix recommends measuring v1 at 10 bits for SDR content, even when the
  encode is 8 bits, to capture banding.

Each input whose native format differs gets a `format=` filter as the first
filter of its chain, before scaling and deinterlacing. For example, a `yuv422p`
reference with v1 is measured in `yuv422p10le`; a `yuv420p` reference with only
v0.6 is measured in `yuv420p`, with no filter if the distorted video is already
`yuv420p`. The format used is reported as `pix_fmt` in the results. Netflix does
not document whether the v1 models are calibrated for 4:2:2 or 4:4:4, so
`pix_fmt` lets you identify those measurements later.

### CAMBI encoding resolution

easyVmaf always scales the distorted video to the display resolution. Without
the encoding resolution, CAMBI would measure banding on the scaled picture.
easyVmaf passes the distorted video size and bit depth as `cambi.enc_width`,
`cambi.enc_height` and `cambi.enc_bitdepth` to every v1 model.

libvmaf 3.2.1 CAMBI rejects encoding sizes below 180x150, or with both sides
below 216, and the whole v1 calculation fails. Low rungs of an adaptive bitrate
(ABR) ladder, such as 256x144,
192x108 or 160x90 fall below that limit. easyVmaf raises the size to the smallest
accepted one with the same aspect ratio and logs a warning:

```
CAMBI does not accept a 256x144 encoding size; using 267x150, the smallest accepted size with the same aspect ratio
```

The same adjustment applies to `--enc-size`.

### High frame rate

The HFR variants of the v1 models use a five-frame motion window and are
calibrated for ~50/60 fps. With `--hfr auto`, easyVmaf uses them when the
effective frame rate of the distorted video is at least 47 fps. The effective
frame rate is the distorted frame rate after deinterlacing and `--fps`, not the
container value. Above 60 fps, easyVmaf still uses the HFR
variants and logs a warning, since that is outside their calibration.

## Interlaced sources

easyVmaf detects interlacing by sampling the first 5 seconds of each input and
deinterlaces with `yadif` when only one of them is interlaced. Deinterlacing
runs before scaling, so the two fields of an SD or 720i picture are never
blended, and the sync search aligns on single fields.

In the table, `1080i25` is an interlaced video with 25 frames (50 fields) per
second; the 29.97/59.94 family behaves the same (`1080i29.97` vs `59.94p`).
`1080i25*` is the same video when ffprobe reports it at its field rate
(`r_frame_rate` of 50, typical of H.264 Picture-Adaptive Frame-Field (PAFF)
broadcast streams).

| Reference | Distorted | Typical case | What easyVmaf does | Compared at | Supported |
|---|---|---|---|---|---|
| `1080i25` | `25p` | Broadcast source, OTT rendition at frame rate | Reference: one frame per frame (`yadif=0`) | 25 fps | Yes |
| `1080i25` | `50p` | Broadcast source, OTT rendition at field rate | Reference: one frame per field (`yadif=1`) | 50 fps (HFR models with v1) | Yes |
| `25p` | `1080i25` | Progressive master, interlaced broadcast | Distorted: one frame per frame | 25 fps | Yes |
| `50p` | `1080i25` | 50p master, interlaced broadcast | Distorted: one frame per field | 50 fps (HFR models with v1) | Yes |
| `1080i25*` | `25p` | PAFF source, OTT rendition at frame rate | Reference: one frame per frame | 25 fps | Yes |
| `25p` | `1080i25*` | Progressive master, PAFF broadcast | Distorted: one frame per frame | 25 fps | Yes |
| `1080i25` | `1080i25` | Interlaced transcode | Nothing is deinterlaced | 25 fps, both fields woven | **No** |
| `1080i25*` | `50p` | PAFF source, OTT rendition at field rate | Reference: one frame per frame, instead of per field | Reference at 25 vs distorted at 50: half of the pairs are 20 ms off | **No** |
| `50p` | `1080i25*` | 50p master, PAFF broadcast | Distorted: one frame per frame, instead of per field | 25 fps, only the first field (HFR models picked as if 50 fps) | **No** |

Any other frame rate pair with an interlaced input (for example `1080i25` vs
`30p`) stops with an error that suggests `--fps`. `--fps` forces a common frame
rate on both inputs but does not deinterlace, so it is a workaround, not a fix,
for the unsupported rows.

To check whether an interlaced file is reported at its field rate:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=r_frame_rate,avg_frame_rate,field_order -of default=nw=1 file.ts
```

An interlaced `field_order` (`tt`, `bb`, `tb`, `bt`) with `r_frame_rate` twice
`avg_frame_rate` (for example `50/1` and `25/1`) is a `1080i25*` input.

## Frame ranges

Measuring a long video takes time, and a single calculation runs on one
machine. `--start-frame` and `--frame-count` measure a range of frames instead
of the whole video, without cutting or re-encoding the inputs. Frames are numbered as
in the log of the full calculation: after sync, deinterlacing and frame rate
conversion, at the frame rate libvmaf receives.

A range returns exactly the frames of the full calculation, with the same frame
numbers and identical scores. Consecutive ranges therefore join into the full
calculation: concatenating their logs gives its log, and its mean is the mean
of the joined frames, or the mean of each range weighted by its
`vmaf.range.frames_scored`. This lets a higher layer split a long video into
ranges and compute them in parallel, on one machine or on several:

```bash
# 1. Sync once
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 3 --sync-only   # sync.offset: 1.5

# 2. One range per process or instance, with the same offset and options
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-offset 1.5 --start-frame 0    --frame-count 9000
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-offset 1.5 --start-frame 9000 --frame-count 9000
```

A range can also search the sync itself with `--sync-window`: the search always
runs at the beginning of the videos and finds the same offset for every range,
but each range then repeats its cost.

How a range keeps the frames of the full calculation:

- Each input is read from shortly before the range (`-ss`), keeping its original
  timestamps, so deinterlacing and frame rate conversion select the same frames.
- The first frame the full calculation measures is found by running the same
  filters up to it, and each range counts its frames from there.
- Two extra frames are measured on each side of the range and dropped from its
  log: the motion features of a frame depend on the previous and the next frame.

Each range writes its own log, named after the requested frames:
`<distorted>_vmaf_f<first>-<last>.{json,xml,csv}` (`-end` without
`--frame-count`). Its frames keep their frame numbers in the full calculation,
and its pooled metrics cover only the range. Its summary file is named after
the log (`<distorted>_vmaf_f<first>-<last>_summary.json`), so ranges computed in
parallel never overwrite each other's results.

With `--cambi-heatmap`, a range writes its heatmaps to
`<distorted>_cambi_heatmap_f<first>-<last>/`, and its summary points to that
directory (`vmaf.cambi_heatmap_path`). Each file holds only the pictures of the
range: concatenating the files of the same name of consecutive ranges, in range
order, gives the heatmaps of the full calculation byte for byte (with more
than one thread, see [Known bugs](#known-bugs)).

Limits:

- Only MP4, MOV, Matroska and WebM inputs. Other containers are an error
  (exit code 1): MPEG-TS seeks to the next keyframe, which would silently
  score other frames, and raw elementary streams have no timestamps.
- A range that starts after the last frame is an error. A range that loses
  frames before the end of the videos, e.g. after an inexact seek, is an error
  too, never a partial result.
- Without a sync offset, both videos must start at the same timestamp.
- Not yet with `--subsample`.

With `--gpu`, ranges join into the full GPU calculation in the same way:
`libvmaf_cuda` gives identical values run after run, and the seek, the sync
trims and the range trim select the same frames on the CPU and, for an input
that stays on the GPU after hardware decoding, on the GPU.

## Examples

### Basic VMAF (no sync)

```bash
# VMAF v1 HD: vmaf_v1_hd and vmaf_v1_phone
easyvmaf -d distorted.mp4 -r reference.mp4
```

### Model selection

```bash
# VMAF v1 4K at 1.5H
easyvmaf -d distorted.mp4 -r reference.mp4 --display 4k

# Both v1 4K models, 1.5H and 3H ([0, 110])
easyvmaf -d distorted.mp4 -r reference.mp4 --display 4k --view 1.5h 3h

# Only the v1 phone model
easyvmaf -d distorted.mp4 -r reference.mp4 --view phone

# VMAF v0.6 models, as in easyVmaf 3.x: vmaf_hd, vmaf_hd_neg, vmaf_hd_phone
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6

# v1 and v0.6 in one pass, to compare both generations
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 1 0.6
```

### CAMBI encoding parameters

```bash
# The distorted file was upscaled from a 1280x720 8-bit encode
easyvmaf -d distorted.mp4 -r reference.mp4 --enc-size 1280x720 --enc-bitdepth 8

# CAMBI heatmaps, written to <distorted>_cambi_heatmap/
easyvmaf -d distorted.mp4 -r reference.mp4 --cambi-heatmap
```

With v1 models the heatmap directory is passed inside the libvmaf `model=`
option, where `:`, `|`, `\` and `'` cannot be escaped. If the distorted file path
contains any of them, easyVmaf stops with an error before running FFmpeg; rename
or move the file.

The heatmaps are of the distorted picture: one raw 16-bit gray file per CAMBI
scale (`cambi_heatmap_scale_<s>_<W>x<H>_16b.gray`, 5 scales), with picture n at
n × picture size. `<W>x<H>` is the size of the scale computed from the
encoding size, not always the picture size: from a 1920x1080 encoding size the
v1 models compute CAMBI at half resolution (`cambi_high_res_speedup`), so every
scale holds pictures of half the size in its name (scale 0 of a 1080p encoding
size, `..._1920x1080_16b.gray`, holds 960x540 pictures). The picture size in
bytes is the file size divided by the frames scored.

### With automatic sync

```bash
# Sync window of 2 seconds starting from the beginning of reference
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 2

# Sync window starting at 6 s into the distorted video, reverse direction
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 3 --sync-start 6 --sync-reverse
```

Human-readable output of the first command (logs go to stderr):

```
=======================================
Results: distorted.mp4
=======================================
VMAF computed
=======================================
offset:  1.5  | psnr:  40.032121
pix_fmt: yuv420p10le | HFR: off
vmaf_v1_hd     93.256258  [vmaf_v1.0.16_3d0h, 0-100]
vmaf_v1_phone  95.393178  [vmaf_v1.0.16_5d0h, 0-100]
VMAF output file path:  distorted_vmaf.json
Summary file path:  distorted_vmaf_summary.json
```

The offset of 1.5 s means that the first frame of `distorted.mp4` matches the
reference 1.5 s in. So easyVmaf trimmed the first 1.5 s of the reference before
computing VMAF; 40.03 dB is the PSNR of that best match.

Without a sync search, `--sync-offset X` applies a manual offset: positive to trim
the reference, negative to trim the distorted video. Automatic offsets use the
same sign convention in the summary file and the human output, so the offset reported by
`--sync-only` can be passed to `--sync-offset` as is. A zero manual offset is reported
as `0.0`, including `--sync-offset -0`.

### Sync measurement only

`--sync-only` requires an explicit, finite `--sync-window` greater than zero.
Missing, zero, negative, NaN, or infinite windows are usage errors (exit code 2),
reported before checking FFmpeg or probing videos. Every matched input is
synchronized, with one result per file. No VMAF calculation or libvmaf log is
produced: the summary file of each input is `<distorted>_sync_summary.json` and
contains only `schema_version`, `distorted`, `reference`, and `sync`.

```bash
easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 2 --sync-only

# Batch: one summary file per matched file
easyvmaf -d "folder/*.mp4" -r reference.mp4 --sync-window 2 --sync-only
```

### Batch processing

```bash
# Glob pattern — one log and one summary file per file
easyvmaf -d "folder/*.mp4" -r reference.mp4
```

### GPU

```bash
# Decode on the GPU, compute v0.6 with libvmaf_cuda
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6 --gpu

# Decode on the GPU, compute the default v1 models on the CPU
easyvmaf -d distorted.mp4 -r reference.mp4 --gpu
```

`--gpu` uses an NVIDIA GPU (CUDA) for every part of the calculation it can do
without changing a score, and the CPU for the rest. Each part has an opt-out:

| Flags | Decoding | VMAF |
|---|---|---|
| (none) | CPU | CPU (`libvmaf`) |
| `--gpu` | GPU (NVDEC) where the input allows it | GPU (`libvmaf_cuda`) where the models allow it, else CPU |
| `--gpu --disable-hw-decode` | CPU | as `--gpu`; with v0.6, the `--gpu` of earlier versions |
| `--gpu --disable-vmaf-cuda` | as `--gpu` | CPU |

`--disable-hw-decode` and `--disable-vmaf-cuda` without `--gpu` are a usage
error. The sync search always runs on the CPU.

**VMAF on the GPU.** `libvmaf_cuda` only computes the v0.6 models at 8 bits:
libvmaf 3.2.1 has no CUDA extractors for the v1 features (`cambi`,
`speed_chroma`, `adm3`, `motion3`). With any v1 model, including the default,
or with `--bitdepth 10`, `--gpu` computes VMAF on the CPU, decodes on the GPU,
says why in the log and records `"cuda": false` in the summary:

```
VMAF runs on the CPU (libvmaf): libvmaf_cuda cannot compute VMAF v1 models
```

With `libvmaf_cuda` both inputs are measured in `yuv420p`. Its scores differ
from the CPU scores by thousandths, a difference of libvmaf's own CUDA
extractors ([Netflix/vmaf#1644](https://github.com/Netflix/vmaf/issues/1644),
about 0.003 VMAF per frame in motion;
[Netflix/vmaf#1564](https://github.com/Netflix/vmaf/issues/1564), ADM rounding),
so compare scores computed the same way. `vmaf.cuda` in the summary says which.

#### Hardware decoding

Decoding on the GPU **never changes a score**: a frame stays on the GPU only
for the steps that give exactly the CPU's frames.

- **When VMAF runs on the CPU** (v1, `--bitdepth 10`, `--disable-vmaf-cuda`),
  each decoded frame is copied back to the CPU right away and goes through the
  usual chain.
- **With `libvmaf_cuda`**, an input already at the display resolution
  (1920x1080 or 3840x2160), in `yuv420p` and not deinterlaced (progressive, or
  with `--fps`) stays on the GPU up to `libvmaf_cuda`. Scaling, deinterlacing
  and 10 to 8 bit conversion give different frames on the GPU. For instance, in
  our tests, upscaling a 720p H.264 encode to 1080p with `scale_cuda` instead of
  FFmpeg's `scale` lowered VMAF v1 by 2.2 points. So an input that needs any of
  them is copied back to the CPU after decoding: only its decoding moves to the
  GPU.

In practice, it helps when software decoding is what keeps the CPU busy, as
with high-bitrate inputs. We measured it with the equivalent FFmpeg commands
(not an easyVmaf benchmark), on a Tesla T4 with 4 vCPU, one run each, with a
synthetic 1080p25 H.264 distorted video against a 268 Mb/s H.264 reference:

| Calculation | CPU decoding | NVDEC | Score |
|---|---|---|---|
| v0.6 with `libvmaf_cuda`, 1200 frames | 55.2 s (212 s of CPU) | **12.8 s** (7 s of CPU) | identical |
| v1 on the CPU, 300 frames | 22.6 s | **11.1 s** | identical |

The gain depends on the codec, the bitrate and the CPU: with a light input and
a fast CPU, software decoding is cheap and the gain is smaller.

Inputs decoded on the GPU: H.264 8-bit 4:2:0, HEVC 8 and 10-bit 4:2:0 and VP9
8-bit 4:2:0, the decoders validated as bit-exact. Each one is first checked by
decoding one frame, since what NVDEC supports depends on the GPU. Any other
input (MPEG-2, whose NVDEC output differs, AV1, ProRes, FFV1, H.264 High 10 or
4:2:2, ...), or one this GPU or FFmpeg build cannot decode, is decoded on the
CPU with a warning, and the summary records it as `"sw"`:

```
Decoding the reference video ref.mov in software: cuda decoding of prores yuv422p10le is not supported
```

```json
"hw_decode": {"api": "cuda", "distorted": "hw", "reference": "sw"}
```

#### What runs on the GPU

We split every step of the calculation into three groups, based on what we
measured on a Tesla T4 with FFmpeg 8.1 and libvmaf 3.2.1.

**On the GPU with `--gpu`:**

| Step | On the GPU | Score |
|---|---|---|
| Decoding (H.264, HEVC and VP9, see above) | NVDEC | Unchanged: bit-exact |
| Format conversion without resizing (`nv12` → `yuv420p`) | `scale_cuda` | Unchanged: bit-exact |
| Frame selection and timestamps (`fps`, `trim`, sync, frame ranges) | Same filters on GPU frames | Unchanged: the same frames |
| VMAF v0.6 features (ADM, VIF, motion) | `libvmaf_cuda` | Differs from the CPU by thousandths (see [GPU](#gpu)) |

**Possible on the GPU, but kept on the CPU because the score changes.** The
error is the typical difference per pixel between the CPU and the GPU output,
in 8-bit levels (0 to 255; one level is the smallest possible step):

| Step | CUDA filter | Error per pixel | Effect on VMAF |
|---|---|---|---|
| Scaling (e.g. 720p → 1080p) | `scale_cuda` | 1.0 level | VMAF v1 −2.2 points in our test |
| Deinterlacing | `yadif_cuda` | 1.1 levels | Not measured |
| 10 to 8 bit conversion | `scale_cuda` | 0.6 levels | Not measured |
| MPEG-2 decoding | NVDEC | 0.13 levels | Not measured |

These differences are invisible, but they are systematic on edges and detail,
which is what VMAF weighs: a scaler that differs by one level per pixel moved
VMAF v1 by 2.2 points. Hence, in practice, an input that needs one of these
steps is copied back to the CPU after decoding.

**Not available on the GPU:**

| Step | Why | Consequence |
|---|---|---|
| PSNR in the libvmaf log | Neither libvmaf nor FFmpeg has a CUDA PSNR | With `libvmaf_cuda`, both frames are copied to the CPU for it. It stays: without a CPU feature, libvmaf 3.2.1 crashes with `n_threads` set |
| PSNR of the sync search | FFmpeg's `psnr` filter is CPU only | The sync search always runs on the CPU |
| VMAF v1 features (`cambi`, `speed_chroma`, `adm3`, `motion3`) | libvmaf has no CUDA version | With v1, VMAF runs on the CPU (`vmaf.cuda: false`) |
| CAMBI heatmaps (`--cambi-heatmap`) | CAMBI is CPU only | Frames are copied to the CPU for it |
| VMAF at 10 bits | easyVmaf measures in `yuv420p` with `libvmaf_cuda` | With `--bitdepth 10`, VMAF runs on the CPU |
| Other codecs (ProRes, FFV1, H.264 High 10 or 4:2:2, AV1, ...) | No NVDEC support, or not validated as bit-exact | Decoded on the CPU, recorded as `"sw"` |

Requirements: see [GPU requirements](#gpu-requirements). An FFmpeg build
without `libvmaf_cuda` exits with code 1 only when VMAF would run on the GPU;
one without NVDEC decodes every input on the CPU, with the warning above. In
Docker, NVDEC needs the `video` driver capability, which the CUDA image sets.

Roadmap: an explicit option to run the whole chain on the GPU, scaling and
deinterlacing included, which changes the scores; and other APIs
(VideoToolbox, VAAPI, QSV, Vulkan).

## Summary file

Every run writes the final results of each input to a JSON summary file, next
to the libvmaf log and named after it, with `schema_version: 2`:

| Run | Summary file |
|---|---|
| VMAF | `<distorted>_vmaf_summary.json` |
| Frame range | `<distorted>_vmaf_f<first>-<last>_summary.json` |
| `--sync-only` | `<distorted>_sync_summary.json` |

The path is printed at the end of each result (`Summary file path:`). For
`easyvmaf -d distorted.mp4 -r reference.mp4 --sync-window 2`,
`distorted_vmaf_summary.json` contains:

```json
{
  "schema_version": 2,
  "distorted": "distorted.mp4",
  "reference": "reference.mp4",
  "sync": {"offset": 1.5, "psnr": 40.032121},
  "vmaf": {
    "display": "hd",
    "pix_fmt": "yuv420p10le",
    "hfr": false,
    "cuda": false,
    "scores": {"vmaf_v1_hd": 93.256258, "vmaf_v1_phone": 95.393178},
    "models": [
      {"name": "vmaf_v1_hd", "libvmaf_model": "vmaf_v1.0.16_3d0h",
       "vmaf_version": "1", "view": "3h", "range": [0, 100]},
      {"name": "vmaf_v1_phone", "libvmaf_model": "vmaf_v1.0.16_5d0h",
       "vmaf_version": "1", "view": "5h", "range": [0, 100]}
    ],
    "output_file": "distorted_vmaf.json"
  }
}
```

| Field | Description |
|---|---|
| `schema_version` | `2` since easyVmaf 4.0. Present in every record, including `--sync-only`. |
| `sync.offset` | Offset in seconds. Positive: the reference was trimmed; negative: the distorted video was trimmed. |
| `sync.psnr` | Sync PSNR, or `null` when sync was not calculated or is not finite. |
| `vmaf.display` | `hd` or `4k`. |
| `vmaf.pix_fmt` | Pixel format both inputs were measured in. |
| `vmaf.hfr` | `true` when the v1 HFR variants were used. |
| `vmaf.cuda` | `true` when VMAF was computed on the GPU (`libvmaf_cuda`); `false` without `--gpu`, and with `--gpu` when the models need the CPU or with `--disable-vmaf-cuda`. See [GPU](#gpu). |
| `vmaf.scores` | Mean score of each model over all frames, keyed by score name. |
| `vmaf.models` | One entry per score: libvmaf model id (the `_hfr_` variant when HFR is on), VMAF version, view (`default`, `neg` or `phone` for v0.6) and score range. |
| `vmaf.output_file` | Per-frame libvmaf log: same directory and base name as the distorted video, plus `_vmaf.{json,xml,csv}` (`_vmaf_f<first>-<last>.{json,xml,csv}` for a frame range). |
| `vmaf.cambi_heatmap_path` | Heatmap directory, only with `--cambi-heatmap`. |
| `vmaf.hw_decode` | Only with hardware decoding (`--gpu` without `--disable-hw-decode`): `api` (`cuda`), and `"hw"` or `"sw"` for the `distorted` and `reference` inputs. See [Hardware decoding](#hardware-decoding). |
| `vmaf.range` | Only with a frame range: `start_frame`, `frame_count` (`null` without `--frame-count`) and `frames_scored`, which is lower than `frame_count` for the last range of a video. |

`vmaf.scores` is the flat view for quick reads; `vmaf.models` carries the context
needed to avoid comparing scores of different generations.

Each summary is strict JSON. Logs, progress, diagnostics, and help associated with
usage errors go to stderr; the human-readable results go to stdout. If a file fails,
the command stops with a nonzero exit code and writes no summary for that file;
the summaries of earlier files in a batch remain. A summary left by a previous
run is not removed.

SIGINT (Ctrl-C) exits with code 130 and reports the interruption on stderr.
The interrupted calculation writes no summary, and the summaries of the files
already completed in a batch remain. easyVmaf stops the running VMAF process
and waits a bounded time for it to exit; during an automatic sync search, it
can still wait for the running PSNR workers to finish.

Finite sync PSNR values keep their numeric value, rounded to six decimal places.
When sync PSNR was not calculated, `sync.psnr` is `null` with no status field.
For nonfinite PSNR, `sync.psnr` is also `null`, and `sync.psnr_status` identifies
the value as `"positive_infinity"`, `"negative_infinity"`, or `"nan"`. Identical
frames legitimately produce positive infinity. This representation affects only
the summary file; the Python API and sync calculation retain the numeric value.
Nonfinite offsets or VMAF scores cause an error before the summary is written.

## Python API

The CLI is a client of this API: each flag is an argument of the same name
(`--sync-offset` is `sync_offset=`, `--output-format` is `output_format=`).

```python
from easyvmaf import Vmaf

v = Vmaf('distorted.mp4', 'reference.mp4', display='hd', vmaf_versions=('1', '0.6'))
offset, psnr = v.sync(2)           # optional: sync window of 2 s (SyncResult)
result = v.compute()               # VmafResult

result.scores       # {'vmaf_v1_hd': ..., 'vmaf_v1_phone': ..., 'vmaf_hd': ..., ...}
result.pix_fmt      # 'yuv420p10le'
result.log_path     # 'distorted_vmaf.json'
result.sync_offset  # the offset applied, in seconds
```

```python
# Frames 9000-17999 of the full calculation, with a manual offset
v = Vmaf('distorted.mp4', 'reference.mp4', sync_offset=1.5,
         start_frame=9000, frame_count=9000)
result = v.compute()
result.frames_scored  # 9000, or fewer for the last range
```

`sync(window, start=0, reverse=False)` is `--sync-window`, `--sync-start` and
`--sync-reverse`: it returns `SyncResult(offset, psnr)` and stores the offset in
`v.sync_offset`, which `compute()` applies. All `Vmaf()` arguments after the two
paths are keyword-only: `display`, `vmaf_versions`, `views`, `hfr`, `bitdepth`,
`enc_size`, `enc_bitdepth`, `model_options`, `output_format`, `loglevel`,
`subsample`, `threads`, `progress`, `shortest`, `fps`, `cambi_heatmap`, `gpu`,
`sync_offset`, `start_frame`, `frame_count`, `disable_hw_decode` and
`disable_vmaf_cuda`. `result.cuda` and `result.hw_decode` are the summary's
`vmaf.cuda` and `vmaf.hw_decode` (`None` without hardware decoding).

The public API is what the `easyvmaf` package exports: `Vmaf`, `VmafResult`,
`SyncResult`, `validate_model_config`, `validate_range_config`,
`UnsupportedModelConfigError`, `UnsupportedRangeError`,
`UnsupportedFramerateError`, `FFmpegExecutionError`, `ModelSpec`, `ModelRun`,
`CATALOG`, `select_models`, `check_ffmpeg` and `__version__`. The submodules
(`easyvmaf.vmaf`, `easyvmaf.ffmpeg`, ...) are internal and can change in any
release.

---

## Docker

From the 5.0.0 release on, every release publishes two images on the GitHub
Container Registry, `ghcr.io/gdavila/easyvmaf`, built from this repository's
`Dockerfile` and `Dockerfile.cuda`:

| Image | Tags | Contents |
|-------|------|----------|
| CPU | `5.0.0`, `5.0`, `5`, `latest` | easyVmaf, FFmpeg 8.1, libvmaf 3.2.1 |
| CUDA | `5.0.0-cuda`, `5.0-cuda`, `5-cuda`, `latest-cuda`, `cuda` | The same, plus `libvmaf_cuda`, NVDEC and the CUDA filters, on the `nvidia/cuda` 12.8 base image (NVIDIA driver >= 570) |

A pre-release (`5.1.0rc1`) is tagged only with its full version (`5.1.0rc1`,
`5.1.0rc1-cuda`). Both images are `linux/amd64` only: on Apple Silicon, Docker
runs them emulated, which is slower; there, install easyVmaf and FFmpeg
with Homebrew instead (see [Installation](#installation)).

```bash
docker pull ghcr.io/gdavila/easyvmaf:5.0.0
docker pull ghcr.io/gdavila/easyvmaf:5.0.0-cuda
```

### Running with Docker

```bash
# CPU
docker run --rm -v /path/to/videos:/videos \
  ghcr.io/gdavila/easyvmaf:5.0.0 -d /videos/distorted.mp4 -r /videos/reference.mp4

# With sync
docker run --rm -v /path/to/videos:/videos \
  ghcr.io/gdavila/easyvmaf:5.0.0 -d /videos/distorted.mp4 -r /videos/reference.mp4 --sync-window 2

# GPU (requires NVIDIA Container Toolkit): GPU decoding and libvmaf_cuda (v0.6)
docker run --rm --gpus all -v /path/to/videos:/videos \
  ghcr.io/gdavila/easyvmaf:5.0.0-cuda -d /videos/distorted.mp4 -r /videos/reference.mp4 --vmaf-version 0.6 --gpu

# GPU decoding, default v1 models on the CPU
docker run --rm --gpus all -v /path/to/videos:/videos \
  ghcr.io/gdavila/easyvmaf:5.0.0-cuda -d /videos/distorted.mp4 -r /videos/reference.mp4 --gpu
```

The CUDA image sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`: NVDEC
needs the `video` capability. Without it every input is decoded on the CPU,
with a warning. An image built from an older `Dockerfile.cuda` lacks it:
rebuild it, or pass `-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video` to
`docker run`.

### Building the images

Instead of pulling them, build the images from a clone of the repository, and
use these names in place of `ghcr.io/gdavila/easyvmaf:5.0.0` and `:5.0.0-cuda`
in the examples above:

```bash
docker build -t easyvmaf .                          # CPU
docker build -f Dockerfile.cuda -t easyvmaf:cuda .  # CUDA
```

The CUDA image builds FFmpeg without `--enable-nonfree`: its CUDA filters are
compiled with clang (`--enable-cuda-llvm`) instead of nvcc, and libnpp is left
out, so the image can be redistributed. FFmpeg is LGPL-3.0-or-later in both
images (`--enable-version3`, no `--enable-gpl`).

The license texts of what each image builds from source (FFmpeg, libvmaf, and
in the CUDA image dav1d and nv-codec-headers) are in
`/usr/local/share/licenses`. FFmpeg is unmodified; `ffmpeg/SOURCE` there says
where its source code is: the archive the image was built from, with its
sha256 and the commit of its tag:

```bash
docker run --rm --entrypoint cat ghcr.io/gdavila/easyvmaf:5.0.0 /usr/local/share/licenses/ffmpeg/SOURCE
```

### Build arguments

Both Dockerfiles accept these build-time arguments:

| ARG | Default | Description |
|-----|---------|-------------|
| `FFMPEG_version` | `8.1` | FFmpeg release tag (>= 8.1) |
| `VMAF_version` | `3.2.1` | libvmaf release tag (>= 3.2.1) |
| `EASYVMAF_VERSION` | `5.0.0` | easyVmaf version label (the published images take it from the release tag) |
| `DAV1D_version` | `1.4.3` | dav1d release (CUDA image only — built from source) |
| `NVCODEC_version` | `13.0.19.1` | nv-codec-headers release (CUDA image only). 13.0.19.1 is the only release that builds both FFmpeg 8.1 and libvmaf 3.2.1 |

```bash
# Custom versions
docker build --build-arg FFMPEG_version=8.1 --build-arg VMAF_version=3.2.1 -t easyvmaf .
```

### Docker Compose

A `docker-compose.yml` is included with pre-configured `easyvmaf` (CPU) and `easyvmaf-cuda` (GPU) services:

```bash
# CPU service
VIDEO_DIR=/path/to/videos docker compose run easyvmaf \
  -d /videos/distorted.mp4 -r /videos/reference.mp4

# GPU service
VIDEO_DIR=/path/to/videos docker compose run easyvmaf-cuda \
  -d /videos/distorted.mp4 -r /videos/reference.mp4 --vmaf-version 0.6 --gpu
```

`VIDEO_DIR` is required: the directory with your videos, mounted at `/videos`.
The services build the images from the checkout (`easyvmaf:latest`,
`easyvmaf:cuda`); they do not pull the published ones.

---

## Sync examples explained

### Reference delayed relative to distorted

![](https://raw.githubusercontent.com/gdavila/easyVmaf/master/readme/easyVmaf1.svg)

`reference.ts` has 0.7 extra seconds at the start: the first frame of `distorted-A.ts` appears 0.7 seconds into `reference.ts`. Use `--sync-window` to search for the offset automatically:

```bash
easyvmaf -d distorted-A.ts -r reference.ts --sync-window 2
```

With a 2-second sync window, easyVmaf searches the first 2 seconds of `reference.ts` for the best PSNR match against the first frames of `distorted-A.ts`. The reported offset is 0.7: the first 0.7 seconds of `reference.ts` were trimmed.

### Distorted delayed relative to reference

![](https://raw.githubusercontent.com/gdavila/easyVmaf/master/readme/easyVmaf2.svg)

This time `distorted-B.ts` has the extra seconds: the first frame of `reference.ts` appears 8.3 seconds into `distorted-B.ts`. Use `--sync-reverse` to flip the sync direction:

```bash
easyvmaf -d distorted-B.ts -r reference.ts --sync-window 3 --sync-start 6 --sync-reverse
```

With `--sync-reverse`, the window slides over the distorted video: `--sync-start 6 --sync-window 3` searches from 6 to 9 seconds into `distorted-B.ts` for the first frames of `reference.ts`. The reported offset is negative (−8.3), meaning that the distorted video was trimmed.

---

## Known bugs

- **Error messages at the end of a `libvmaf_cuda` run.** With `--gpu` and v0.6,
  libvmaf 3.2.1 can print `problem flushing libvmaf context` or `context could
  not be synchronized` when it finishes. The run succeeds and its log and scores
  are complete.
- **CAMBI heatmaps with more than one thread.** libvmaf 3.2.1 can leave part of
  the first pictures of a `--cambi-heatmap` file as zeros when it runs on more
  than one thread, which is the default
  ([Netflix/vmaf#1676](https://github.com/Netflix/vmaf/issues/1676)). Scores are
  not affected. Use `--threads 1` if you need exact heatmaps.

---

## Versioning

From 5.0.0, easyVmaf follows [Semantic Versioning](https://semver.org). The
versioned contract is:

- the CLI flags and exit codes;
- the output file names: libvmaf log, summary file and CAMBI heatmap directory;
- the [summary file](#summary-file) format, versioned by its `schema_version`;
- the score names (`vmaf_v1_hd`, `vmaf_hd`, ...);
- what the `easyvmaf` package exports (`easyvmaf.__all__`). The submodules are
  internal.

A new default that changes scores, such as a different model or measurement
format, is a major release. A bug fix that changes scores is a minor release,
with a prominent CHANGELOG note that says which inputs change.

Deprecated flags and API names keep working, with a warning, in a minor release
and are removed in the next major release.

Releases before 5.0.0 made no compatibility guarantees.

## Migrating from 3.x

easyVmaf 5.0 changes the default model, the requirements, the CLI flags, the
Python API and the JSON output of 3.x. Every change has a one-line migration.
Coming from 4.x instead? The
[CHANGELOG](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md) lists
the changes since 4.0.

| Contract | 3.x | 5.0 | Migration |
|---|---|---|---|
| Default model | v0.6 (`vmaf_hd`, `vmaf_hd_neg`, `vmaf_hd_phone`) | v1 (`vmaf_v1_hd`, `vmaf_v1_phone`) | `--vmaf-version 0.6` |
| Python | >= 3.8 | >= 3.10 | Upgrade Python |
| FFmpeg and libvmaf | FFmpeg >= 5.0, libvmaf with built-in v0.6 models | FFmpeg >= 8.1, libvmaf >= 3.2.1 with built-in models (verified by computing a v1 frame) | The [Docker](#docker) images, `brew upgrade libvmaf ffmpeg`, a BtbN build, or rebuild libvmaf (see [Installation](#installation)) |
| Installation | From a clone of the repository | `pipx install easyvmaf` (PyPI), or the Docker images on GHCR | See [Installation](#installation) |
| CLI flags | `-model HD`, `-sw`, `-output_fmt`, ... | `--display hd`, `--sync-window`, `--output-format`, ... | 3.x flags are rejected; see [Flag equivalences](#flag-equivalences) |
| Manual offset | `-ss S` without `-sw`, negative with `-reverse` | `--sync-offset S`: positive trims the reference, negative the distorted video | `-ss 1.5 -reverse` → `--sync-offset -1.5` |
| `-gpu` | `libvmaf_cuda` with any model, CPU decoding | `--gpu`: also decodes on the GPU (NVDEC), with identical scores. `libvmaf_cuda` only computes v0.6 at 8 bits, so with the default v1 models VMAF runs on the CPU (`vmaf.cuda: false`) | Add `--vmaf-version 0.6` to compute VMAF on the GPU, as in 3.x |
| JSON (`-json`) | Printed to stdout: `vmaf.model` and flat scores in `vmaf` | Always written to the [summary file](#summary-file): `schema_version: 2`, `vmaf.display`, `vmaf.scores`, `vmaf.models` | Read scores from `vmaf.scores` of `<distorted>_vmaf_summary.json` |
| Text output | `VMAF HD:`, `VMAF Neg:`, `VMAF Phone:`, `VMAF 4K:` labels | One line per score: name, value, libvmaf model and range | Parse score names, or read the summary file |
| `docker-compose.yml` | `VIDEO_DIR` defaults to `./video_samples` | `VIDEO_DIR` is required | Set `VIDEO_DIR` |
| `vmaf(mainSrc, refSrc, output_fmt, model='HD', phone=False, ...)` | Class `vmaf`, `output_fmt` positional, `model`, `phone` | `Vmaf(distorted, reference, ...)`, keyword-only after the paths: `display='hd'`, `vmaf_versions=('1',)`, `views=None`, ...; `output_format` defaults to `'json'` | `from easyvmaf import Vmaf`; `phone` is gone (select with `views`) |
| `vmaf()` arguments | `output_fmt=`, `print_progress=`, `end_sync=`, `manual_fps=`, `gpu_mode=` | `output_format=`, `progress=`, `shortest=`, `fps=`, `gpu=` | Rename the arguments |
| Manual offset (API) | `v.offset = 1.5`, or `setOffset(1.5)` | `Vmaf(..., sync_offset=1.5)` | Pass it to the constructor |
| `syncOffset(syncWindow=3, start=0, reverse=False)` | Returns `[offset, psnr]` | `sync(window, start=0, reverse=False)` returns `SyncResult(offset, psnr)`; `window` has no default | Rename the method; pass the window |
| `getVmaf(autoSync=False)` | Returns the FFmpeg process; `autoSync=True` runs `syncOffset()` first | `compute()` returns a `VmafResult` with scores, models and paths; no `autoSync` | Call `sync()` before `compute()`; read `result.scores` instead of parsing the log |
| `FFprobe`, `FFmpegQos`, `inputFFmpeg` | Exported by `easyvmaf` | Not exported: the submodules are internal | Use `Vmaf` |
| `VMAF_MODELS`, `HD_MODEL_NAME`, `_4K_MODEL_NAME`, `*_VERSION` | In `easyvmaf.ffmpeg` | Removed; the catalog is `CATALOG` | `from easyvmaf import CATALOG` |
| `check_ffmpeg()['builtin_models']` | Probe of `vmaf_v0.6.1` | `libvmaf_v1` (probe of `vmaf_v1.0.16_3d0h`) | `from easyvmaf import check_ffmpeg`; rename the key |
| `FFmpegQos.vmaf_cambi_heatmap_path` | In `easyvmaf.ffmpeg` | `vmaf.cambi_heatmap_path` and `VmafResult.cambi_heatmap_path` | Read it from the result |

Unchanged: the v0.6 score names, the libvmaf log path
(`<distorted>_vmaf.{json,xml,csv}`), the PSNR sync search for progressive
inputs at the same frame rate, duration handling and the libvmaf json/xml/csv
log formats. Interlaced inputs changed: easyVmaf now deinterlaces before
scaling, fixes two deinterlacing cases against a progressive reference, and
keeps sync field accurate. Reverse sync between inputs at different frame
rates now converts the right one, so its reported PSNR changes (see the
changelog).

With `--vmaf-version 0.6`, easyVmaf 5.0 produces the same v0.6 scores as 3.x on
the same libvmaf for progressive inputs; interlaced inputs can score
differently because of those fixes. Moving from libvmaf 3.0.0 to 3.2.1 changed
them by at most 0.00002 in our checks. With `--vmaf-version 1 0.6`, the v0.6
models are measured at 10 bits, which moved them by at most 0.028 when the
inputs are scaled. See the
[CHANGELOG](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md) for
the full verification.

New since 3.x:
- VMAF v1 and its parameters: `--vmaf-version`, `--view`, `--hfr`,
  `--bitdepth`, `--enc-size`, `--enc-bitdepth` and `--model-option`.
- [Frame ranges](#frame-ranges): `--start-frame` and `--frame-count`.
- GPU decoding with `--gpu`, and its opt-outs `--disable-hw-decode` and
  `--disable-vmaf-cuda`.
- The [summary file](#summary-file), and the [Docker images](#docker) on GHCR.
- In the Python API: `VmafResult`, `SyncResult`, `ModelSpec`, `ModelRun`,
  `CATALOG`, `select_models`, `check_ffmpeg`, `validate_model_config`,
  `validate_range_config`, and the `UnsupportedModelConfigError`,
  `UnsupportedRangeError` and `FFmpegExecutionError` exceptions.

### Flag equivalences

Only `-d` and `-r` keep their short form. Every other 3.x flag exits with code 2
and names its replacement.

| 3.x flag (removed) | Current flag |
|---|---|
| `-d` | `-d`, `--distorted` |
| `-r` | `-r`, `--reference` |
| `-sw` | `--sync-window` |
| `-ss` | `--sync-start` (sync window start) or `--sync-offset` (manual offset) |
| `-sync_only` | `--sync-only` |
| `-reverse` | `--sync-reverse` |
| `-fps` | `--fps` |
| `-subsample` | `--subsample` |
| `-threads` | `--threads` |
| `-endsync` | `--shortest` |
| `-output_fmt` | `--output-format` |
| `-cambi_heatmap` | `--cambi-heatmap` |
| `-progress` | `--progress` |
| `-verbose` | `--verbose` |
| `-json` | None: the [summary file](#summary-file) is always written |
| `-gpu` | `--gpu` |
| `-model HD` / `-model 4K` | `--display hd` / `--display 4k` |

Unique prefixes of long flags (`--sync-w`) are not accepted: write the full name.
