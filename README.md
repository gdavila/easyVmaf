# easyVmafPlus

easyVmafPlus is an enhanced fork of [easyVmaf](https://github.com/gdavila/easyVmaf) by Gabriel Davila. It is a Python tool built on FFmpeg and ffprobe that prepares a reference and a distorted video for [VMAF](https://github.com/Netflix/vmaf). It handles deinterlacing, upscaling and downscaling, frame-to-frame syncing and frame rate adaptation.

Details about how the original tool works can be found in [this OTTVerse article](https://ottverse.com/vmaf-easyvmaf/).

## What easyVmafPlus adds

| Change | Details |
|---|---|
| Hardware accelerated decoding | Every ffmpeg run passes `-hwaccel auto` to both the distorted and the reference input. When no hardware decoder is available, FFmpeg decodes in software. |
| `easyVmafPlus` command | `easyVmafPlus.sh` starts the tool from any directory. `install_symlink.sh` and `uninstall_symlink.sh` add and remove an `easyVmafPlus` symlink on your `PATH`. |
| Own Docker image | The Dockerfile builds FFmpeg and libvmaf from source and copies the code from this repository. A GitHub Actions workflow publishes the image to the GitHub Container Registry for `linux/amd64` and `linux/arm64`. |

### Fixes

| Area | Fix |
|---|---|
| Relative paths | `easyVmafPlus.sh` runs the tool from the directory you call it in, so relative `-d` and `-r` paths work. |
| Installer | `install_symlink.sh` stops with an error when no writable directory is found, instead of trying to create `/easyVmafPlus`. |
| Uninstaller | `uninstall_symlink.sh` also searches the `PATH` directories the installer can fall back to. |
| Docker build on `linux/amd64` | libvmaf is installed to `/usr/local/lib` on every platform, so the FFmpeg build finds it. Before, it landed in `/usr/local/lib64` on amd64 and FFmpeg's configure step failed. |

## Features from easyVmaf

easyVmafPlus keeps the features of the original tool:

* [Cambi](https://github.com/Netflix/vmaf/blob/master/resource/doc/cambi.md#options), the Netflix banding detector, is computed on every run.
* [Cambi heatmaps](https://github.com/Netflix/vmaf/issues/936) can be written with `-cambi_heatmap`. They can be viewed with [ffplay](https://github.com/Netflix/vmaf/issues/1016#issuecomment-1099591977).
* The command line follows the [libvmaf filter documentation](https://ffmpeg.org/ffmpeg-filters.html#libvmaf).
* The VMAF models are the built-in models of FFmpeg 5.0 and later.
* With the HD model, the HD, HD Neg and HD Phone scores are computed in one run.

## Requirements

| Requirement | Notes |
|---|---|
| Linux or macOS | |
| Python 3 | Verified with Python 3.8 (Docker image) and Python 3.14 (macOS) |
| Python module [ffmpeg-progress-yield](https://github.com/slhck/ffmpeg-progress-yield) | `pip3 install ffmpeg-progress-yield` |
| FFmpeg 5.0 or later, built with libvmaf | Since easyVmaf 2.0 only FFmpeg 5.0 and later is supported. For older FFmpeg versions, use easyVmaf 1.3. |

## Installation

```bash
git clone https://github.com/marcelpoelstra/easyVmafPlus.git
cd easyVmafPlus
pip3 install ffmpeg-progress-yield
```

You can now run `python3 easyVmafPlus.py` from the repository directory.

To use the `easyVmafPlus` command from any directory, run the installer from the repository root. It links to `easyVmafPlus.sh` in the current directory.

```bash
./install_symlink.sh
```

The installer uses the first writable directory of `/usr/local/bin`, `/usr/bin`, `~/bin` and `~/.local/bin`. If none of these is writable, it uses the first writable directory on your `PATH`, skipping `/bin`, `/sbin`, `/usr/bin` and `/usr/sbin`. It refuses to overwrite an existing `easyVmafPlus` file or link.

To remove the command again, run the uninstaller. It asks for confirmation before it removes the link.

```bash
./uninstall_symlink.sh
```

## Usage

```console
$ easyVmafPlus -h
usage: easyVmafPlus [-h] -d D -r R [-sw SW] [-ss SS] [-fps FPS] [-subsample N]
                    [-reverse] [-model MODEL] [-threads THREADS] [-verbose]
                    [-progress] [-endsync] [-output_fmt OUTPUT_FMT]
                    [-cambi_heatmap] [-sync_only]

Script to easy compute VMAF using FFmpeg. It allows to deinterlace, scale and sync Ref and Distorted video samples automatically:

 	 Autodeinterlace: If the Reference or Distorted samples are interlaced, deinterlacing is applied

 	 Autoscale: Reference and Distorted samples are scaled automatically to 1920x1080 or 3840x2160 depending on the VMAF model to use

 	 Autosync: The first frames of the distorted video are used as reference to a sync look up with the Reference video.
 	 	 The sync is doing by a frame-by-frame look up of the best PSNR
 	 	 See [-reverse] for more options of syncing

 As output, a json file with VMAF score is created

options:
  -h, --help            show this help message and exit
  -sw SW                Sync Window: window size in seconds of a subsample of the Reference video. The sync lookup will be done between the first frames of the Distorted input and this Subsample of the Reference. (default=0. No sync).
  -ss SS                Sync Start Time. Time in seconds from the beginning of the Reference video to which the Sync Window will be applied from. (default=0).
  -fps FPS              Video Frame Rate: force frame rate conversion to <fps> value. Autodeinterlace is disabled when setting this
  -subsample N          Specifies the subsampling of frames to speed up calculation. (default=1, None).
  -reverse              If enable, it Changes the default Autosync behaviour: The first frames of the Reference video are used as reference to sync with the Distorted one. (Default = Disable).
  -model MODEL          Vmaf Model. Options: HD, 4K. (Default: HD).
  -threads THREADS      number of threads
  -verbose              Activate verbose loglevel. (Default: info).
  -progress             Activate progress indicator for vmaf computation. (Default: false).
  -endsync              Activate end sync. This ends the computation when the shortest video ends. (Default: false).
  -output_fmt OUTPUT_FMT
                        Output vmaf file format. Options: json or xml (Default: json)
  -cambi_heatmap        Activate cambi heatmap. (Default: false).
  -sync_only            For sync measurement only. No Vmaf processing

required arguments:
  -d D                  Distorted video
  -r R                  Reference video
```

`-d` accepts a glob pattern, for example `"myFolder/video-sample-*.mp4"`. easyVmafPlus then computes VMAF for every matching file against the same reference.

### Output files

| File | Location |
|---|---|
| VMAF log, `<distorted name>_vmaf.json` (or `_vmaf.xml` with `-output_fmt xml`) | Next to the distorted video |
| CAMBI heatmap, `<distorted name>_cambi_heatmap` (with `-cambi_heatmap`) | Next to the distorted video |
| `stats_file_psnr.log`, written during sync | The directory you run the command from |

## Sync examples

The examples use the samples in `video_samples/`. Run them from that directory.

### Reference delayed against the distorted video

![Sync window applied to the reference video](readme/easyVmaf1.svg)

The first frame of `BBB_sampleA_distorted.mp4` matches the frame at 1.5 seconds in `BBB_reference_10s.mp4`. With `-sw 1 -ss 1`, easyVmafPlus searches a sync window of 1 second, starting 1 second into the reference. It picks the offset with the highest PSNR.

```console
$ easyVmafPlus -r BBB_reference_10s.mp4 -d BBB_sampleA_distorted.mp4 -sw 1 -ss 1
...
VMAF computed
=======================================
offset:  1.5  | psnr:  40.032121
VMAF HD:  90.8878725125
VMAF Neg:  89.00986847916667
VMAF Phone:  99.91903107083333
VMAF output file path:  BBB_sampleA_distorted_vmaf.json
```

### Distorted video delayed against the reference

![Sync window applied to the distorted video](readme/easyVmaf2.svg)

Here the first frame of the reference `BBB_sampleA_distorted.mp4` matches the frame at 1.0 second in the distorted `BBB_sampleB_distorted.mp4`. The `-reverse` flag applies the sync window to the distorted video instead of the reference. The offset is then reported as a negative value.

```console
$ easyVmafPlus -r BBB_sampleA_distorted.mp4 -d BBB_sampleB_distorted.mp4 -sw 2 -ss 0 -reverse
...
VMAF computed
=======================================
offset:  -1.0  | psnr:  37.254979
VMAF HD:  56.162353304166665
VMAF Neg:  54.50033624583333
VMAF Phone:  74.858480325
VMAF output file path:  BBB_sampleB_distorted_vmaf.json
```

## Docker image

The image `ghcr.io/marcelpoelstra/easyvmafplus` is published to the GitHub Container Registry for `linux/amd64` and `linux/arm64`. It is based on `python:3.8-slim`, with FFmpeg and libvmaf built from source. See the [Dockerfile](Dockerfile) for details.

| Tag | Published on |
|---|---|
| `latest` | Every push to `master` |
| `sha-<commit>` | Every push to `master`, with the short commit hash |
| `<version>` | Every version tag `v*`, for example `v1.2.3` publishes `1.2.3` |

To analyse your own files, mount the folder that holds them. The VMAF log is written next to the distorted video, so it ends up in the same folder.

```bash
docker run --rm -v <local-path-to-your-video-files>:/<custom-name-folder> ghcr.io/marcelpoelstra/easyvmafplus -r /<custom-name-folder>/video-1.mp4 -d /<custom-name-folder>/video-2.mp4
```

The image contains the samples from `video_samples/`:

```text
NAME                        TIME

                           t=0
                            |
BBB_reference_10s.mp4       */-----------------------------*/
BBB_sampleA_distorted.mp4           */---------------------*/
BBB_sampleB_distorted.mp4       */-------------------------*/
```

VMAF between `BBB_reference_10s.mp4` and `BBB_sampleA_distorted.mp4`:

```bash
docker run --rm ghcr.io/marcelpoelstra/easyvmafplus -r video_samples/BBB_reference_10s.mp4 -d video_samples/BBB_sampleA_distorted.mp4 -sw 1 -ss 1
```

VMAF between `BBB_sampleA_distorted.mp4` and `BBB_sampleB_distorted.mp4`:

```bash
docker run --rm ghcr.io/marcelpoelstra/easyvmafplus -r video_samples/BBB_sampleA_distorted.mp4 -d video_samples/BBB_sampleB_distorted.mp4 -sw 2 -ss 0 -reverse
```

### Building the image yourself

```bash
docker build -t easyvmafplus .
docker build --platform linux/amd64 -t easyvmafplus:amd64 .
```

On an Apple silicon Mac, the `linux/amd64` build runs under QEMU emulation and takes much longer than a native build. QEMU 7.0.0 crashed the compiler during this build. QEMU 10.2.3 from `tonistiigi/binfmt` builds it correctly:

```bash
docker run --privileged --rm tonistiigi/binfmt --uninstall qemu-x86_64
docker run --privileged --rm tonistiigi/binfmt --install amd64
```

### Publishing

The workflow `.github/workflows/docker-publish.yml` builds and pushes the image. It uses Docker's reusable [github-builder](https://github.com/docker/github-builder) workflow, which builds each platform on a native GitHub-hosted runner. A newly published GitHub package is private by default. Set its visibility in the package settings on GitHub.

## Licence

MIT, see [LICENSE](LICENSE). The original easyVmaf is written by Gabriel Davila.
