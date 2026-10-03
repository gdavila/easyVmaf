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
import csv
import glob
import json
import logging
import math
import os.path
import sys
import xml.etree.ElementTree as ET
from signal import signal, SIGINT
from statistics import mean, harmonic_mean

from .ffmpeg import FFmpegExecutionError, check_ffmpeg, HD_MODEL_NAME, HD_NEG_MODEL_NAME, HD_PHONE_MODEL_NAME, _4K_MODEL_NAME, HD_PHONE_MODEL_VERSION
from .vmaf import vmaf, UnsupportedFramerateError

logger = logging.getLogger(__name__)


def _build_result(distorted, reference, offset, psnr, model,
                  vmaf_scores=None, vmaf_output_file=None,
                  cambi_heatmap_path=None):
    """
    Build the structured result dict for one distorted/reference pair.

    Args:
        distorted:          path to distorted file
        reference:          path to reference file
        offset:             sync offset in seconds (float)
        psnr:               sync PSNR value (float or None)
        model:              'HD' or '4K'
        vmaf_scores:        dict of metric_name → mean score, or None
                            for --sync_only runs
        vmaf_output_file:   path to VMAF output file, or None
        cambi_heatmap_path: path to CAMBI heatmap output, or None

    Returns:
        dict ready for json.dumps()
    """
    result = {
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
    if vmaf_scores is not None:
        vmaf_block = {'model': model}
        vmaf_block.update({k: round(v, 6) for k, v in vmaf_scores.items()})
        if vmaf_output_file:
            vmaf_block['output_file'] = vmaf_output_file
        if cambi_heatmap_path:
            vmaf_block['cambi_heatmap_path'] = cambi_heatmap_path
        result['vmaf'] = vmaf_block
    return result


def _print_json_result(result):
    """Serialize the entire record before writing any of it to stdout."""
    try:
        serialized = json.dumps(result, allow_nan=False)
    except ValueError as e:
        print(f"[easyVmaf] ERROR: Cannot serialize result as strict JSON: {e}",
              file=sys.stderr)
        sys.exit(1)
    print(serialized)


def handler(signal_received, frame):
    print('SIGINT or CTRL-C detected. Calculation interrupted.', file=sys.stderr)
    sys.exit(130)


def get_args():
    '''This function parses and return arguments passed in'''
    parser = MyParser(prog='easyVmaf',
                      description="Script to easy compute VMAF using FFmpeg. It allows to deinterlace, scale and sync Ref and Distorted video samples automatically: \
                        \n\n \t Autodeinterlace: If the Reference or Distorted samples are interlaced, deinterlacing is applied\
                        \n\n \t Autoscale: Reference and Distorted samples are scaled automatically to 1920x1080 or 3840x2160 depending on the VMAF model to use\
                        \n\n \t Autosync: The first frames of the distorted video are used as reference to a sync look up with the Reference video. \
                        \n \t \t The sync is doing by a frame-by-frame look up of the best PSNR\
                        \n \t \t See [-reverse] for more options of syncing\
                        \n\n As output, a json file with VMAF score is created",
                      formatter_class=argparse.RawTextHelpFormatter)
    requiredgroup = parser.add_argument_group('required arguments')
    requiredgroup.add_argument(
        '-d', dest='d', type=str, help='Distorted video', required=True)
    requiredgroup.add_argument(
        '-r', dest='r', type=str, help='Reference video ', required=True)
    parser.add_argument('-sw', dest='sw', type=float, default=0,
                        help='Sync Window: window size in seconds of a subsample of the Reference video. The sync lookup will be done between the first frames of the Distorted input and this Subsample of the Reference. (default=0. No sync).')
    parser.add_argument('-ss', dest='ss', type=float, default=0,
                        help="Sync Start Time. Time in seconds from the beginning of the Reference video to which the Sync Window will be applied from. (default=0).")
    parser.add_argument('-fps', dest='fps', type=float, default=0,
                        help='Video Frame Rate: force frame rate conversion to <fps> value. Autodeinterlace is disabled when setting this')
    parser.add_argument('-subsample', dest='n', type=int, default=1,
                        help="Specifies the subsampling of frames to speed up calculation. (default=1, None).")
    parser.add_argument('-reverse', help="If enable, it Changes the default Autosync behaviour: The first frames of the Reference video are used as reference to sync with the Distorted one. (Default = Disable).", action='store_true')
    parser.add_argument('-model', dest='model', type=str, choices=('HD', '4K'), default="HD",
                        help="Vmaf Model. Options: HD, 4K. (Default: HD).")
    parser.add_argument('-threads', dest='threads', type=int,
                        default=0, help='number of threads')
    parser.add_argument(
        '-verbose', help='Activate verbose loglevel. (Default: info).', action='store_true')
    parser.add_argument(
        '-progress', help='Activate progress indicator for vmaf computation. (Default: false).', action='store_true')
    parser.add_argument(
        '-endsync', help='Activate end sync. This ends the computation when the shortest video ends. (Default: false).', action='store_true')

    parser.add_argument('-output_fmt', dest='output_fmt', type=str,
                        choices=('json', 'xml', 'csv'), default='json',
                        help='Output vmaf file format. Options: json, xml or csv (Default: json)')

    parser.add_argument(
        '-cambi_heatmap', help='Activate cambi heatmap. (Default: false).', action='store_true')
    parser.add_argument(
        '-sync_only', action='store_true', default=False,
        help='Measure sync only for every input. Requires an explicit finite -sw greater than zero. No Vmaf processing')
    parser.add_argument(
        '-json',
        help='Output final results as JSON to stdout. '
             'Compatible with --sync_only and full VMAF runs. '
             '(Default: false).',
        action='store_true',
        default=False
    )
    parser.add_argument(
        '-gpu',
        help='Use GPU-accelerated VMAF computation via libvmaf_cuda. '
             'Requires FFmpeg built with --enable-nonfree --enable-ffnvcodec '
             '--enable-libvmaf and libvmaf built with -Denable_cuda=true. '
             'Use the provided Dockerfile.cuda to build a compatible image. '
             '(Default: false).',
        action='store_true',
        default=False
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()
    if args.sync_only and (not math.isfinite(args.sw) or args.sw <= 0):
        parser.error('-sync_only requires an explicit finite -sw greater than zero')
    for flag, value in (('-sw', args.sw), ('-ss', args.ss), ('-fps', args.fps)):
        if not math.isfinite(value) or value < 0:
            parser.error('%s must be finite and greater than or equal to zero' % flag)
    if args.n < 1:
        parser.error('-subsample must be an integer of at least 1')
    if args.threads < 0:
        parser.error('-threads must be an integer greater than or equal to zero')
    return args


class MyParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help(sys.stderr)
        sys.exit(2)


def main():
    signal(SIGINT, handler)

    '''reading values from cmdParser'''
    cmdParser = get_args()
    main_pattern = cmdParser.d
    reference = cmdParser.r

    syncWin = cmdParser.sw
    ss = cmdParser.ss
    fps = cmdParser.fps
    n_subsample = cmdParser.n
    reverse = cmdParser.reverse
    model = cmdParser.model
    verbose = cmdParser.verbose
    output_fmt = cmdParser.output_fmt
    threads = cmdParser.threads
    print_progress = cmdParser.progress
    end_sync = cmdParser.endsync
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
            f"easyVmaf requires FFmpeg >= 5.0 built with --enable-libvmaf. "
            f"The 'model=' parameter for libvmaf was introduced in FFmpeg 5.0.",
            file=sys.stderr, flush=True
        )
        sys.exit(1)

    if not ffmpeg_info['builtin_models']:
        print(
            f"[easyVmaf] ERROR: FFmpeg {ffmpeg_info['version_str']} is installed "
            f"but libvmaf built-in models are not available. "
            f"Rebuild libvmaf with '-Dbuilt_in_models=true' and recompile FFmpeg.",
            file=sys.stderr, flush=True
        )
        sys.exit(1)

    logger.info(
        "FFmpeg %s detected. Built-in models: available.",
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
            myVmaf = vmaf(main, reference, loglevel=loglevel, subsample=n_subsample, model=model,
                          output_fmt=output_fmt, threads=threads, print_progress=print_progress, end_sync=end_sync, manual_fps=fps, cambi_heatmap=cambi_heatmap, gpu_mode=gpu_mode)
            if syncWin > 0:
                offset, psnr = myVmaf.syncOffset(syncWin, ss, reverse)
                if sync_only:
                    if use_json:
                        result = _build_result(
                            distorted=main,
                            reference=reference,
                            offset=offset,
                            psnr=psnr,
                            model=model,
                        )
                        _print_json_result(result)
                    else:
                        print(f"offset: {offset} | psnr: {psnr}", flush=True)
                    continue
            else:
                offset = (-ss if reverse else ss) if ss else 0.0
                psnr = None
                myVmaf.offset = offset

            vmafProcess = myVmaf.getVmaf()
        except (FFmpegExecutionError, UnsupportedFramerateError, ValueError) as e:
            print(f"[easyVmaf] ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        vmafpath = myVmaf.ffmpegQos.vmafpath
        vmafScore = []
        vmafNegScore = []
        vmafPhoneScore = []

        if output_fmt == 'csv':
            with open(vmafpath, mode='r', newline='') as csvFile:
                csvReader = csv.DictReader(csvFile)
                for row in csvReader:
                    if model == 'HD':
                        vmafScore.append(float(row[HD_MODEL_NAME]))
                        vmafNegScore.append(float(row[HD_NEG_MODEL_NAME]))
                        vmafPhoneScore.append(float(row[HD_PHONE_MODEL_NAME]))
                    if model == '4K':
                        vmafScore.append(float(row[_4K_MODEL_NAME]))

        elif output_fmt == 'xml':
            tree = ET.parse(vmafpath)
            root = tree.getroot()
            for frame in root.findall('frames/frame'):
                if model == 'HD':
                    vmafScore.append(float(frame.attrib[HD_MODEL_NAME]))
                    vmafNegScore.append(float(frame.attrib[HD_NEG_MODEL_NAME]))
                    vmafPhoneScore.append(float(frame.attrib[HD_PHONE_MODEL_NAME]))
                if model == '4K':
                    vmafScore.append(float(frame.attrib[_4K_MODEL_NAME]))
        else:
            with open(vmafpath) as jsonFile:
                jsonData = json.load(jsonFile)
                for frame in jsonData['frames']:
                    if model == 'HD':
                        vmafScore.append(frame["metrics"][HD_MODEL_NAME])
                        vmafNegScore.append(
                            frame["metrics"][HD_NEG_MODEL_NAME])
                        vmafPhoneScore.append(
                            frame["metrics"][HD_PHONE_MODEL_NAME])
                    if model == '4K':
                        vmafScore.append(frame["metrics"][_4K_MODEL_NAME])

        if use_json:
            vmaf_scores = {}
            if model == 'HD':
                vmaf_scores = {
                    HD_MODEL_NAME:       mean(vmafScore),
                    HD_NEG_MODEL_NAME:   mean(vmafNegScore),
                    HD_PHONE_MODEL_NAME: mean(vmafPhoneScore),
                }
            elif model == '4K':
                vmaf_scores = {
                    _4K_MODEL_NAME: mean(vmafScore),
                }
            result = _build_result(
                distorted=main,
                reference=reference,
                offset=offset,
                psnr=psnr,
                model=model,
                vmaf_scores=vmaf_scores,
                vmaf_output_file=myVmaf.ffmpegQos.vmafpath,
                cambi_heatmap_path=(
                    myVmaf.ffmpegQos.vmaf_cambi_heatmap_path
                    if cambi_heatmap else None
                ),
            )
            _print_json_result(result)
        else:
            print("\n \n \n \n \n ")
            print("=======================================", flush=True)
            print("Results:", main, flush=True)
            print("=======================================", flush=True)
            print("VMAF computed", flush=True)
            print("=======================================", flush=True)
            print("offset: ", offset, " | psnr: ", psnr)
            if model == 'HD':
                print("VMAF HD: ", mean(vmafScore))
                print("VMAF Neg: ", mean(vmafNegScore))
                print("VMAF Phone: ", mean(vmafPhoneScore))
            if model == '4K':
                print("VMAF 4K: ", mean(vmafScore))
            print("VMAF output file path: ", myVmaf.ffmpegQos.vmafpath)
            if cambi_heatmap:
                print("CAMBI Heatmap output path: ",
                    myVmaf.ffmpegQos.vmaf_cambi_heatmap_path)

            print("\n \n \n \n \n ")


if __name__ == '__main__':
    main()
