"""
MIT License

Copyright (c) 2020 Gabriel Davila - https://github.com/gdavila

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import argparse
import glob
import json
import logging
import math
import os.path
import re
import sys
from signal import signal, SIGINT

from .ffmpeg import FFmpegExecutionError, check_ffmpeg
from .models import DISPLAY_RESOLUTION, VMAF_VERSIONS
from .vmaf import vmaf, validate_model_config, UnsupportedFramerateError

logger = logging.getLogger(__name__)

JSON_SCHEMA_VERSION = 2

# How validate_model_config() errors name each vmaf() argument
_FLAG_LABELS = {
    'display': '--display',
    'vmaf_versions': '--vmaf-version',
    'views': '--view',
    'hfr': '--hfr',
    'bitdepth': '--bitdepth',
    'enc_size': '--enc-size',
    'enc_bitdepth': '--enc-bitdepth',
    'model_options': '--model-option',
    'gpu_mode': '--gpu',
}

# 3.x flags, removed in 4.0 without aliases: rejected with their 4.0 name
_REMOVED_FLAGS = {
    '-sw': '--sync-window',
    '-ss': '--sync-start',
    '-sync_only': '--sync-only',
    '-reverse': '--reverse',
    '-fps': '--fps',
    '-subsample': '--subsample',
    '-threads': '--threads',
    '-endsync': '--shortest',
    '-output_fmt': '--output-format',
    '-cambi_heatmap': '--cambi-heatmap',
    '-progress': '--progress',
    '-verbose': '--verbose',
    '-json': '--json',
    '-gpu': '--gpu',
    '-model': '--display',
}


def _range(model_run):
    """Score range of a model, with integral bounds as ints: [0, 100]."""
    return [int(v) if float(v).is_integer() else v for v in model_run.spec.score_range]


def _build_result(distorted, reference, offset, psnr, vmaf_result=None):
    """
    Build the structured result dict (JSON schema 2) for one
    distorted/reference pair.

    Args:
        distorted:   path to distorted file
        reference:   path to reference file
        offset:      sync offset in seconds (float)
        psnr:        sync PSNR value (float or None)
        vmaf_result: VmafResult of the calculation, or None for
                     --sync-only runs (no vmaf block)

    Returns:
        dict ready for json.dumps()
    """
    result = {
        'schema_version': JSON_SCHEMA_VERSION,
        'distorted': distorted,
        'reference': reference,
        'sync': {
            'offset': round(offset, 6) if offset is not None else 0.0,
            'psnr':   round(psnr, 6)   if psnr   is not None else None,
        },
    }
    if psnr is not None and not math.isfinite(psnr):
        result['sync']['psnr'] = None
        result['sync']['psnr_status'] = (
            'nan' if math.isnan(psnr) else
            'positive_infinity' if psnr > 0 else 'negative_infinity'
        )
    if vmaf_result is not None:
        vmaf_block = {
            'display': vmaf_result.display,
            'pix_fmt': vmaf_result.pix_fmt,
            'hfr': vmaf_result.hfr,
            'scores': {k: round(v, 6) for k, v in vmaf_result.scores.items()},
            'models': [
                {'name': run.spec.name,
                 'libvmaf_model': run.libvmaf_model,
                 'vmaf_version': run.spec.vmaf_version,
                 'view': run.spec.view,
                 'range': _range(run)}
                for run in vmaf_result.models
            ],
        }
        if vmaf_result.log_path:
            vmaf_block['output_file'] = vmaf_result.log_path
        if vmaf_result.cambi_heatmap_path:
            vmaf_block['cambi_heatmap_path'] = vmaf_result.cambi_heatmap_path
        result['vmaf'] = vmaf_block
    return result


def _print_text_result(distorted, offset, psnr, vmaf_result):
    print("\n \n \n \n \n ")
    print("=======================================", flush=True)
    print("Results:", distorted, flush=True)
    print("=======================================", flush=True)
    print("VMAF computed", flush=True)
    print("=======================================", flush=True)
    print("offset: ", offset, " | psnr: ", psnr)
    print(f"pix_fmt: {vmaf_result.pix_fmt} | HFR: {'on' if vmaf_result.hfr else 'off'}")
    width = max(len(name) for name in vmaf_result.scores)
    for run in vmaf_result.models:
        low, high = _range(run)
        print(f"{run.spec.name:<{width}}  {vmaf_result.scores[run.spec.name]:.6f}  "
              f"[{run.libvmaf_model}, {low}-{high}]", flush=True)
    print("VMAF output file path: ", vmaf_result.log_path)
    if vmaf_result.cambi_heatmap_path:
        print("CAMBI Heatmap output path: ", vmaf_result.cambi_heatmap_path)
    print("\n \n \n \n \n ")


def _print_json_result(result):
    """Serialize the entire record before writing any of it to stdout."""
    try:
        serialized = json.dumps(result, allow_nan=False)
    except ValueError as e:
        print(f"[easyVmaf] ERROR: Cannot serialize result as strict JSON: {e}",
              file=sys.stderr)
        sys.exit(1)
    print(serialized)


def _enc_size(value):
    """argparse type for --enc-size: 'WxH' with positive integers → (W, H)."""
    match = re.fullmatch(r'(\d+)[xX](\d+)', value)
    if not match or not all(int(v) > 0 for v in match.groups()):
        raise argparse.ArgumentTypeError(f"expected WxH, e.g. 1280x720, not '{value}'")
    return int(match.group(1)), int(match.group(2))


def handler(signal_received, frame):
    print('SIGINT or CTRL-C detected. Calculation interrupted.', file=sys.stderr)
    sys.exit(130)


def get_args():
    '''This function parses and return arguments passed in'''
    parser = MyParser(prog='easyVmaf', allow_abbrev=False,
                      description="Script to easy compute VMAF using FFmpeg. It allows to deinterlace, scale and sync Ref and Distorted video samples automatically: \
                        \n\n \t Autodeinterlace: If the Reference or Distorted samples are interlaced, deinterlacing is applied\
                        \n\n \t Autoscale: Reference and Distorted samples are scaled automatically to 1920x1080 or 3840x2160 depending on --display\
                        \n\n \t Autosync: The first frames of the distorted video are used as reference to a sync look up with the Reference video. \
                        \n \t \t The sync is doing by a frame-by-frame look up of the best PSNR\
                        \n \t \t See [--reverse] for more options of syncing\
                        \n\n As output, a json file with VMAF score is created",
                      formatter_class=argparse.RawTextHelpFormatter)

    inputs = parser.add_argument_group('input')
    inputs.add_argument('-d', '--distorted', dest='distorted', type=str, required=True,
                        help='Distorted video, or a glob pattern for batch processing.')
    inputs.add_argument('-r', '--reference', dest='reference', type=str, required=True,
                        help='Reference video.')
    inputs.add_argument('--fps', dest='fps', type=float, default=0,
                        help='Video Frame Rate: force frame rate conversion to <fps> value. Autodeinterlace is disabled when setting this')

    sync = parser.add_argument_group('synchronization')
    sync.add_argument('--sync-window', dest='sync_window', type=float, default=0,
                      help='Sync Window: window size in seconds of a subsample of the Reference video. The sync lookup will be done between the first frames of the Distorted input and this Subsample of the Reference. (default=0. No sync).')
    sync.add_argument('--sync-start', dest='sync_start', type=float, default=0,
                      help="Sync Start Time. Time in seconds from the beginning of the Reference video to which the Sync Window will be applied from. (default=0).")
    sync.add_argument('--sync-only', dest='sync_only', action='store_true',
                      help='Measure sync only for every input. Requires an explicit finite --sync-window greater than zero. No Vmaf processing')
    sync.add_argument('--reverse', dest='reverse', action='store_true',
                      help="If enable, it Changes the default Autosync behaviour: The first frames of the Reference video are used as reference to sync with the Distorted one. (Default = Disable).")
    sync.add_argument('--shortest', dest='shortest', action='store_true',
                      help='Stop when the shorter video ends, instead of repeating its last frame until the longer one ends. Use it when the inputs have different durations. (Default: false).')

    models = parser.add_argument_group('models')
    models.add_argument('--display', dest='display', type=str.lower,
                        choices=tuple(DISPLAY_RESOLUTION), default='hd',
                        help="Target display: inputs are scaled to 1920x1080 (hd) or 3840x2160 (4k). (Default: hd).")
    models.add_argument('--vmaf-version', dest='vmaf_versions', nargs='+',
                        choices=VMAF_VERSIONS, default=['1'],
                        help="VMAF generations to compute: 1 and/or 0.6. '--vmaf-version 1 0.6' computes both in one pass. (Default: 1).")
    models.add_argument('--bitdepth', dest='bitdepth', type=str.lower,
                        choices=('auto', '8', '10'), default='auto',
                        help="Measurement bit depth. auto: 10 with VMAF v1 models, otherwise the reference bit depth. (Default: auto).")

    v1 = parser.add_argument_group('VMAF v1 parameters')
    v1.add_argument('--view', dest='views', nargs='+', type=str.lower,
                    choices=('3h', '5h', 'phone', '1.5h'),
                    help="VMAF v1 viewing distances (phone = 5h). (Default: 3h 5h for hd; 1.5h for 4k).")
    v1.add_argument('--hfr', dest='hfr', type=str.lower,
                    choices=('auto', 'on', 'off'), default='auto',
                    help="VMAF v1 high frame rate models. auto: when the effective frame rate is >= 47 fps. (Default: auto).")
    v1.add_argument('--enc-size', dest='enc_size', type=_enc_size, metavar='WxH',
                    help="VMAF v1 encoding resolution for CAMBI. (Default: distorted video size).")
    v1.add_argument('--enc-bitdepth', dest='enc_bitdepth', type=int, choices=(8, 10, 12),
                    help="VMAF v1 encoding bit depth for CAMBI. (Default: from the distorted pixel format).")
    v1.add_argument('--model-option', dest='model_options', action='append', default=[],
                    type=str.lower,
                    metavar='FEATURE.OPTION=VALUE',
                    help="Advanced VMAF v1 model option override, e.g. cambi.topk=0.5. Repeatable.")

    output = parser.add_argument_group('output')
    output.add_argument('--output-format', dest='output_format', type=str.lower,
                        choices=('json', 'xml', 'csv'), default='json',
                        help='Output vmaf file format. Options: json, xml or csv (Default: json)')
    output.add_argument('--json', dest='json', action='store_true',
                        help='Output final results as JSON to stdout. '
                             'Compatible with --sync-only and full VMAF runs. '
                             '(Default: false).')
    output.add_argument('--cambi-heatmap', dest='cambi_heatmap', action='store_true',
                        help='Activate cambi heatmap. (Default: false).')
    output.add_argument('--verbose', dest='verbose', action='store_true',
                        help='Activate verbose loglevel. (Default: info).')
    output.add_argument('--progress', dest='progress', action='store_true',
                        help='Activate progress indicator for vmaf computation. (Default: false).')

    execution = parser.add_argument_group('execution')
    execution.add_argument('--threads', dest='threads', type=int, default=0,
                           help='Number of parallel sync workers (each runs FFmpeg single-threaded) and libvmaf threads. (default=0, CPU count).')
    execution.add_argument('--subsample', dest='subsample', type=int, default=1,
                           help="Specifies the subsampling of frames to speed up calculation. (default=1, None).")
    execution.add_argument('--gpu', dest='gpu', action='store_true',
                           help='Use GPU-accelerated VMAF computation via libvmaf_cuda. '
                                'Only supports --vmaf-version 0.6. '
                                'Requires FFmpeg built with --enable-nonfree --enable-ffnvcodec '
                                '--enable-libvmaf and libvmaf built with -Denable_cuda=true. '
                                'Use the provided Dockerfile.cuda to build a compatible image. '
                                '(Default: false).')

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()
    if args.sync_only and (not math.isfinite(args.sync_window) or args.sync_window <= 0):
        parser.error('--sync-only requires an explicit finite --sync-window greater than zero')
    for flag, value in (('--sync-window', args.sync_window), ('--sync-start', args.sync_start),
                        ('--fps', args.fps)):
        if not math.isfinite(value) or value < 0:
            parser.error('%s must be finite and greater than or equal to zero' % flag)
    if args.subsample < 1:
        parser.error('--subsample must be an integer of at least 1')
    if args.threads < 0:
        parser.error('--threads must be an integer greater than or equal to zero')
    try:
        validate_model_config(
            args.display, args.vmaf_versions, args.views, args.hfr, args.bitdepth,
            args.enc_size, args.enc_bitdepth, args.model_options, args.gpu,
            labels=_FLAG_LABELS)
    except ValueError as e:
        parser.error(str(e))
    return args


class MyParser(argparse.ArgumentParser):
    def parse_known_args(self, args=None, namespace=None):
        # Checked before parsing: argparse would read '-reverse' as '-r everse'.
        args = sys.argv[1:] if args is None else list(args)
        for arg in args:
            if arg == '--':
                break
            flag = arg.split('=', 1)[0]
            if flag in _REMOVED_FLAGS:
                self.error('%s was removed in easyVmaf 4.0, use %s'
                           % (flag, _REMOVED_FLAGS[flag]))
        return super().parse_known_args(args, namespace)

    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help(sys.stderr)
        sys.exit(2)


def main():
    signal(SIGINT, handler)

    '''reading values from cmdParser'''
    cmdParser = get_args()
    main_pattern = cmdParser.distorted
    reference = cmdParser.reference

    syncWin = cmdParser.sync_window
    ss = cmdParser.sync_start
    fps = cmdParser.fps
    n_subsample = cmdParser.subsample
    reverse = cmdParser.reverse
    display = cmdParser.display
    vmaf_versions = tuple(cmdParser.vmaf_versions)
    views = tuple(cmdParser.views) if cmdParser.views else None
    model_options = tuple(cmdParser.model_options)
    verbose = cmdParser.verbose
    output_fmt = cmdParser.output_format
    threads = cmdParser.threads
    print_progress = cmdParser.progress
    shortest = cmdParser.shortest
    cambi_heatmap = cmdParser.cambi_heatmap
    sync_only = cmdParser.sync_only
    use_json = cmdParser.json
    gpu_mode = cmdParser.gpu

    # Setting verbosity
    if verbose:
        loglevel = "verbose"
    else:
        loglevel = "info"

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s [%(name)s] %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stderr,    # explicit — stdout is reserved for JSON output
    )

    # --- FFmpeg compatibility check ---
    try:
        ffmpeg_info = check_ffmpeg()
    except RuntimeError as e:
        print(f"[easyVmaf] ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    if not ffmpeg_info['meets_minimum']:
        print(
            f"[easyVmaf] ERROR: FFmpeg {ffmpeg_info['version_str']} detected. "
            f"easyVmaf requires FFmpeg >= 8.1 built with --enable-libvmaf. "
            f"Use the easyVmaf Docker image or upgrade FFmpeg.",
            file=sys.stderr, flush=True
        )
        sys.exit(1)

    if not ffmpeg_info['libvmaf_v1']:
        print(
            f"[easyVmaf] ERROR: FFmpeg {ffmpeg_info['version_str']} is installed "
            f"but its libvmaf cannot compute VMAF v1 models. "
            f"easyVmaf requires libvmaf >= 3.2.1 built with '-Dbuilt_in_models=true'. "
            f"Use the easyVmaf Docker image, or upgrade libvmaf "
            f"(e.g. 'brew upgrade libvmaf') and rebuild FFmpeg against it.",
            file=sys.stderr, flush=True
        )
        sys.exit(1)

    logger.info(
        "FFmpeg %s detected. libvmaf VMAF v1 models: available.",
        ffmpeg_info['version_str']
    )

    if gpu_mode:
        if not ffmpeg_info['cuda_vmaf']:
            print(
                "[easyVmaf] ERROR: --gpu requested but libvmaf_cuda filter "
                "is not available in the detected FFmpeg build. "
                "Build FFmpeg using Dockerfile.cuda with "
                "--enable-nonfree --enable-ffnvcodec --enable-libvmaf "
                "and libvmaf with -Denable_cuda=true.",
                file=sys.stderr, flush=True
            )
            sys.exit(1)
        logger.info("GPU mode enabled — using libvmaf_cuda filter.")

    '''
    Distorted video path could be loaded as patterns i.e., "myFolder/video-sample-*.mp4"
    In this way, many computations could be done with just one command line.
    '''
    main_pattern = os.path.expanduser(main_pattern)
    mainFiles = glob.glob(main_pattern)

    if not (os.path.isfile(reference)):
        print("Reference Video file not found:", reference, file=sys.stderr)
        sys.exit(1)

    if len(mainFiles) == 0:
        print("Distorted Video files not found with the given pattern/name:",
              main_pattern, file=sys.stderr)
        sys.exit(1)

    for main in mainFiles:
        '''check if syncWin was set. If true offset is computed automatically, otherwise manual values are used  '''

        try:
            myVmaf = vmaf(main, reference, display=display,
                          vmaf_versions=vmaf_versions, views=views, hfr=cmdParser.hfr,
                          bitdepth=cmdParser.bitdepth, enc_size=cmdParser.enc_size,
                          enc_bitdepth=cmdParser.enc_bitdepth, model_options=model_options,
                          loglevel=loglevel, subsample=n_subsample, output_fmt=output_fmt, threads=threads, print_progress=print_progress, shortest=shortest, manual_fps=fps, cambi_heatmap=cambi_heatmap, gpu_mode=gpu_mode)
            if syncWin > 0:
                offset, psnr = myVmaf.syncOffset(syncWin, ss, reverse)
                if sync_only:
                    if use_json:
                        result = _build_result(
                            distorted=main,
                            reference=reference,
                            offset=offset,
                            psnr=psnr,
                        )
                        _print_json_result(result)
                    else:
                        print(f"offset: {offset} | psnr: {psnr}", flush=True)
                    continue
            else:
                offset = (-ss if reverse else ss) if ss else 0.0
                psnr = None
                myVmaf.offset = offset

            vmaf_result = myVmaf.getVmaf()
        except (FFmpegExecutionError, UnsupportedFramerateError, ValueError) as e:
            print(f"[easyVmaf] ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        if use_json:
            _print_json_result(_build_result(
                distorted=main,
                reference=reference,
                offset=offset,
                psnr=psnr,
                vmaf_result=vmaf_result,
            ))
        else:
            _print_text_result(main, offset, psnr, vmaf_result)


if __name__ == '__main__':
    main()
