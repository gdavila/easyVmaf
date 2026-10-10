# Changelog

## 5.0.0 (unreleased)

easyVmaf 5.0 is the first release published on PyPI (`pipx install easyvmaf`)
and on the GitHub Container Registry (Docker). From this version easyVmaf
follows Semantic Versioning: see [Versioning](README.md#versioning) for what
the contract covers. It requires Python >= 3.10. The models and scores of 4.0
are unchanged. Each breaking change below says how to migrate; users of 3.x
can follow [Migrating from 3.x](README.md#migrating-from-3x).

### Breaking changes

- **`--end-sync` is now `--shortest`.** The flag never synchronized anything:
  it stops the calculation when the shorter input ends (libvmaf `shortest=1`,
  like FFmpeg's `-shortest`). `--end-sync` exits with code 2:
  `error: --end-sync was removed, use --shortest`.
- **`--reverse` is now `--sync-reverse`**, and it requires `--sync-window`.
  `--reverse` exits with code 2: `error: --reverse was removed, use --sync-reverse`.
- **The manual offset moved to `--sync-offset`.** In 4.0, `--sync-start` without
  `--sync-window` was a manual offset whose sign came from `--reverse`. Now
  `--sync-start` and `--sync-reverse` only configure the sync search and require
  `--sync-window`; without it they exit with code 2 and name the equivalent,
  e.g. `--sync-start 1.5 --sync-reverse` → `use --sync-offset -1.5`.
- **`--json` is removed.** Every run writes its result to a summary file next
  to the libvmaf log instead (see New). `--json` exits with code 2 and says so.
- **`--gpu` accepts every model, and also decodes on the GPU.** In 4.x it
  exited with code 2 with a VMAF v1 model (the default) or `--bitdepth 10`;
  now it computes those on the CPU with `libvmaf`, and the summary says
  `"cuda": false`. The inputs are decoded on the GPU where possible (see New).
  The v0.6 scores of 4.x `--gpu` are unchanged, and
  `--gpu --disable-hw-decode` runs exactly the 4.x command.
- **Python 3.8 and 3.9 are no longer supported.** Both are end of life.
- **The Python API is renamed after the CLI flags** (`--sync-offset` is
  `sync_offset=`):

  | 4.x | 5.0 |
  |---|---|
  | `vmaf(mainSrc, refSrc, ...)` | `Vmaf(distorted, reference, ...)` |
  | `manual_fps=` | `fps=` |
  | `v.offset = 1.5` | `Vmaf(..., sync_offset=1.5)` |
  | `output_fmt=` | `output_format=` |
  | `print_progress=` | `progress=` |
  | `end_sync=` | `shortest=` |
  | `gpu_mode=` | `gpu=`; `validate_model_config()` no longer takes it |
  | `syncOffset(syncWindow, start, reverse)` → `[offset, psnr]` | `sync(window, start=0, reverse=False)` → `SyncResult(offset, psnr)` |
  | `getVmaf(autoSync=False)` | `compute()` |
  | `VmafResult.offset` | `VmafResult.sync_offset` |

  - `sync()` returns a `SyncResult` NamedTuple (`offset, psnr = v.sync(2)` still
    works), and `window` has no default (`syncOffset()` defaulted to 3 s).
  - `compute()` has no `autoSync`: call `sync()` first.
  - `setOffset()` is gone: pass `sync_offset=` to the constructor, or call
    `sync()`.
  - `FFprobe`, `FFmpegQos` and `inputFFmpeg` are no longer exported, and the
    submodules (`easyvmaf.vmaf`, `easyvmaf.ffmpeg`, ...) are internal.
  - New exports: `SyncResult`, `ModelRun`, `FFmpegExecutionError`,
    `check_ffmpeg`, `validate_range_config` and `UnsupportedRangeError`.
- **The CUDA image needs an NVIDIA driver >= 570** on the host (545 before):
  it is built on CUDA 12.8.2 instead of 12.3.2.
- **docker-compose requires `VIDEO_DIR`**, the host directory with the videos.
  Its default, `./video_samples`, is no longer in the repository.

### New

- Published on PyPI: `pipx install easyvmaf` or `pip install easyvmaf`. See
  [Installation](README.md#installation).
- Docker images on the GitHub Container Registry, `linux/amd64` only (on Apple
  Silicon they run emulated): `ghcr.io/gdavila/easyvmaf:5.0.0` (also `5.0`,
  `5`, `latest`) and `ghcr.io/gdavila/easyvmaf:5.0.0-cuda` (also `5.0-cuda`,
  `5-cuda`, `latest-cuda`, `cuda`). See [Docker](README.md#docker).
- `--sync-offset S`: signed manual offset. Positive trims the reference,
  negative the distorted video, with the same sign as the reported offset, so
  the offset printed by `--sync-only` can be passed as is. It cannot be combined
  with `--sync-window`.
- Frame ranges: `--start-frame N` and `--frame-count N` measure exactly frames
  `N..N+count-1` of the full calculation, with the same frame numbers and
  scores, without cutting or re-encoding the inputs. Consecutive ranges join
  into the full calculation, so a long video can be split and its ranges
  computed in parallel, on one machine or on several. Each range writes
  `<distorted>_vmaf_f<first>-<last>.<ext>` (and its CAMBI heatmaps to
  `<distorted>_cambi_heatmap_f<first>-<last>/`), and the summary adds
  `vmaf.range`. MP4, MOV, Matroska and WebM inputs only; not yet with
  `--subsample`. See [Frame ranges](README.md#frame-ranges).
- Summary file: every successful input writes its result as JSON (schema 2,
  the fields of the 4.0 `--json` record) next to the libvmaf log:
  `<distorted>_vmaf_summary.json`,
  `<distorted>_vmaf_f<first>-<last>_summary.json` for a range, or
  `<distorted>_sync_summary.json` with `--sync-only`. See
  [Summary file](README.md#summary-file).
- GPU decoding: `--gpu` decodes the inputs with NVDEC, with the same scores as
  decoding on the CPU: H.264 8-bit, HEVC 8/10-bit and VP9 8-bit 4:2:0. Other
  inputs (MPEG-2, AV1, ProRes, FFV1, H.264 High 10, ...), or every input when
  FFmpeg has no NVDEC, are decoded on the CPU with a warning. Scaling,
  deinterlacing and bit depth conversion stay on the CPU. Two opt-outs, valid
  only with `--gpu`: `--disable-hw-decode` (decode on the CPU) and
  `--disable-vmaf-cuda` (compute VMAF on the CPU). Works with frame ranges.
  See [GPU](README.md#gpu).
- The summary adds `vmaf.cuda`, `true` when VMAF was computed on the GPU
  (`libvmaf_cuda`), and, with GPU decoding, `vmaf.hw_decode`, which says per
  input whether it was decoded on the GPU (`"hw"`) or the CPU (`"sw"`).
  `schema_version` stays 2.
- The CUDA image:
  - is 0.6 GB instead of 7.4 GB;
  - can be redistributed: its FFmpeg is LGPL, without nonfree components. The
    frames and scores are unchanged;
  - enables NVDEC in the container without `-e NVIDIA_DRIVER_CAPABILITIES`.
- Both images carry the license texts of FFmpeg, libvmaf and, in the CUDA
  image, dav1d and nv-codec-headers in `/usr/local/share/licenses`, and
  `ffmpeg/SOURCE` there says where FFmpeg's source code is.

### Fixed

- The sync search no longer leaves `stats_file_psnr.log` in the working
  directory, and no longer fails when that directory is not writable.
- `--cambi-heatmap` no longer mixes the heatmaps of two runs at different
  encoding sizes (another `--enc-size`, or VMAF v1 vs `--vmaf-version 0.6`) in
  `<distorted>_cambi_heatmap/`.
- When FFmpeg cannot run, is older than 8.1 or its libvmaf cannot compute VMAF
  v1, the error says how to install a suitable FFmpeg (Homebrew on macOS, the
  BtbN static build on Linux) and links the README.
- The CUDA image builds again, and its `easyvmaf` command exists: the package
  was installed as `UNKNOWN-0.0.0`.

## 4.0.0

easyVmaf 4.0 computes VMAF v1 by default. The VMAF v0.6 models of 3.x remain
available with `--vmaf-version 0.6`. See [Migrating from 3.x](README.md#migrating-from-3x)
for the one-line migration of each change.

### Breaking changes

- **VMAF v1 is the default.** Without model flags, easyVmaf computes
  `vmaf_v1_hd` and `vmaf_v1_phone` (`vmaf_v1_4k` with `--display 4k`) instead of
  `vmaf_hd`, `vmaf_hd_neg` and `vmaf_hd_phone`. v1 and v0.6 scores are not
  comparable. Use `--vmaf-version 0.6` for the 3.x models, or
  `--vmaf-version 1 0.6` to compute both in one pass.
- **New requirements: FFmpeg >= 8.1 and libvmaf >= 3.2.1** built with
  `-Dbuilt_in_models=true`. At startup, easyVmaf computes one frame with
  `vmaf_v1.0.16_3d0h` and exits with an error if that fails. libvmaf 3.2.0 is not
  enough: a default 3.2.0 build loads the v1 models but cannot compute them.
- **3.x CLI flags are removed, without aliases.** Every option is now
  `--kebab-case`; only `-d` and `-r` keep their short form (and gain
  `--distorted` and `--reference`). A 3.x flag exits with code 2 and names its
  replacement, e.g. `error: -sw was removed in easyVmaf 4.0, use --sync-window`.
  `-model HD|4K` becomes `--display hd|4k`. Unique prefixes of long flags are no
  longer accepted. Option values are case-insensitive (`--display 4K`,
  `--output-format XML`); in 3.x `-output_fmt` only took lowercase.
- **`--gpu` only supports `--vmaf-version 0.6`.** libvmaf 3.2.1 has no CUDA
  extractors for the v1 features. `--gpu` with any v1 model, including the
  default, is a usage error.
- **JSON schema 2.** Every `--json` record has `schema_version: 2`, including
  `--sync-only` records. The `vmaf` block replaces `model` and the flat score
  keys with `display`, `pix_fmt`, `hfr`, `scores` and `models` (name, libvmaf
  model id, VMAF version, view, range per score).
- **Text output** prints one line per score with its value, libvmaf model and
  range (`vmaf_v1_hd     93.256258  [vmaf_v1.0.16_3d0h, 0-100]`), plus the
  measurement `pix_fmt` and HFR state, instead of the `VMAF HD:`, `VMAF Neg:`,
  `VMAF Phone:` and `VMAF 4K:` labels.
- **Python API.**
  - `vmaf(mainSrc, refSrc, *, display='hd', vmaf_versions=('1',), views=None,
    hfr='auto', bitdepth='auto', enc_size=None, enc_bitdepth=None,
    model_options=(), output_fmt='json', ...)`: every argument after the two
    paths is keyword-only. `model` and `phone` are removed; `output_fmt` is no
    longer positional.
  - `vmaf.getVmaf()` returns a `VmafResult` (scores, models, display, pix_fmt,
    hfr, log path, CAMBI heatmap path, offset) instead of the FFmpeg process.
    Reading the libvmaf log moved from the CLI to layer 2 (`easyvmaf.results`).
  - `FFmpegQos.getVmaf(models, ..., features=None, gpu=False)` takes a list of
    resolved `ModelRun` and a complete `feature=` string; `model` and
    `cambi_heatmap` are removed. `FFmpegQos.vmaf_cambi_heatmap_path` moved to
    `vmaf.cambi_heatmap_path`.
  - `VMAF_MODELS`, `HD_MODEL_NAME`, `HD_NEG_MODEL_NAME`, `HD_PHONE_MODEL_NAME`,
    `_4K_MODEL_NAME` and the `*_VERSION` constants are removed from
    `easyvmaf.ffmpeg`. The model catalog is `easyvmaf.models.CATALOG`.
  - `check_ffmpeg()` returns `libvmaf_v1` instead of `builtin_models`, and
    `meets_minimum` now means FFmpeg >= 8.1.

### New

- Model catalog (`easyvmaf.models`): the 4 VMAF v1 models and the 4 v0.6 models
  as `ModelSpec` entries, each v1 model with its HFR variant. `select_models()`
  picks them by display, VMAF version and view.
- `--display {hd,4k}`, `--vmaf-version {1,0.6} [...]` and `--view
  {3h,5h,phone,1.5h} [...]`. With `--display 4k`, `--view 3h` adds
  `vmaf_v1_4k_3h`, whose range is [0, 110].
- `--hfr {auto,on,off}`: HFR v1 models when the effective frame rate after
  deinterlacing or `--fps` is at least 47 fps, with a warning above 60 fps.
- Pixel format normalization: both inputs are measured in the chroma
  subsampling of the reference, at 10 bits when any v1 model is computed
  (`--bitdepth {auto,8,10}`). The conversion is the first filter of each chain
  that needs it, so the reference is never converted to a lower format. The
  format used is reported as `pix_fmt`.
- CAMBI encoding parameters: the distorted video size and bit depth are passed to
  every v1 model as `cambi.enc_width`, `cambi.enc_height` and
  `cambi.enc_bitdepth`. Overrides: `--enc-size WxH`, `--enc-bitdepth {8,10,12}`.
- `--model-option feature.option=value` (repeatable) for advanced v1 model
  overrides, validated so that it cannot inject into the FFmpeg filter graph.
- `--cambi-heatmap` with v1 models writes the heatmaps of the first v1 model.
  Distorted paths whose heatmap directory contains `:`, `|`, `\` or `'` are
  rejected with a clear error, since libvmaf cannot receive them escaped.
- `--help` groups the options into input, synchronization, models, VMAF v1
  parameters, output and execution.
- `easyvmaf` exports `VmafResult`, `ModelSpec`, `CATALOG`, `select_models`,
  `validate_model_config` and `UnsupportedModelConfigError`.
- Docker images pin libvmaf 3.2.1 (FFmpeg 8.1).

### Fixed

- Low ABR rungs no longer fail with VMAF v1. libvmaf 3.2.1 CAMBI rejects
  encoding sizes below 180x150, or with both sides below 216, which broke the
  default calculation for distorted videos such as 256x144, 192x108 or 160x90.
  easyVmaf now raises the encoding size to the smallest accepted size with the
  same aspect ratio (256x144 → 267x150) and logs a warning. The same applies to
  `--enc-size`.
- `UnsupportedFramerateError` now suggests `--fps`.
- Progressive reference at twice the frame rate of an interlaced distorted
  video (50p vs 1080i25, 59.94p vs 1080i29.97): the distorted video is
  deinterlaced to one frame per field and every field is scored. Since 2020
  (`393b950`) an `fps` filter dropped the second field, so only half of the
  pictures were measured (scores about 1-2 VMAF higher in the clip measured),
  and VMAF v1 did not pick the HFR models.
- Progressive reference vs an interlaced distorted video that ffprobe reports
  at its field rate (25p vs 1080i25 reported as 50, as with H.264 PAFF): the
  distorted video is deinterlaced to one frame per frame. Since `d94297e`
  (2026-03) it was deinterlaced per field, pairing half of its pictures with
  the wrong reference instant (about -25 VMAF in the clip measured).
- Interlaced inputs are deinterlaced before scaling. Scaling first blended the
  two fields of an SD or 720i input (or a 1080i input with `--display 4k`)
  before `yadif` separated them: a 720i29.97 distorted video against its
  720p59.94 reference scored 56.6 (v0.6) / 54.3 (v1) instead of 78.7 / 80.5.
  The v0.6 FFmpeg command now puts `fps` before `scale` for every input; for
  progressive inputs the frames and scores are identical.
- `--reverse` sync is field accurate on interlaced inputs. The sync workers
  trimmed the searched video before deinterlacing, unlike the final
  calculation: the right offset tied with the one a field earlier, which won,
  and every picture was then compared with the previous field (about 40 VMAF
  instead of 84 in the clip measured). Workers now build their chains like the
  final calculation and trim after them; a pre-trim that keeps timestamps
  avoids filtering the frames well before each offset.
- `--reverse` sync converts the frame rate of the right input when both inputs
  are progressive (or both interlaced) at different rates (50p reference vs
  25p distorted). The `fps` filter for the reference landed on the distorted
  video, so half of the comparisons were 20 ms off: same offset in the clip
  measured, but a sync PSNR of 30.4 instead of 41.7.
- A sync window past the end of the searched video (the reference, or the
  distorted video with `--reverse`) is rejected with a clear error instead of
  an `IndexError` traceback.

### Migration verification

The tolerance for the v0.6 scores is 0.1 VMAF points on the mean of each video:
about five times the largest difference libvmaf itself records between 3.0.0 and
3.2.1, and twenty times below the lowest JND estimate (2 points). It is a
one-time check of the transition, not part of the test suite.

**libvmaf 3.0.0 → 3.2.1** (same easyVmaf 3.x code, FFmpeg 8.1 Docker images,
`BBB_sampleA_distorted.mp4` vs `BBB_reference_10s.mp4`):

| Run | Metric | libvmaf 3.0.0 | libvmaf 3.2.1 | Δ |
|---|---|---|---|---|
| `-sw 2` (offset 1.5 s) | vmaf_hd | 90.868505 | 90.868493 | -0.000012 |
| `-sw 2` | vmaf_hd_neg | 88.989537 | 88.989525 | -0.000012 |
| `-sw 2` | vmaf_hd_phone | 99.911627 | 99.911630 | +0.000003 |
| `-sw 2` | vmaf_4k | 88.484956 | 88.484940 | -0.000016 |
| no sync | vmaf_hd | 8.877029 | 8.877024 | -0.000005 |
| no sync | vmaf_hd_neg | 8.742868 | 8.742864 | -0.000004 |
| no sync | vmaf_hd_phone | 16.121366 | 16.121360 | -0.000006 |
| no sync | vmaf_4k | 28.617225 | 28.617205 | -0.000020 |

Maximum |Δ|: 0.00002.

**easyVmaf 3.x → 4.0** (same environment, libvmaf 3.2.1; the sample pair plus
two x264 encodes of `BBB_reference_10s.mp4`):

| Video | Metric | 3.x | 4.0 `--vmaf-version 0.6` | 4.0 `--vmaf-version 1 0.6` | Δ 3.x → `0.6` | Δ `0.6` → `1 0.6` |
|---|---|---|---|---|---|---|
| sampleA (sync 2 s) | vmaf_hd | 90.868493 | 90.868493 | 90.868493 | 0 | 0 |
| sampleA | vmaf_hd_neg | 88.989525 | 88.989525 | 88.989525 | 0 | 0 |
| sampleA | vmaf_hd_phone | 99.911630 | 99.911630 | 99.911630 | 0 | 0 |
| sampleA | vmaf_4k | 88.484940 | 88.484940 | 88.497486 | 0 | 0.012546 |
| x264 540p (crf 32) | vmaf_hd | 49.363333 | 49.363333 | 49.360900 | 0 | 0.002433 |
| x264 540p | vmaf_hd_neg | 47.818348 | 47.818348 | 47.816287 | 0 | 0.002061 |
| x264 540p | vmaf_hd_phone | 69.702369 | 69.702369 | 69.699812 | 0 | 0.002557 |
| x264 540p | vmaf_4k | 45.577244 | 45.577244 | 45.548972 | 0 | 0.028272 |
| x264 1080p (crf 35) | vmaf_hd | 70.598923 | 70.598923 | 70.598923 | 0 | 0 |
| x264 1080p | vmaf_hd_neg | 68.607921 | 68.607921 | 68.607921 | 0 | 0 |
| x264 1080p | vmaf_hd_phone | 88.410700 | 88.410700 | 88.410700 | 0 | 0 |
| x264 1080p | vmaf_4k | 67.089964 | 67.089964 | 67.073690 | 0 | 0.016274 |

`--vmaf-version 0.6` reproduces the 3.x scores exactly. With
`--vmaf-version 1 0.6` the v0.6 models are measured at 10 bits; the scores move
only when the inputs are scaled (`vmaf_4k` always is; 540p → 1080p), by at most
0.028. All differences are within the 0.1 tolerance.
