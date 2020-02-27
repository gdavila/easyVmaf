# easyVmaf

Python tool based on ffmpeg and ffprobe to deal with the video preprocesing required for VMAF inputs:
* Deinterlacing
* Upscaling/downscaling
* Frame-to-Frame Syncing
* Frame rate adaptation

Details about **How it Works** can be found [here](https://gdavila.github.io/broadcast/Vmaf/Vmaf/).

# Requirements

* `Linux`/`OSX`

* Python `>= v3.0`. Libraries: `json`, `varname`

* FFmpeg build with `libvmaf`. More details [here](http://underpop.online.fr/f/ffmpeg/help/libvmaf.htm.gz)

# Installation

Just clone the repo and run it from the source folder.

```bash
$ git clone https://github.com/gdavila/easyVmaf.git
$ cd easyVmaf
```

# Examples

* Help
  
    ```
    $ python3 eVmaf.py -h
    usage: eVmaf [-h] [-i I] [-r R] [-sw SW] [-ss SS] [-reverse] [-model MODEL]
                [-phone] [-verbose]

    script to easy compute VMAF using FFmpeg. It allows to deinterlace, scale and
    sync Ref and Distorted videos

    optional arguments:
    -h, --help    show this help message and exit
    -i I          main video sample to compute VMAF (distorted)
    -r R          Reference video sample to compute VMAF
    -sw SW        synchronisation windows in seconds(default=0)
    -ss SS        start time to sync(default=0).It specifies in seconds where
                    the sync Windows begin. If [-r] is dissabled [ss] it is for
                    reference video, otherwise it is for main video
    -reverse      Vmaf Model. Options: HD, 4K. Default: HD
    -model MODEL  Vmaf Model. Options: HD, 4K. Default: HD
    -phone        Activate phone vmaf (HD only)
    -verbose      Activate verbose loglevel. Default: info

    ```

* VMAF computation for two video samples (ref.ts and main.ts) with a synchronisation windows of 2 seconds. The ref video is interlaced (1920x1080@29.97i)  and the main video is progressive (960x540@29.97p)

    ```bash
    $ python3 eVmaf.py -i main.mp4 -r ref.ts' -sw 2 


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