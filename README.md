# easyVmaf

Python tool based on FFmpeg and FFprobe to handle the video preprocessing required for VMAF:

- Deinterlacing
- Upscaling / downscaling
- Frame-to-frame syncing
- Frame rate adaptation
- Pixel format normalization

Since 4.0, easyVmaf computes [VMAF v1](https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v1.md)
by default. The VMAF v0.6 models of easyVmaf 3.x are still available with
`--vmaf-version 0.6`. Upgrading? See [Migrating from 4.x](#migrating-from-4x) or
[Migrating from 3.x](#migrating-from-3x).

On GitHub, this README follows the `master` branch, which can document changes
not released yet (see the `unreleased` entry of the
[CHANGELOG](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md)). For
the version you installed (`pip show easyvmaf`), read its page on
[PyPI](https://pypi.org/project/easyvmaf/) or the README of its `v<version>` tag.

Details about **How it Works** can be found [here](https://ottverse.com/vmaf-easyvmaf/).

## Requirements

- Linux / macOS
- Python >= 3.10
- FFmpeg >= 8.1 built with `--enable-libvmaf`
- libvmaf >= 3.2.1 built with `-Dbuilt_in_models=true`
- Python package: [`ffmpeg-progress-yield`](https://github.com/slhck/ffmpeg-progress-yield)

easyVmaf checks both at startup. It exits with an error if FFmpeg is older than
8.1, or if its libvmaf cannot compute a frame with the VMAF v1 model
`vmaf_v1.0.16_3d0h`.

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
- 3.2.1 also fixes wrong CUDA scores with inputs above 8 bits, which matters for
  `--gpu`.

libvmaf 3.2.1 still reports itself as **3.2.0**: `pkg-config --modversion libvmaf`
and the `version` field of the libvmaf log both say `3.2.0`, because the upstream
`v3.2.1` tag kept that version string. No version check can tell the two apart.
The only reliable test is to compute a frame with a v1 model, which is what
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
probe frames are 320x240 because the SpEED feature of v1 rejects frames smaller
than about 288x162. That limit does not affect normal use: easyVmaf always scales
the inputs to 1920x1080 or 3840x2160 before libvmaf.

### GPU requirements

For GPU-accelerated VMAF (`--gpu`, VMAF v0.6 only, see [GPU](#gpu)):

- NVIDIA GPU with CUDA support
- FFmpeg built with `--enable-nonfree --enable-ffnvcodec --enable-libvmaf`
- libvmaf built with `-Denable_cuda=true`
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (for Docker GPU usage)

For GPU decoding (`--enable-hwaccel`, any model, see
[Hardware decoding](#hardware-decoding)):

- NVIDIA GPU with NVDEC
- FFmpeg built with `--enable-ffnvcodec` and `--enable-cuda-nvcc` (or
  `--enable-cuda-llvm`): `ffmpeg -hwaccels` must list `cuda`, and the
  `scale_cuda` filter must exist, or every input is decoded on the CPU. libvmaf
  needs CUDA only with `--gpu`
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

**Docker.** The [Docker](#docker) image bundles FFmpeg 8.1 and libvmaf 3.2.1.
It is not published to a registry yet: clone the repository and build it with
`docker build -t easyvmaf .`.

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
| `--view {3h,5h,phone,1.5h} [...]` | hd: `3h 5h`; 4k: `1.5h` | Viewing distances, in picture heights. `phone` is an alias of `5h`. With `--display 4k`, `3h` selects the [0, 110] model. |
| `--hfr {auto,on,off}` | `auto` | High frame rate models. `auto`: when the effective frame rate is >= 47 fps. See [High frame rate](#high-frame-rate). |
| `--enc-size WxH` | distorted video size | Encoding resolution passed to CAMBI. |
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
| `--gpu` | off | GPU-accelerated VMAF via `libvmaf_cuda`. Only supports `--vmaf-version 0.6`. Requires a CUDA build of FFmpeg (see [Docker: CUDA](#cuda--gpu-build)). |
| `--enable-hwaccel [API]` | off | Decode the inputs on the GPU (`auto` or `cuda`; alone: `auto`) without changing any score. Any model; combinable with `--gpu`. See [Hardware decoding](#hardware-decoding). |

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
- `--gpu` requires `--vmaf-version 0.6` alone, and cannot be combined with
  `--bitdepth 10`.
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
variant with `_hfr_` in its id (e.g. `vmaf_v1.0.16_hfr_3d0h`). The HFR variant
keeps the score name; only `libvmaf_model` changes.

VMAF v1 differs from v0.6 in ways that change the scores:

- v1 uses the `cambi`, `speed_chroma_uv`, `adm3` and `motion3` features. VIF is
  gone. v1 measures chroma and banding; v0.6 measures luma only.
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

easyVmaf measures both inputs in one format:

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
below 216, and the whole v1 calculation fails. Low ABR rungs such as 256x144,
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
(`r_frame_rate` of 50, typical of H.264 PAFF broadcast streams).

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

`--start-frame` and `--frame-count` measure a range of frames instead of the
whole video, without cutting or re-encoding the inputs. Frames are numbered as
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
trims and the range trim all run on the CPU before the upload to the GPU.

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
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6 --gpu
```

`--gpu` only supports the v0.6 models: libvmaf 3.2.1 has no CUDA extractors for
the v1 features (`cambi`, `speed_chroma`, `adm3`, `motion3`). `--gpu` with any
v1 model, including the default, is a usage error:

```
error: --gpu only supports --vmaf-version 0.6: libvmaf_cuda cannot compute VMAF v1 models
```

In GPU mode both inputs are measured in `yuv420p`. Sync always runs on CPU; the
GPU is used only for the final VMAF scoring step, and also for decoding with
[`--enable-hwaccel`](#hardware-decoding).

### Hardware decoding

```bash
# Decode on the GPU, compute the default v1 models on the CPU
easyvmaf -d distorted.mp4 -r reference.mp4 --enable-hwaccel

# Decode and compute v0.6 on the GPU
easyvmaf -d distorted.mp4 -r reference.mp4 --vmaf-version 0.6 --gpu --enable-hwaccel
```

`--enable-hwaccel` decodes the inputs with NVDEC (`--enable-hwaccel` alone is
`auto`, which is `cuda`, the only API for now). It **never changes a score**: a
frame stays on the GPU only for the steps that give exactly the CPU's frames.
It works with any model without `--gpu`, and with `--gpu` (v0.6 on CUDA).

- **Without `--gpu`**, libvmaf runs on the CPU: each decoded frame is copied
  back to the CPU right away and goes through the usual chain.
- **With `--gpu`**, an input already at the display resolution (1920x1080 or
  3840x2160), in `yuv420p` and not deinterlaced (progressive, or with `--fps`)
  stays on the GPU up to `libvmaf_cuda`. Scaling, deinterlacing and 10 to 8 bit conversion give
  different frames on the GPU (`scale_cuda` costs 2.2 VMAF v1 points from 720p
  to 1080p), so an input that needs any of them is copied back to the CPU after
  decoding: only its decoding moves to the GPU.

It helps when software decoding is what keeps the CPU busy, as with
high-bitrate inputs. Measured with the equivalent FFmpeg commands (not an easyVmaf
benchmark), on a Tesla T4 with 4 vCPU, one run each, 1080p against 1080p:

| Calculation | CPU decoding | NVDEC | Score |
|---|---|---|---|
| v0.6 with `--gpu`, 1200 frames | 55.2 s (212 s of CPU) | **12.8 s** (7 s of CPU) | identical |
| v1 on the CPU, 300 frames | 22.6 s | **11.1 s** | identical |

Inputs decoded on the GPU: H.264 8-bit 4:2:0, HEVC 8 and 10-bit 4:2:0 and VP9
8-bit 4:2:0, the decoders validated as bit-exact. Each one is first checked by
decoding one frame, since what NVDEC supports depends on the GPU. Any other
input (MPEG-2, whose NVDEC output differs, AV1, ProRes, FFV1, H.264 High 10 or
4:2:2, ...), or one this GPU cannot decode, is decoded on the CPU with a
warning, and the summary records it as `"sw"`:

```
Decoding the reference video ref.mov in software: cuda decoding of prores yuv422p10le is not supported
```

```json
"hwaccel": {"api": "cuda", "decode": {"distorted": "hw", "reference": "sw"}}
```

Requirements: FFmpeg built with NVDEC (`ffmpeg -hwaccels` lists `cuda`;
otherwise easyvmaf exits with code 1), see [GPU requirements](#gpu-requirements).
In Docker, NVDEC needs the `video` driver capability, which the CUDA image
sets. The sync search always decodes on the CPU.

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
    "gpu": false,
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
| `vmaf.gpu` | `true` when VMAF was computed on the GPU (`--gpu`, `libvmaf_cuda`). |
| `vmaf.scores` | Mean score of each model over all frames, keyed by score name. |
| `vmaf.models` | One entry per score: libvmaf model id (the `_hfr_` variant when HFR is on), VMAF version, view (`default`, `neg` or `phone` for v0.6) and score range. |
| `vmaf.output_file` | Per-frame libvmaf log: same directory and base name as the distorted video, plus `_vmaf.{json,xml,csv}` (`_vmaf_f<first>-<last>.{json,xml,csv}` for a frame range). |
| `vmaf.cambi_heatmap_path` | Heatmap directory, only with `--cambi-heatmap`. |
| `vmaf.hwaccel` | Only with `--enable-hwaccel`: `api` (`cuda`) and `decode`, `"hw"` or `"sw"` for the `distorted` and `reference` inputs. See [Hardware decoding](#hardware-decoding). |
| `vmaf.range` | Only with a frame range: `start_frame`, `frame_count` (`null` without `--frame-count`) and `frames_scored`, which is lower than `frame_count` for the last range of a video. |

`vmaf.scores` is the flat view for quick reads; `vmaf.models` carries the context
needed to avoid comparing scores of different generations.

Each summary is strict JSON. Logs, progress, diagnostics, and help associated with
usage errors go to stderr; the human-readable results go to stdout. If a file fails,
the command stops with a nonzero exit code and writes no summary for that file;
the summaries of earlier files in a batch remain. A summary left by a previous
run is not removed.

SIGINT (Ctrl-C) exits with code 130 and reports the interruption on stderr.
The interrupted calculation writes no summary; completed batch summaries remain. An active VMAF scoring process is stopped and reaped, with a bounded wait.
During automatic synchronization, shutdown can still wait for running PSNR search
workers to finish.

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
`sync_offset`, `start_frame`, `frame_count` and `enable_hwaccel` (`None`,
`'auto'` or `'cuda'`).

The public API is what the `easyvmaf` package exports: `Vmaf`, `VmafResult`,
`SyncResult`, `validate_model_config`, `validate_range_config`,
`UnsupportedModelConfigError`, `UnsupportedRangeError`,
`UnsupportedFramerateError`, `FFmpegExecutionError`, `ModelSpec`, `ModelRun`,
`CATALOG`, `select_models`, `check_ffmpeg` and `__version__`. The submodules
(`easyvmaf.vmaf`, `easyvmaf.ffmpeg`, ...) are internal and can change in any
release.

---

## Docker

### CPU build

```bash
docker build -t easyvmaf .
```

### CUDA / GPU build

```bash
docker build -f Dockerfile.cuda -t easyvmaf:cuda .
```

> **Note:** The CUDA image links FFmpeg with `--enable-nonfree` components (nvcc/CUDA). It cannot be legally redistributed — build and use locally only.

### Build arguments

Both Dockerfiles accept these build-time arguments:

| ARG | Default | Description |
|-----|---------|-------------|
| `FFMPEG_version` | `8.1` | FFmpeg release tag (>= 8.1) |
| `VMAF_version` | `3.2.1` | libvmaf release tag (>= 3.2.1) |
| `EASYVMAF_VERSION` | `5.0.0` | easyVmaf version label |
| `DAV1D_version` | `1.4.3` | dav1d release (CUDA image only — built from source) |

```bash
# Custom versions
docker build --build-arg FFMPEG_version=8.1 --build-arg VMAF_version=3.2.1 -t easyvmaf .
```

### Running with Docker

```bash
# CPU
docker run --rm -v /path/to/videos:/videos \
  easyvmaf -d /videos/distorted.mp4 -r /videos/reference.mp4

# With sync
docker run --rm -v /path/to/videos:/videos \
  easyvmaf -d /videos/distorted.mp4 -r /videos/reference.mp4 --sync-window 2

# GPU (requires NVIDIA Container Toolkit; VMAF v0.6 only)
docker run --rm --gpus all -v /path/to/videos:/videos \
  easyvmaf:cuda -d /videos/distorted.mp4 -r /videos/reference.mp4 --vmaf-version 0.6 --gpu

# GPU decoding (any model; add --gpu for v0.6 on the GPU)
docker run --rm --gpus all -v /path/to/videos:/videos \
  easyvmaf:cuda -d /videos/distorted.mp4 -r /videos/reference.mp4 --enable-hwaccel
```

The CUDA image sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`: NVDEC
needs the `video` capability. Without it every input is decoded on the CPU,
with a warning. An image built from an older `Dockerfile.cuda` lacks it:
rebuild it, or pass `-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video` to
`docker run`.

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

---

## Sync examples explained

### Reference delayed relative to distorted

![](https://raw.githubusercontent.com/gdavila/easyVmaf/master/readme/easyVmaf1.svg)

`reference.ts` has 0.7 extra seconds at the start: the first frame of `distorted-A.ts` appears 0.7 seconds into `reference.ts`. Use `--sync-window` to search for the offset automatically:

```bash
easyvmaf -d distorted-A.ts -r reference.ts --sync-window 2
```

The sync window of 2 seconds means easyVmaf searches the first 2 seconds of `reference.ts` for the best PSNR match against the first frames of `distorted-A.ts`.

### Distorted delayed relative to reference

![](https://raw.githubusercontent.com/gdavila/easyVmaf/master/readme/easyVmaf2.svg)

This time `distorted-B.ts` has the extra seconds: the first frame of `reference.ts` appears 8.3 seconds into `distorted-B.ts`. Use `--sync-reverse` to flip the sync direction:

```bash
easyvmaf -d distorted-B.ts -r reference.ts --sync-window 3 --sync-start 6 --sync-reverse
```

With `--sync-reverse`, the window slides over the distorted video: `--sync-start 6 --sync-window 3` searches from 6 to 9 seconds into `distorted-B.ts` for the first frames of `reference.ts`. The reported offset is negative (−8.3), meaning that the distorted video was trimmed.

---

## Known bugs

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

## Migrating from 4.x

easyVmaf 5.0 keeps the models, the scores and the summary schema 2 of 4.0. It
renames two flags, moves the manual offset to its own flag, replaces `--json`
with the summary file and renames the Python API after the CLI flags. See also
[CHANGELOG.md](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md#500-unreleased).

| Contract | 4.x | 5.0 | Migration |
|---|---|---|---|
| Python | >= 3.8 | >= 3.10 | Upgrade Python |
| `--end-sync` | Stops when the shorter video ends | `--shortest` | Rename the flag |
| `--reverse` | Reverse sync search | `--sync-reverse`, requires `--sync-window` | Rename the flag |
| Manual offset | `--sync-start S` without `--sync-window`, negative with `--reverse` | `--sync-offset S`: positive trims the reference, negative the distorted video | `--sync-start 1.5 --reverse` → `--sync-offset -1.5` |
| `--json` | Schema 2 record printed to stdout | Always written to the [summary file](#summary-file) (`<log>_summary.json`) | Read the summary file |
| `docker-compose.yml` | `VIDEO_DIR` defaults to `./video_samples` | `VIDEO_DIR` is required | Set `VIDEO_DIR` |
| `vmaf(mainSrc, refSrc, ...)` | Class `vmaf` | `Vmaf(distorted, reference, ...)` | `from easyvmaf import Vmaf` |
| `vmaf()` arguments | `manual_fps=`, `output_fmt=`, `print_progress=`, `end_sync=`, `gpu_mode=` | `fps=`, `output_format=`, `progress=`, `shortest=`, `gpu=` | Rename the arguments; `gpu=` also in `validate_model_config()` |
| Manual offset (API) | `v.offset = 1.5` | `Vmaf(..., sync_offset=1.5)` | Pass it to the constructor |
| `syncOffset(syncWindow=3, start=0, reverse=False)` | Returns `[offset, psnr]` | `sync(window, start=0, reverse=False)` returns `SyncResult(offset, psnr)`; `window` has no default | Rename the method; pass the window |
| `getVmaf(autoSync=False)` | `autoSync=True` runs `syncOffset()` first | `compute()`, no `autoSync` | Call `sync()` before `compute()` |
| `setOffset()` | Public | Removed | Use `sync_offset` |
| `VmafResult.offset` | Offset applied | `VmafResult.sync_offset` | Rename the field |
| `FFprobe`, `FFmpegQos`, `inputFFmpeg` | Exported by `easyvmaf` | Not exported: the submodules are internal | Use `Vmaf` |

The package also exports `SyncResult`, `ModelRun`, `FFmpegExecutionError`,
`check_ffmpeg`, `validate_range_config` and `UnsupportedRangeError`.

## Migrating from 3.x

easyVmaf 4.0 changes the default model, the CLI flags, the Python API and the
JSON output. Every change has a one-line migration. See also
[CHANGELOG.md](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md).

| Contract | 3.x | 4.0 | Migration |
|---|---|---|---|
| Default model | v0.6 (`vmaf_hd`, `vmaf_hd_neg`, `vmaf_hd_phone`) | v1 (`vmaf_v1_hd`, `vmaf_v1_phone`) | `--vmaf-version 0.6` |
| Requirements | FFmpeg >= 5.0, libvmaf with built-in v0.6 models | FFmpeg >= 8.1, libvmaf >= 3.2.1 with built-in models (verified by computing a v1 frame) | 4.0 Docker image, `brew upgrade libvmaf ffmpeg`, or rebuild libvmaf |
| CLI flags | `-model HD`, `-sw`, `-output_fmt`, ... | `--display hd`, `--sync-window`, `--output-format`, ... | 3.x flags are rejected; see the table below |
| `--gpu` | Any model | Only `--vmaf-version 0.6` | Add `--vmaf-version 0.6` |
| JSON (`-json`) | Printed to stdout: `vmaf.model` and flat scores in `vmaf` | Always written to the [summary file](#summary-file): `schema_version: 2`, `vmaf.display`, `vmaf.scores`, `vmaf.models` | Read scores from `vmaf.scores` of `<distorted>_vmaf_summary.json` |
| Text output | `VMAF HD:`, `VMAF Neg:`, `VMAF Phone:`, `VMAF 4K:` labels | One line per score: name, value, libvmaf model and range | Parse score names, or read the summary file |
| `vmaf(main, ref, output_fmt, model='HD', phone=False, ...)` | `output_fmt` positional, `model`, `phone` | Keyword-only after the paths: `display='hd'`, `vmaf_versions=('1',)`, `views=None`, ...; `output_fmt` defaults to `'json'` | Rename the arguments; `phone` is gone (select with `views`) |
| `vmaf.getVmaf()` | Returns the FFmpeg process | Returns `VmafResult` with scores, models and paths | Read `result.scores` instead of parsing the log |
| `FFmpegQos.getVmaf(model='HD', cambi_heatmap=...)` | Model key | `models`: resolved `ModelRun` list; complete `features` string | Only affects direct use of `easyvmaf.ffmpeg` |
| `VMAF_MODELS`, `HD_MODEL_NAME`, `_4K_MODEL_NAME`, `*_VERSION` | In `easyvmaf.ffmpeg` | Removed; the catalog is `easyvmaf.models.CATALOG` | `from easyvmaf import CATALOG` |
| `check_ffmpeg()['builtin_models']` | Probe of `vmaf_v0.6.1` | `libvmaf_v1` (probe of `vmaf_v1.0.16_3d0h`) | Rename the key |
| `FFmpegQos.vmaf_cambi_heatmap_path` | In layer 1 | `vmaf.cambi_heatmap_path` and `VmafResult.cambi_heatmap_path` | Read it from the result |

Unchanged: the v0.6 score names, the libvmaf log path
(`<distorted>_vmaf.{json,xml,csv}`), the PSNR sync search for progressive
inputs at the same frame rate, duration handling and the libvmaf json/xml/csv
log formats. Interlaced inputs changed: easyVmaf now deinterlaces before
scaling, fixes two deinterlacing cases against a progressive reference, and
keeps sync field accurate. Reverse sync between inputs at different frame
rates now converts the right one, so its reported PSNR changes (see the
changelog).

With `--vmaf-version 0.6`, easyVmaf 4.0 produces the same v0.6 scores as 3.x on
the same libvmaf for progressive inputs; interlaced inputs can score
differently because of those fixes. Moving from libvmaf 3.0.0 to 3.2.1 changed them by at most
0.00002 in our checks. With `--vmaf-version 1 0.6`, the v0.6 models are measured
at 10 bits, which moved them by at most 0.028 when the inputs are scaled. See
the [CHANGELOG](https://github.com/gdavila/easyVmaf/blob/master/CHANGELOG.md) for the full verification.

### Flag equivalences

Only `-d` and `-r` keep their short form. Every other 3.x flag exits with code 2
and names its replacement. After 4.0, `--reverse` became `--sync-reverse`, the
manual offset moved from `--sync-start` without a sync window to `--sync-offset`,
and `--json` was removed: the summary file is always written.

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

New in 4.0: `--vmaf-version`, `--view`, `--hfr`, `--bitdepth`, `--enc-size`,
`--enc-bitdepth` and `--model-option`.

Unique prefixes of long flags (`--sync-w`) are not accepted: write the full name.
