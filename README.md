# easyVmaf

Python tool based on ffmpeg and ffprobe to deal with the video preprocesing required for VMAF inputs:
* Deinterlacing
* Upscaling/downscaling
* Frame-to-Frame Syncing
* Frame rate adaptation

Details about **How it Works** can be found [here](https://gdavila.github.io/broadcast/Vmaf/Vmaf/).

## Requirements

* `Linux`/`OSX`

* Python `>= v3.0`. Libraries: `json`, `varname`

* FFmpeg build with `libvmaf`. More details [here](http://underpop.online.fr/f/ffmpeg/help/libvmaf.htm.gz)

## Installation

Just clone the repo and run it from the source folder.

```bash
$ git clone https://github.com/gdavila/easyVmaf.git
$ cd easyVmaf
```

## Examples

1. VMAF computation for two video samples (`reference.ts` and `distorted.ts`). Both videos are not synced: `reference.ts` is delayed in comparition with `distorted.ts`, i.e.,  The first frame of `distorted.ts` matchs with the frame located at 0.7007 seconds since the begining of `reference.ts` video. To sync the videos automatically, a synchronisation windows of *2 seconds* is applied, this means that the sync lookup will be done betwwen the first frames in `distorted.ts` and a subsample of `reference.ts` of *2 seconds* lenght since it begins. Additionally, `reference.ts` is interlaced (1920x1080@29.97i)  and `distorted.ts` is progressive (960x540@29.97p) with diferent resolutions.

    ```bash
    $ python3 easyVmaf.py -d distorted.ts -r reference.ts -sw 2


    ...
    ...
    [Ignored FFmpeg outputs]
    ...
    ...

    Sync Info:
    offset:  0.7007000000000001 psnr:  48.863779
    VMAF score:  89.37913542219542
    VMAF json File Path:  main_vmaf.json
    ```

2. VMAF computation for two video samples (`reference.ts` and `distorted.ts`). Both videos are not synced but this time,  `distorted.ts` is delayed in comparition with `reference.ts`, i.e.,  The first frame of `reference.ts` matchs with the frame located at 8.3003 seconds since the begining of `distorted.ts` video. To sync the videos automatically, a synchronisation windows of *3 seconds*, a *sync start time* of 6 seconds and *reverse* flag is applied, this means that the sync lookup will be done between the first frames in  `reference.ts` and a `distorted.ts` subsample of *3 seconds* lenght taken from 6 seconds of its begin.

    ```bash
    $ python3 easyVmaf.py -d distorted.ts -r reference.ts -sw 3 -ss 6 -reverse


    ...
    ...
    [Ignored FFmpeg outputs]
    ...
    ...

    Sync Info:
    offset:  8.300300000000000 psnr:  34.897866
    VMAF score:  92.34452778643345
    VMAF json File Path:  main_vmaf.json
    ```
