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
import re
import subprocess
import json
import logging
import os
from ffmpeg_progress_yield import FfmpegProgress

logger = logging.getLogger(__name__)


# Structured model definitions
# Each entry: (version_string, name_alias, extra_params_dict)
VMAF_MODELS = {
    'HD': [
        ('vmaf_v0.6.1',     'vmaf_hd',       {}),
        ('vmaf_v0.6.1neg',  'vmaf_hd_neg',   {}),
        ('vmaf_v0.6.1',     'vmaf_hd_phone', {'enable_transform': 'true'}),
    ],
    '4K': [
        ('vmaf_4k_v0.6.1',  'vmaf_4k',       {}),
    ],
}

# Keep existing names as aliases for cli.py imports — do not remove these
HD_MODEL_NAME       = VMAF_MODELS['HD'][0][1]   # 'vmaf_hd'
HD_NEG_MODEL_NAME   = VMAF_MODELS['HD'][1][1]   # 'vmaf_hd_neg'
HD_PHONE_MODEL_NAME = VMAF_MODELS['HD'][2][1]   # 'vmaf_hd_phone'
_4K_MODEL_NAME      = VMAF_MODELS['4K'][0][1]   # 'vmaf_4k'

# Version aliases (used in vmaf.py for the phone model version check)
HD_MODEL_VERSION        = VMAF_MODELS['HD'][0][0]
HD_NEG_MODEL_VERSION    = VMAF_MODELS['HD'][1][0]
HD_PHONE_MODEL_VERSION  = VMAF_MODELS['HD'][2][0]
_4K_MODEL_VERSION       = VMAF_MODELS['4K'][0][0]




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

    def __init__(self,  main, ref, loglevel="info"):
        self.loglevel = loglevel
        self._cmd = None
        self.main = inputFFmpeg(main, input_id=0)
        self.ref = inputFFmpeg(ref, input_id=1)
        self.psnrFilter = []
        self.vmafFilter = []
        self.invertedSrc = False
        self.vmafpath = None
        self.vmaf_cambi_heatmap_path = None

    def _commitBase(self):
        return [FFmpegQos._executable, '-y', '-hide_banner', '-stats', '-loglevel', self.loglevel]

    def _commit(self):
        """build the final cmd to run"""
        self._cmd = (
            self._commitBase() +
            self._commitInputs() +
            self._commitFilters() +
            self._commitOutputs()
        )

    def _commitInputs(self):
        """build the cmd for the inputs files"""
        return ['-i', self.main.videoSrc, '-i', self.ref.videoSrc, '-map', '0:v', '-map', '1:v']

    def _commitOutputs(self):
        return ['-f', 'null', '-']

    def _commitFilters(self, filterName='lavfi'):
        """build the cmd for the filters"""
        filter_string = ';'.join(self.main.filtersList + self.ref.filtersList + self.psnrFilter + self.vmafFilter)
        return [f'-{filterName}', filter_string]

    @staticmethod
    def _build_model_string(model: str) -> str:
        """
        Build the libvmaf model= filter parameter string for the given model.

        Format: version=X\\:name=Y\\:param=val|version=X\\:name=Y
        Pipe-separates multiple models (HD computes vmaf_hd, vmaf_hd_neg,
        vmaf_hd_phone in a single pass).

        Args:
            model: 'HD' or '4K'

        Returns:
            model string ready to pass as the model= parameter to libvmaf

        Raises:
            ValueError: if model is not a known key in VMAF_MODELS
        """
        if model not in VMAF_MODELS:
            raise ValueError(
                f"Unknown VMAF model '{model}'. "
                f"Supported models: {list(VMAF_MODELS.keys())}"
            )
        parts = []
        for version, name, params in VMAF_MODELS[model]:
            tokens = [f'version={version}', f'name={name}']
            for k, v in params.items():
                tokens.append(f'{k}={v}')
            parts.append('\\\\:'.join(tokens))
        return '|'.join(parts)

    def getPsnr(self, stats_file=False):
        """
        It adds PSNR filter to lavfi chain and run the ffmpeg cmd.
        The output is returned and saved as stats_file_psnr.log
        """
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID
        if stats_file == True:
            stats_file = os.path.splitext(self.main.videoSrc)[0] + '_psnr.log'
        else:
            stats_file = 'stats_file_psnr.log'

        self.psnrFilter = [f'[{main}][{ref}]psnr=stats_file={stats_file}']
        self._commit()

        logger.debug("FFmpeg PSNR cmd: %s", self._cmd)
        stdout = subprocess.check_output(
            self._cmd, stderr=subprocess.STDOUT, shell=False).decode('utf-8')
        stdout = stdout.split(" ")
        psnr = [s for s in stdout if "average" in s][0].split(":")[1]
        return float(psnr)

    def getVmaf(self, log_path=None, model='HD', subsample=1, output_fmt='json', threads=0, print_progress=False, end_sync=False, features = None, cambi_heatmap = False):
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID
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

        self.vmaf_cambi_heatmap_path = os.path.splitext(self.main.videoSrc)[0] + '_cambi_heatmap'



        model_str = FFmpegQos._build_model_string(model)
        if threads == 0:
            threads = os.cpu_count()
        if end_sync:
            shortest = 1
        else:
            shortest = 0

        if not features:
            self.vmafFilter = [f'[{main}][{ref}]libvmaf=log_fmt={log_fmt}:model={model_str}:n_subsample={subsample}:log_path={log_path}:n_threads={threads}:shortest={shortest}']

        elif features and not cambi_heatmap:
            self.vmafFilter = [f'[{main}][{ref}]libvmaf=log_fmt={log_fmt}:model={model_str}:n_subsample={subsample}:log_path={log_path}:n_threads={threads}:shortest={shortest}:feature={features}']

        elif features and cambi_heatmap:
            self.vmafFilter = [f'[{main}][{ref}]libvmaf=log_fmt={log_fmt}:model={model_str}:n_subsample={subsample}:log_path={log_path}:n_threads={threads}:shortest={shortest}:feature={features}\\\\:heatmaps_path={self.vmaf_cambi_heatmap_path}']


        self._commit()
        logger.debug("FFmpeg VMAF cmd: %s", self._cmd)

        if print_progress:
            process = FfmpegProgress(self._cmd)
            for progress in process.run_command_with_progress():
                logger.info("progress = %s%% - %s", progress,
                            "\n".join(str(process.stderr).splitlines()[-9:-8]))

        else:
            process = subprocess.Popen(
                self._cmd, stdout=subprocess.PIPE, shell=False)
            process.communicate()

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
    - setFpsFilter()
    - clearFilters()
    '''

    def __init__(self, videoSrc, input_id):
        self.name = f'input{input_id}_'
        self.id = input_id
        self.videoSrc = videoSrc
        self.filtersList = []
        self.extraOptions = []
        self.lastOutputID = f'{str(self.id)}:v'

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

    def setScaleFilter(self, width, height, algo='bicubic'):
        """Filter options for Upscale or Downscale"""
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

    def setFpsFilter(self, fps):
        inputID, outputID = self._newInOutForFilter()
        fpsFilter = f'[{inputID}]fps=fps={fps}[{outputID}]'
        self._setFilter(fpsFilter)
        self._updateOutputId(outputID)

    def clearFilters(self):
        self.filtersList = []
        self.lastOutputID = f'{str(self.id)}:v'


def check_ffmpeg() -> dict:
    """
    Detect FFmpeg version and libvmaf built-in model availability.

    Returns a dict with:
        {
            'version': (major, minor, patch),  # e.g. (7, 1, 0)
            'version_str': '7.1',
            'meets_minimum': bool,              # >= 5.0
            'builtin_models': bool,             # libvmaf built-in models available
        }

    Raises:
        RuntimeError: if ffmpeg binary is not found or version cannot be parsed
    """
    result = {
        'version': (0, 0, 0),
        'version_str': 'unknown',
        'meets_minimum': False,
        'builtin_models': False,
    }

    # --- Version detection ---
    try:
        proc = subprocess.run(
            [FFmpegQos._executable, '-version'],
            capture_output=True,
            text=True
        )
        output = proc.stdout
    except FileNotFoundError:
        raise RuntimeError(
            f"FFmpeg binary not found at '{FFmpegQos._executable}'. "
            f"Install FFmpeg >= 5.0 built with --enable-libvmaf."
        )

    # Parse "ffmpeg version X.Y.Z" or "ffmpeg version N-YYYYMMDD-..."
    # Dev builds look like: "ffmpeg version N-111825-gabcdef123"
    # Release builds: "ffmpeg version 7.1" or "ffmpeg version 7.1.1"
    match = re.search(r'ffmpeg version (\d+)\.(\d+)', output)
    if not match:
        # Dev build — cannot determine version reliably, warn and continue
        result['version_str'] = 'dev-build'
        result['meets_minimum'] = True   # assume dev builds are recent enough
    else:
        major, minor = int(match.group(1)), int(match.group(2))
        result['version'] = (major, minor, 0)
        result['version_str'] = f'{major}.{minor}'
        result['meets_minimum'] = (major, minor) >= (5, 0)

    # --- Built-in model probe ---
    # Run a minimal libvmaf command that uses a built-in model.
    # Use nullsrc as input — FFmpeg will fail, but the error message
    # tells us whether the model was found or not.
    # A "could not load libvmaf model" error = built-in models not compiled in.
    # Any other error (invalid input, etc.) = models are fine, input is the problem.
    probe_cmd = [
        FFmpegQos._executable,
        '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'nullsrc=s=64x64:r=1:d=0.1',
        '-f', 'lavfi', '-i', 'nullsrc=s=64x64:r=1:d=0.1',
        '-lavfi', f'libvmaf=model=version=vmaf_v0.6.1:log_fmt=json:log_path={os.devnull}',
        '-f', 'null', '-'
    ]
    try:
        probe = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True
        )
        probe_output = probe.stderr + probe.stdout
        if 'could not load libvmaf model' in probe_output:
            result['builtin_models'] = False
        else:
            result['builtin_models'] = True
    except Exception:
        # Probe failed entirely — conservative assumption
        result['builtin_models'] = False

    return result
