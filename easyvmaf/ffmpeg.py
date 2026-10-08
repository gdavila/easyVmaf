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


from . import config
from .models import ModelRun
import re
import subprocess
import json
import logging
import os
from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, Sequence, Tuple
from ffmpeg_progress_yield import FfmpegProgress

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HwAccel:
    """How FFmpeg decodes and filters with one hardware acceleration method.
    Pure data: when a step may run on the device is decided by layer 2."""
    name: str                        # -hwaccel
    output_format: str               # -hwaccel_output_format: frames stay on the device
    convert: str                     # pixel format conversion on device frames, {pix_fmt};
                                     # exact only without resizing
    frame_formats: Mapping[str, str]  # software pix_fmt -> format of its decoded frames
    download: str                    # device frames to memory, {frame_format}


HW_ACCELS = {
    'cuda': HwAccel(
        name='cuda',
        output_format='cuda',
        convert='scale_cuda=format={pix_fmt}',
        frame_formats={'yuv420p': 'nv12', 'yuv420p10le': 'p010'},
        download='hwdownload,format={frame_format}',
    ),
}


def _cleanup_interrupted_process(process):
    """Stop and reap only the child owned by the interrupted calculation."""
    if process is None:
        return
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        # Preserve the interruption, even if the OS cannot complete cleanup.
        logger.warning("Could not reap interrupted FFmpeg process: %s", error)
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


class FFmpegExecutionError(RuntimeError):
    """An FFmpeg calculation failed; its output must not be used as a result."""

    def __init__(self, cmd, returncode=None, detail=None):
        self.cmd = list(cmd)
        self.returncode = returncode
        message = "FFmpeg execution failed"
        if returncode is not None:
            message += f" (exit code {returncode})"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class FFprobe:
    '''
    Class to interact with FFprobe.
    It gets info about stream, frames and mpeg packets

    Inputs:
        - videoSrc: path to video
    Outputs:
        - getStreamInfo()
        - getFramesInfo()
        - getPacketsInfo()
    '''
    _executable = os.environ.get('FFPROBE', config.ffprobe)

    def __init__(self, videoSrc, loglevel="info"):
        self.videoSrc = videoSrc
        self.loglevel = loglevel
        self.streamInfo = None
        self.framesInfo = None
        self.packetsInfo = None
        self._cmd = None

    ''' private methods '''

    def _commitBase(self):
        ffprobe_loglevel = self.loglevel if self.loglevel == "verbose" else "quiet"
        return [FFprobe._executable, '-hide_banner', '-loglevel', ffprobe_loglevel,
                '-print_format', 'json']

    def _commitStreamSelection(self):
        return ['-select_streams', 'v']

    def _commitInput(self):
        return ['-i', self.videoSrc, '-read_intervals', '%+5']

    def _commit(self, opt):
        self._cmd = (
            self._commitBase() +
            [opt] +
            self._commitStreamSelection() +
            self._commitInput()
        )

    def _run(self):
        logger.debug("FFprobe cmd: %s", self._cmd)
        return json.loads(subprocess.check_output(self._cmd, shell=False))

    ''' public methods '''

    def getStreamInfo(self):
        self._commit('-show_streams')
        self.streamInfo = self._run()['streams'][0]
        return self.streamInfo

    def getFramesInfo(self):
        self._commit('-show_frames')
        self.framesInfo = self._run()['frames']
        return self.framesInfo

    def getPacketsInfo(self):
        self._commit('-show_packets')
        self.packetsInfo = self._run()['packets']
        return self.packetsInfo

    def getFormatInfo(self):
        self._commit('-show_format')
        self.packetsInfo = self._run()['format']
        return self.packetsInfo


class FFmpegQos:
    '''
    Class to interact with FFmpeg QoS Filters: PSNR and VMAF.
    Particullary, it interacts with libvmaf library through lavfi filter
    '''
    _executable = os.environ.get('FFMPEG', config.ffmpeg)

    def __init__(self,  main, ref, loglevel="info", gpu_mode=False):
        self.loglevel = loglevel
        self._cmd = None
        self.main = inputFFmpeg(main, input_id=0, gpu_mode=gpu_mode)
        self.ref  = inputFFmpeg(ref,  input_id=1, gpu_mode=gpu_mode)
        self.psnrFilter = []
        self.vmafFilter = []
        self.invertedSrc = False
        self.vmafpath = None
        self.gpu_mode = gpu_mode
        # Private: run FFmpeg single-threaded. Set only by sync PSNR workers,
        # which already run one process per CPU; default keeps FFmpeg's own threading.
        self._single_thread = False

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        r'''
        Escape a string so it can be safely embedded in an FFmpeg filter option
        value.

        Applies Level 1 (filter option) then Level 2 (filtergraph) escaping as
        defined in the FFmpeg filtergraph documentation.  Intended for values
        that appear after `=` in a filter assignment, e.g.
        `psnr=stats_file=VALUE` or `libvmaf=log_path=VALUE`.

        See https://ffmpeg.org/ffmpeg-filters.html#Notes-on-filtergraph-escaping.

        **Level 1** — filter option value special characters: `\':`

        **Level 2** — filtergraph special characters: `\'[],;`

        The two passes are applied in order: L1 first, then L2 on the result.
        This naturally handles the interaction where L1 introduces backslashes
        that L2 must then double.
        '''
        def _escape_chars(value: str, chars: str) -> str:
            for c in chars:
                value = value.replace(c, "\\" + c)
            return value

        # Level 1: escape filter option value special chars
        value = _escape_chars(value, r"\':")
        # Level 2: escape filtergraph special chars (including \ and ' again)
        value = _escape_chars(value, r"\'[],;")
        return value

    def _commitBase(self):
        base = [FFmpegQos._executable, '-y', '-hide_banner', '-stats', '-loglevel', self.loglevel]
        if self._single_thread:
            # Global option: -lavfi is a complex filtergraph, so -filter_threads
            # (simple filtergraphs only) would not apply.
            base += ['-filter_complex_threads', '1']
        return base

    def _commit(self):
        """build the final cmd to run"""
        self._cmd = (
            self._commitBase() +
            self._commitInputs() +
            self._commitFilters() +
            self._commitOutputs()
        )

    def _commitInputs(self):
        """build the cmd for the inputs files

        No -map: the -lavfi output is mapped automatically. Mapping 0:v/1:v
        would add unfiltered outputs that decode both files to EOF despite
        trim. -an/-sn/-dn stop auto-selection of other streams.
        """
        # Decoder threads are an input option: it must precede each -i.
        decoder = ['-threads', '1'] if self._single_thread else []
        # A seek keeps the timestamps the inputs have without it, so that the
        # filters see the same timeline as a calculation from the beginning.
        timestamps = (['-copyts', '-start_at_zero']
                      if self.main.seekOptions or self.ref.seekOptions else [])
        return (timestamps +
                decoder + self.main.extraOptions + ['-i', self.main.videoSrc] +
                decoder + self.ref.extraOptions + ['-i', self.ref.videoSrc] +
                ['-an', '-sn', '-dn'])

    def _commitOutputs(self):
        return ['-f', 'null', '-']

    def _commitFilters(self, filterName='lavfi'):
        """build the cmd for the filters"""
        filter_string = ';'.join(self.main.filtersList + self.ref.filtersList + self.psnrFilter + self.vmafFilter)
        return [f'-{filterName}', filter_string]

    @staticmethod
    def _build_model_string(models: Sequence[ModelRun]) -> str:
        """
        Build the libvmaf model= filter parameter string for the given runs.

        Format: version=X\\:name=Y\\:option=val|version=X\\:name=Y
        Each run contributes its libvmaf model id, its metric name and then its
        options in order; runs are pipe-separated so all models are computed in
        a single pass.

        Args:
            models: resolved ModelRun entries (see easyvmaf.models.select_models)

        Returns:
            model string ready to pass as the model= parameter to libvmaf
        """
        parts = []
        for run in models:
            tokens = [f'version={run.libvmaf_model}', f'name={run.spec.name}']
            for k, v in run.options:
                tokens.append(f'{k}={v}')
            parts.append('\\\\:'.join(tokens))
        return '|'.join(parts)

    def getPsnr(self):
        """
        It adds PSNR filter to lavfi chain and run the ffmpeg cmd.
        Returns the average PSNR that FFmpeg reports; no file is written.
        """
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID

        self.psnrFilter = [f'[{main}][{ref}]psnr']
        self._commit()

        logger.debug("FFmpeg PSNR cmd: %s", self._cmd)
        stdout = subprocess.check_output(
            self._cmd, stderr=subprocess.STDOUT, shell=False).decode('utf-8')
        averages = [s for s in stdout.split(" ") if "average" in s]
        if not averages:
            # The trims left no frame to compare, e.g. a start past the end.
            raise FFmpegExecutionError(self._cmd, 0, "the PSNR filter compared no frames")
        return float(averages[0].split(":")[1])

    def getFirstFrameTimestamps(self) -> Tuple[Tuple[int, Fraction], Tuple[int, Fraction]]:
        """
        Run the current filter chains until each one outputs its first frame.

        Returns:
            ((pts, time_base), (pts, time_base)) of the first frame of the main
            and ref chains, in the time base of the end of each chain

        Raises:
            FFmpegExecutionError: if FFmpeg fails or a chain outputs no frame
        """
        firsts = {}
        filters = list(self.main.filtersList + self.ref.filtersList)
        outputs = []
        for name, stream in (('main', self.main), ('ref', self.ref)):
            # Without its checksum, showinfo also accepts hardware frames.
            filters.append(f'[{stream.lastOutputID}]showinfo@{name}=checksum=0[first_{name}]')
            outputs += ['-map', f'[first_{name}]', '-frames:v', '1', '-f', 'null', '-']
        # showinfo reports at the info level.
        loglevel = self.loglevel if self.loglevel == 'verbose' else 'info'
        self._cmd = ([FFmpegQos._executable, '-y', '-hide_banner', '-loglevel', loglevel] +
                     self._commitInputs() + ['-lavfi', ';'.join(filters)] + outputs)
        logger.debug("FFmpeg first frame cmd: %s", self._cmd)
        result = subprocess.run(self._cmd, capture_output=True, text=True, shell=False)
        if result.returncode != 0:
            detail = (result.stderr.strip().splitlines() or ['no diagnostic output'])[-1]
            raise FFmpegExecutionError(self._cmd, result.returncode, detail)
        for name in ('main', 'ref'):
            prefix = r'\[showinfo@%s @ [^\]]+\] ' % name
            time_base = re.search(prefix + r'config in time_base: (\d+)/(\d+)', result.stderr)
            frame = re.search(prefix + r'n:\s*0 pts:\s*(-?\d+)', result.stderr)
            if not (time_base and frame):
                raise FFmpegExecutionError(self._cmd, 0, f"the {name} chain output no frame")
            firsts[name] = (int(frame.group(1)),
                            Fraction(int(time_base.group(1)), int(time_base.group(2))))
        return firsts['main'], firsts['ref']

    def getVmaf(self, models: Sequence[ModelRun], log_path=None, subsample=1, output_fmt='json', threads=0, print_progress=False, shortest=False, features=None, gpu=False):
        """Run VMAF and return its process, raising FFmpegExecutionError on failure.

        models are the resolved runs serialized into the libvmaf model= option;
        features is the complete libvmaf feature= value, built by the caller.
        An existing log is not a valid result after an execution failure.
        """
        if output_fmt == 'xml':
            log_fmt = "xml"
            if log_path == None:
                log_path = os.path.splitext(self.main.videoSrc)[
                    0] + '_vmaf.xml'
        elif output_fmt == "csv":
            log_fmt = "csv"
            if log_path == None:
                log_path = os.path.splitext(self.main.videoSrc)[
                    0] + '_vmaf.csv'
        else :
            log_fmt = "json"
            if log_path == None:
                log_path = os.path.splitext(self.main.videoSrc)[
                    0] + '_vmaf.json'

        self.vmafpath = log_path

        model_str = FFmpegQos._build_model_string(models)
        if threads == 0:
            threads = os.cpu_count()

        # Upload frames to GPU immediately before libvmaf_cuda.
        # All CPU filters (scale, fps, trim, deinterlace) must run before this.
        if gpu:
            self.main._insertHwupload()
            self.ref._insertHwupload()

        # Re-read lastOutputID after hwupload may have updated it
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID

        vmaf_filter_name = 'libvmaf_cuda' if gpu else 'libvmaf'

        base_params = (
            f'log_fmt={log_fmt}'
            f':model={model_str}'
            f':n_subsample={subsample}'
            f':log_path={self._escape_filter_value(log_path)}'
            f':n_threads={threads}'
            f':shortest={1 if shortest else 0}'
        )

        if not features:
            self.vmafFilter = [
                f'[{main}][{ref}]{vmaf_filter_name}={base_params}'
            ]
        else:
            self.vmafFilter = [
                f'[{main}][{ref}]{vmaf_filter_name}={base_params}'
                f':feature={features}'
            ]


        self._commit()
        logger.debug("FFmpeg VMAF cmd: %s", self._cmd)

        if print_progress:
            process = FfmpegProgress(self._cmd)
            child = None
            try:
                for progress in process.run_command_with_progress():
                    # Retain ownership even when the dependency clears .process.
                    child = getattr(process, 'process', None)
                    logger.info("progress = %s%% - %s", progress,
                                "\n".join(str(process.stderr).splitlines()[-9:-8]))
            except (KeyboardInterrupt, SystemExit):
                _cleanup_interrupted_process(child or getattr(process, 'process', None))
                raise
            except RuntimeError as error:
                # ffmpeg-progress-yield signals failure with RuntimeError.
                # Recent versions clear their Popen instance during cleanup.
                returncode = getattr(getattr(process, 'process', None), 'returncode', None)
                raise FFmpegExecutionError(self._cmd, returncode, str(error)) from error

        else:
            process = subprocess.Popen(
                self._cmd, stdout=subprocess.PIPE, shell=False)
            try:
                process.communicate()
            except (KeyboardInterrupt, SystemExit):
                _cleanup_interrupted_process(process)
                raise
            if process.returncode != 0:
                raise FFmpegExecutionError(self._cmd, process.returncode)

        return process

    def clearFilters(self):
        self.psnrFilter = []
        self.vmafFilter = []

    def invertSrcs(self):
        temp1 = self.main.videoSrc
        temp2 = self.ref.videoSrc
        invertedSrc = self.invertedSrc
        self.__init__(temp2, temp1, self.loglevel)
        self.invertedSrc = not (invertedSrc)


class inputFFmpeg:
    '''
    Class to interact with FFmpeg inputs.
    It allows to manage Filter chains to each input. i.e., main and ref. Each
    Supported Methods:
    - setScaleFilter()
    - setOffsetFilter()
    - setDeintFrameFilter()
    - setDeintFieldFilter()
    - setTrimFilter()
    - setPreTrimFilter(), setEndTrimFilter(), setRangeTrimFilter()
    - setPtsShiftFilter()
    - setFpsFilter()
    - setFormatFilter()
    - setHwDownloadFilter()
    - setSeek(), setHwDecode() (input options)
    - clearFilters()
    '''

    def __init__(self, videoSrc, input_id, gpu_mode=False):
        self.name = f'input{input_id}_'
        self.id = input_id
        self.videoSrc = videoSrc
        self.filtersList = []
        self.seekOptions = []
        self.hwDecodeOptions = []
        self.hwaccel = None
        self.hwFrames = False         # the chain ends on device frames
        self.lastOutputID = f'{str(self.id)}:v'
        self.gpu_mode = gpu_mode
        self._hwupload_done = False   # tracks whether hwupload has been inserted

    @property
    def extraOptions(self):
        """Input options, placed before -i."""
        return self.hwDecodeOptions + self.seekOptions

    def _setFilter(self, filter):
        self.filtersList.append(filter)

    def _newInOutForFilter(self):
        self.n = len(self.filtersList)
        if self.n == 0:
            inputID = f'{str(self.id)}:v'
            outputID = f'{self.name}{str(self.n)}'
        else:
            inputID = f'{self.name}{str(self.n-1)}'
            outputID = f'{self.name}{str(self.n)}'
        return inputID, outputID

    def _updateOutputId(self, outputID):
        self.lastOutputID = outputID

    def _insertHwupload(self):
        """
        Insert format=yuv420p + hwupload_cuda before libvmaf_cuda; on device
        frames (setHwDecode() without setHwDownloadFilter()), the hwaccel's
        conversion to yuv420p instead, without an upload.
        Called from FFmpegQos.getVmaf() after all CPU filters (trim, fps, yadif,
        scale) have been appended, immediately before libvmaf_cuda is connected.
        Only inserts once — subsequent calls are no-ops.

        format=yuv420p strips color-space metadata (bt709, tv/pc range, etc.)
        so FFmpeg does not auto-insert a CPU auto_scale between hwupload_cuda
        and libvmaf_cuda when inputs carry differing color-space tags.
        """
        if self._hwupload_done:
            return
        # Reset color-space and range metadata before uploading to GPU.
        # format=yuv420p changes pixel format but does NOT strip csp/range tags.
        # setparams resets them to unspecified so both inputs present identical
        # properties to libvmaf_cuda — without this, inputs tagged bt709/tv cause
        # libvmaf_cuda to auto-insert a CPU auto_scale for color normalization,
        # which then crashes because auto_scale cannot accept CUDA frames.
        setparams = 'setparams=colorspace=unknown:range=unknown'
        if self.hwFrames:
            # Hardware decoded and never downloaded: the frames are already on
            # the GPU, and libvmaf_cuda accepts only yuv420p there.
            steps = [self.hwaccel.convert.format(pix_fmt='yuv420p'), setparams]
        else:
            steps = ['format=yuv420p', setparams, 'hwupload_cuda']
        for step in steps:
            inputID, outputID = self._newInOutForFilter()
            self._setFilter(f'[{inputID}]{step}[{outputID}]')
            self._updateOutputId(outputID)
        self._hwupload_done = True

    def setScaleFilter(self, width, height, algo='bicubic'):
        """Filter options for upscale or downscale using software scale."""
        inputID, outputID = self._newInOutForFilter()
        scaleFilter = f'[{inputID}]scale={width}:{height}:flags={algo}[{outputID}]'
        self._setFilter(scaleFilter)
        self._updateOutputId(outputID)

    def setOffsetFilter(self, offset):
        """set offset for videoSrc: time to wait before display frames"""
        inputID, outputID = self._newInOutForFilter()
        ptsFilter = f'[{inputID}]setpts=PTS+{offset}/TB[{outputID}]'
        self._setFilter(ptsFilter)
        self._updateOutputId(outputID)

    def setDeintFrameFilter(self):
        """
        Output one frame for each frame: 30i-> 30p
        """
        yadifOpt = '0:-1:0'
        inputID, outputID = self._newInOutForFilter()
        yadifFilter = f'[{inputID}]yadif={yadifOpt}[{outputID}]'
        self._setFilter(yadifFilter)
        self._updateOutputId(outputID)

    def setDeintFieldFilter(self):
        """
        Output one frame for each field: 30i ->  60p
        """
        yadifOpt = '1:-1:0'
        inputID, outputID = self._newInOutForFilter()
        yadifFilter = f'[{inputID}]yadif={yadifOpt}[{outputID}]'
        self._setFilter(yadifFilter)
        self._updateOutputId(outputID)

    def setTrimFilter(self, start, duration):
        inputID, outputID = self._newInOutForFilter()
        trimFilter = f'[{inputID}]trim=start={start}:duration={duration}, setpts=PTS-STARTPTS[{outputID}]'
        self._setFilter(trimFilter)
        self._updateOutputId(outputID)
        return

    def setPreTrimFilter(self, start):
        """Drop the frames before start, keeping their timestamps: the filters
        that follow see the original timeline, and a later setTrimFilter()
        cuts at the same instant as without this filter."""
        inputID, outputID = self._newInOutForFilter()
        self._setFilter(f'[{inputID}]trim=start={start}[{outputID}]')
        self._updateOutputId(outputID)

    def setEndTrimFilter(self, end):
        """Drop the frames from end (seconds) on, keeping the timestamps."""
        inputID, outputID = self._newInOutForFilter()
        self._setFilter(f'[{inputID}]trim=end={end}[{outputID}]')
        self._updateOutputId(outputID)

    def setRangeTrimFilter(self, start, end=None):
        """Keep the frames in [start, end) seconds (to the end without end),
        then make the first one start at 0."""
        inputID, outputID = self._newInOutForFilter()
        bounds = f'start={start}' + (f':end={end}' if end is not None else '')
        self._setFilter(f'[{inputID}]trim={bounds}, setpts=PTS-STARTPTS[{outputID}]')
        self._updateOutputId(outputID)

    def setPtsShiftFilter(self, pts):
        """Subtract pts, in time base units, from every timestamp."""
        inputID, outputID = self._newInOutForFilter()
        self._setFilter(f'[{inputID}]setpts=PTS-{int(pts)}[{outputID}]')
        self._updateOutputId(outputID)

    def setSeek(self, seconds):
        """Start reading the input at seconds (-ss before -i): FFmpeg decodes
        from the keyframe before it and drops the frames up to it. FFmpegQos
        then keeps the original timestamps (-copyts -start_at_zero)."""
        self.seekOptions = ['-ss', f'{seconds:.6f}']

    def setHwDecode(self, hwaccel: HwAccel):
        """Decode the input with hwaccel, keeping its frames on the device:
        the chain starts on device frames until setHwDownloadFilter()."""
        self.hwDecodeOptions = ['-hwaccel', hwaccel.name,
                                '-hwaccel_output_format', hwaccel.output_format]
        self.hwaccel = hwaccel
        self.hwFrames = True

    def setHwDownloadFilter(self, pix_fmt):
        """Move the hardware decoded frames to memory as pix_fmt, the input's
        native software format: the same frames as the software decoder."""
        frame_format = self.hwaccel.frame_formats[pix_fmt]
        download = self.hwaccel.download.format(frame_format=frame_format)
        inputID, outputID = self._newInOutForFilter()
        self._setFilter(f'[{inputID}]{download},format={pix_fmt}[{outputID}]')
        self._updateOutputId(outputID)
        self.hwFrames = False

    def setFpsFilter(self, fps):
        inputID, outputID = self._newInOutForFilter()
        fpsFilter = f'[{inputID}]fps=fps={fps}[{outputID}]'
        self._setFilter(fpsFilter)
        self._updateOutputId(outputID)

    def setFormatFilter(self, pix_fmt):
        inputID, outputID = self._newInOutForFilter()
        formatFilter = f'[{inputID}]format={pix_fmt}[{outputID}]'
        self._setFilter(formatFilter)
        self._updateOutputId(outputID)

    def clearFilters(self):
        self.filtersList = []
        self.lastOutputID = f'{str(self.id)}:v'
        self._hwupload_done = False   # reset so hwupload can be re-inserted
        # The input options belong to the chain they were set for.
        self.seekOptions = []
        self.hwDecodeOptions = []
        self.hwaccel = None
        self.hwFrames = False


def check_ffmpeg() -> dict:
    """
    Detect FFmpeg version and probe the built-in VMAF v1 model vmaf_v1.0.16_3d0h.

    Returns a dict with:
        {
            'version': (major, minor, patch),  # e.g. (8, 1, 0)
            'version_str': '8.1',
            'meets_minimum': bool,              # >= 8.1
            'libvmaf_v1': bool,                 # vmaf_v1.0.16_3d0h scored a frame
            'cuda_vmaf': bool,                  # compiled filter, not GPU runtime
            'hwaccels': ['cuda', ...],          # ffmpeg -hwaccels: compiled, not usable
        }

    Raises:
        RuntimeError: if FFmpeg cannot run or its version cannot be parsed
    """
    result = {
        'version': (0, 0, 0),
        'version_str': 'unknown',
        'meets_minimum': False,
        'libvmaf_v1': False,
        'cuda_vmaf': False,      # libvmaf_cuda filter available
        'hwaccels': [],          # hardware decoding methods compiled in
    }

    # --- Version detection ---
    if not FFmpegQos._executable:
        raise RuntimeError(
            "FFmpeg binary not found. Install FFmpeg >= 8.1 built with --enable-libvmaf."
        )
    try:
        proc = subprocess.run(
            [FFmpegQos._executable, '-version'],
            capture_output=True,
            text=True,
            shell=False
        )
        output = proc.stdout
    except OSError as error:
        raise RuntimeError(
            f"FFmpeg binary could not be executed at '{FFmpegQos._executable}': {error}. "
            f"Install FFmpeg >= 8.1 built with --enable-libvmaf."
        ) from error

    if proc.returncode != 0:
        detail = proc.stderr.strip() or output.strip() or 'no diagnostic output'
        raise RuntimeError(
            f"FFmpeg version command failed (exit code {proc.returncode}): {detail}"
        )

    # Parse "ffmpeg version X.Y.Z" or "ffmpeg version N-YYYYMMDD-..."
    # Dev builds look like: "ffmpeg version N-111825-gabcdef123"
    # Release builds: "ffmpeg version 7.1" or "ffmpeg version 7.1.1"
    # Builds from git release tags (e.g. BtbN): "ffmpeg version n7.1.1-20250301"
    match = re.match(r'ffmpeg version n?(\d+)\.(\d+)(?=[\s.\-]|$)', output)
    if not match:
        if not re.match(r'ffmpeg version (?:N-\d+-g[0-9a-f]+|git-[0-9a-f]+)(?=[\s-]|$)', output):
            raise RuntimeError("FFmpeg version could not be parsed from command output.")
        # Recognized dev build — retain the existing minimum-version assumption.
        result['version_str'] = 'dev-build'
        result['meets_minimum'] = True   # assume dev builds are recent enough
    else:
        major, minor = int(match.group(1)), int(match.group(2))
        result['version'] = (major, minor, 0)
        result['version_str'] = f'{major}.{minor}'
        result['meets_minimum'] = (major, minor) >= (8, 1)

    # --- VMAF v1 probe ---
    # Score a frame, not just load the model: libvmaf 3.2.0 loads the v1 models
    # but a default build cannot extract their features. Its version string
    # cannot tell it apart from 3.2.1, so only this calculation decides.
    # v1's SpEED feature rejects frames below about 288x162, hence 320x240.
    probe_cmd = [
        FFmpegQos._executable,
        '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=black:s=320x240:r=1:d=1',
        '-f', 'lavfi', '-i', 'color=black:s=320x240:r=1:d=1',
        '-lavfi', f'libvmaf=model=version=vmaf_v1.0.16_3d0h:log_fmt=json:log_path={os.devnull}',
        '-f', 'null', '-'
    ]
    try:
        probe = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            shell=False
        )
        result['libvmaf_v1'] = probe.returncode == 0
    except OSError:
        # Probe failed entirely — conservative assumption
        result['libvmaf_v1'] = False

    # --- CUDA VMAF probe ---
    # Ask FFmpeg to list compiled-in filters. A CUDA-enabled build will
    # show 'libvmaf_cuda'; this does not validate a GPU device or CUDA runtime.
    try:
        probe_cuda = subprocess.run(
            [FFmpegQos._executable, '-hide_banner', '-filters'],
            capture_output=True,
            text=True,
            shell=False
        )
        result['cuda_vmaf'] = probe_cuda.returncode == 0 and any(
            len(fields) >= 2 and fields[1] == 'libvmaf_cuda'
            for fields in (line.split() for line in probe_cuda.stdout.splitlines())
        )
    except OSError:
        result['cuda_vmaf'] = False

    # --- Hardware decoding methods ---
    # A header line, then one method per line. Like -filters, a listed method
    # may still have no device or driver: probe_hw_decode() tells.
    try:
        probe_hwaccels = subprocess.run(
            [FFmpegQos._executable, '-hide_banner', '-hwaccels'],
            capture_output=True,
            text=True,
            shell=False
        )
        if probe_hwaccels.returncode == 0:
            result['hwaccels'] = [line.strip() for line in probe_hwaccels.stdout.splitlines()
                                  if line.strip() and not line.rstrip().endswith(':')]
    except OSError:
        result['hwaccels'] = []

    return result


def probe_hw_decode(path, hwaccel: HwAccel, timeout=60) -> bool:
    """
    Decode the first frame of path with hwaccel and convert it on the device.

    The conversion only accepts device frames, so the probe fails whenever
    FFmpeg would decode in software instead (unsupported codec, profile or
    pixel format, or no device or driver), even where FFmpeg does that
    silently with -hwaccel alone.

    Returns:
        True if the frame reached the device
    """
    cmd = [
        FFmpegQos._executable, '-hide_banner', '-nostdin', '-loglevel', 'error',
        '-hwaccel', hwaccel.name, '-hwaccel_output_format', hwaccel.output_format,
        '-i', path, '-an', '-sn', '-dn', '-frames:v', '1',
        '-vf', hwaccel.convert.format(pix_fmt='yuv420p'),
        '-f', 'null', '-'
    ]
    logger.debug("FFmpeg hardware decode probe cmd: %s", cmd)
    try:
        probe = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, shell=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.debug("Hardware decode probe of %s failed: %s", path, error)
        return False
    if probe.returncode != 0:
        lines = probe.stderr.strip().splitlines()
        logger.debug("Hardware decode probe of %s failed (exit code %d): %s", path,
                     probe.returncode, lines[-1] if lines else 'no diagnostic output')
        return False
    return True
