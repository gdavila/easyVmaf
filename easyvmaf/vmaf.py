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
from .ffmpeg import FFprobe
from .ffmpeg import FFmpegQos
from .models import DISPLAY_RESOLUTION, model_names, select_models
from .results import VmafResult, read_frames, read_scores, trim_log
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
import copy
from typing import Dict, List, Optional, Tuple
import logging
import math
import os
import re

logger = logging.getLogger(__name__)

# HFR v1 models are calibrated for ~50/60 fps content.
HFR_MIN_FPS = 47
HFR_MAX_CALIBRATED_FPS = 60
# libvmaf 3.2.1 CAMBI rejects an encoding size below 180x150, or with both
# sides below 216 (feature/cambi.c), and the whole VMAF v1 calculation fails.
CAMBI_MIN_ENC_WIDTH = 180
CAMBI_MIN_ENC_HEIGHT = 150
CAMBI_MIN_ENC_SIDE = 216
# Sync workers drop the frames up to this long before each offset without
# filtering them; the margin feeds deinterlacing its neighbouring frames.
SYNC_PREROLL = 1.0
# A frame range starts decoding this long before its first frame, so that
# yadif and fps see the same neighbouring frames as a full calculation.
RANGE_PREROLL = 1.0
# Frames measured and discarded on each side of a range: the motion features
# of a frame depend on the previous and the next frame (one is enough with
# the catalog models; the second is a margin).
RANGE_CONTEXT_FRAMES = 2
# Range bounds sit a quarter frame before a frame: trim rounds them to the
# nearest timestamp, and half a frame could round either way.
_RANGE_BOUND_MARGIN = 0.25
# Containers whose seek lands on the requested frame. MPEG-TS seeks to the
# next keyframe instead, and raw elementary streams have no timestamps.
_RANGE_FORMATS = ('mov', 'mp4', 'matroska', 'webm')

# --model-option syntax: no ':', '|', '[', ']', ';' or quotes can reach the filtergraph.
_MODEL_OPTION_RE = re.compile(r'^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*=[A-Za-z0-9_.\-]+$')

# Characters that cannot be escaped inside a v1 model= override: '|' splits
# models, and ':', '\' and "'" are consumed by one parsing level too many.
_HEATMAP_PATH_FORBIDDEN = (':', '|', '\\', "'")

_YUV_PIX_FMT_RE = re.compile(r'^yuvj?(420|422|444)p(\d+)?(le|be)?$')
_SEMIPLANAR_PIX_FMTS = {
    'nv12': ('420', 8), 'nv21': ('420', 8),
    'p010le': ('420', 10), 'p010be': ('420', 10),
}


def _parsePixFmt(pix_fmt) -> Optional[Tuple[str, int]]:
    """(chroma subsampling, bit depth) of a pixel format, or None if unknown."""
    if pix_fmt in _SEMIPLANAR_PIX_FMTS:
        return _SEMIPLANAR_PIX_FMTS[pix_fmt]
    match = _YUV_PIX_FMT_RE.match(str(pix_fmt))
    if not match:
        return None
    return match.group(1), int(match.group(2) or 8)


def _pixFmtBitdepth(pix_fmt) -> int:
    """Bit depth of a pixel format: its trailing digits, or 8 without them."""
    parsed = _parsePixFmt(pix_fmt)
    if parsed:
        return parsed[1]
    match = re.search(r'p(\d+)(le|be)?$', str(pix_fmt))
    return int(match.group(1)) if match else 8


def _cambiEncodingSize(width, height) -> Tuple[int, int]:
    """
    The encoding size passed to CAMBI: (width, height) when CAMBI accepts it,
    otherwise the smallest accepted size with the same aspect ratio, which is
    the closest to the real encoding resolution (e.g. 256x144 -> 267x150).
    """
    scale = max(1.0, CAMBI_MIN_ENC_WIDTH / width, CAMBI_MIN_ENC_HEIGHT / height,
                CAMBI_MIN_ENC_SIDE / max(width, height))
    # The epsilon keeps float error from rounding an exact side up by one.
    return math.ceil(width * scale - 1e-9), math.ceil(height * scale - 1e-9)


def _parseSize(size) -> Tuple[int, int]:
    """(width, height) from a 'WxH' string or a two-item sequence."""
    if isinstance(size, str):
        width, _, height = size.lower().partition('x')
        size = (width, height)
    try:
        width, height = (int(v) for v in size)
    except (TypeError, ValueError):
        raise UnsupportedModelConfigError(f"Invalid enc_size {size!r}: expected WxH") from None
    return width, height


def validate_model_config(display='hd', vmaf_versions=('1',), views=None, hfr='auto',
                          bitdepth='auto', enc_size=None, enc_bitdepth=None,
                          model_options=(), gpu_mode=False, labels=None):
    """
    Check that the requested models can be computed with these options,
    without probing any input. vmaf() runs it in its constructor; a caller that
    processes a batch can run it once before the first input.

    Args:
        display ... gpu_mode: as in vmaf()
        labels: names shown in error messages for each argument, e.g.
            {'gpu_mode': '--gpu'}; default: the argument names

    Returns:
        The selected ModelRun list, without overrides

    Raises:
        UnsupportedModelConfigError: options the selected models cannot honour
        ValueError: unknown display, version or view
    """
    labels = labels or {}

    def label(argument):
        return labels.get(argument, argument)

    vmaf_versions = tuple(str(v) for v in vmaf_versions)
    hfr, bitdepth = str(hfr).lower(), str(bitdepth).lower()
    has_v1 = '1' in vmaf_versions
    # select_models() rejects unknown displays, versions and views with ValueError.
    models = select_models(display, vmaf_versions, views)
    if hfr not in ('auto', 'on', 'off'):
        raise UnsupportedModelConfigError(
            f"{label('hfr')} must be 'auto', 'on' or 'off', not '{hfr}'")
    if bitdepth not in ('auto', '8', '10'):
        raise UnsupportedModelConfigError(
            f"{label('bitdepth')} must be 'auto', 8 or 10, not '{bitdepth}'")
    if gpu_mode and has_v1:
        # libvmaf_cuda has no CUDA extractors for the v1 features.
        raise UnsupportedModelConfigError(
            f"{label('gpu_mode')} only supports {label('vmaf_versions')} 0.6: "
            f"libvmaf_cuda cannot compute VMAF v1 models")
    if gpu_mode and bitdepth == '10':
        raise UnsupportedModelConfigError(
            f"{label('gpu_mode')} measures in yuv420p; {label('bitdepth')} 10 is not supported")
    if not has_v1:
        v1_only = [name for name, used in (
            (label('views'), views is not None), (label('hfr') + ' on', hfr == 'on'),
            (label('enc_size'), enc_size is not None),
            (label('enc_bitdepth'), enc_bitdepth is not None),
            (label('model_options'), bool(model_options))) if used]
        if v1_only:
            raise UnsupportedModelConfigError(
                f"{', '.join(v1_only)}: only for VMAF version 1 models, "
                f"add 1 to {label('vmaf_versions')}")
    for option in model_options:
        if not _MODEL_OPTION_RE.match(option):
            raise UnsupportedModelConfigError(
                f"Invalid {label('model_options')} '{option}': expected feature.option=value")
    return models


@dataclass
class FeatureConfig:
    """
    Represents a single libvmaf feature and its parameters.

    Example:
        FeatureConfig('cambi', {'full_ref': 'true', 'enc_width': '1920'})
        → 'name=cambi\\\\:full_ref=true\\\\:enc_width=1920'
    """
    name: str
    params: Dict[str, str] = field(default_factory=dict)

    def to_string(self) -> str:
        parts = [f'name={self.name}']
        for k, v in self.params.items():
            parts.append(f'{k}={v}')
        return '\\\\:'.join(parts)


class UnsupportedFramerateError(ValueError):
    """
    Raised when ref and distorted framerates cannot be reconciled
    for the given interlace combination. No deinterlace filter is
    available for this input pair.
    """
    pass


class UnsupportedRangeError(ValueError):
    """
    Raised when a frame range cannot be measured with the same frames and
    scores as the full calculation: options not supported with a range, an
    input whose container cannot seek exactly, or frames missing after a seek.
    """
    pass


def validate_range_config(start_frame=None, frame_count=None, subsample=1,
                          cambi_heatmap=False, gpu_mode=False, labels=None):
    """
    Check a frame range and the options it is combined with, without probing
    any input. vmaf() runs it in its constructor; the CLI runs it once.

    Args:
        start_frame ... gpu_mode: as in vmaf()
        labels: names shown in error messages for each argument

    Raises:
        UnsupportedRangeError
    """
    if start_frame is None and frame_count is None:
        return
    labels = labels or {}

    def label(argument):
        return labels.get(argument, argument)

    for argument, value, minimum in (('start_frame', start_frame, 0),
                                     ('frame_count', frame_count, 1)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < minimum):
            raise UnsupportedRangeError(
                f"{label(argument)} must be an integer of at least {minimum}, not {value!r}")
    unsupported = [name for name, used in (
        (label('subsample'), subsample != 1), (label('cambi_heatmap'), cambi_heatmap),
        (label('gpu_mode'), gpu_mode)) if used]
    if unsupported:
        raise UnsupportedRangeError(
            f"{', '.join(unsupported)}: not supported with a frame range "
            f"({label('start_frame')}, {label('frame_count')})")


class UnsupportedModelConfigError(ValueError):
    """
    Raised when the requested models cannot be computed with the given
    options, e.g. VMAF v1 in GPU mode (libvmaf_cuda has no v1 features).
    """
    pass


class video():
    """
    Video class to parse information of video streams obtained
    by _FFmpeg.FFprobe
    """

    def __init__(self, videoSrc, loglevel="info"):
        self.videoSrc = videoSrc
        self.loglevel = loglevel
        self.streamInfo = None
        self.framesInfo = None
        self.packetsInfo = None
        self._formatInfo_cached = None
        self._interlaced_cached = None
        self.interlacedFrames = None
        self.totalFrames = None
        self.bytesFramesTotal = None
        # Eager: streamInfo is needed immediately by all consumers
        self.getStreamInfo()
        # duration is computed eagerly since vmaf.__init__ accesses it immediately
        self.duration = self.getDuration()
        # formatInfo and interlaced are lazy — fetched on first access via properties

    @property
    def formatInfo(self):
        if self._formatInfo_cached is None:
            self._formatInfo_cached = FFprobe(self.videoSrc, self.loglevel).getFormatInfo()
        return self._formatInfo_cached

    @formatInfo.setter
    def formatInfo(self, value):
        self._formatInfo_cached = value

    @property
    def interlaced(self):
        if self._interlaced_cached is None:
            framesInfo = FFprobe(self.videoSrc, self.loglevel).getFramesInfo()
            self._updateFramesSummaryFromFrames(framesInfo)
        return self._interlaced_cached

    @interlaced.setter
    def interlaced(self, value):
        self._interlaced_cached = value

    def _updateFramesSummaryFromFrames(self, framesInfo):
        """Compute interlace summary from a frames list. Called lazily."""
        interlacedFrames_count = 0
        bytesFramesTotal = 0
        for frame in framesInfo:
            interlacedFrames_count += int(frame['interlaced_frame'])
            bytesFramesTotal += int(frame['pkt_size'])
        self.interlacedFrames = interlacedFrames_count
        self.totalFrames = len(framesInfo)
        self.bytesFramesTotal = bytesFramesTotal
        self.interlaced = bool(round(self.interlacedFrames / self.totalFrames))

    def _updateFramesSummary(self):
        if self.framesInfo is None:
            return
        self._updateFramesSummaryFromFrames(self.framesInfo)

    def getDuration(self):
        _EPSILON = 0.001  # 1ms guard against float imprecision
        try:
            duration = (float(self.streamInfo['duration'])
                        - float(self.streamInfo['start_time']))
            if duration < 0:
                duration = float(self.streamInfo['duration'])
        except KeyError:
            duration = (float(self.formatInfo['duration'])
                        - float(self.formatInfo['start_time']))
            if duration < 0:
                duration = float(self.formatInfo['duration'])
        return math.floor(duration * 1000) / 1000  # floor to nearest millisecond

    def getStreamInfo(self):
        logger.info("\n\n=======================================")
        logger.info("[easyVmaf] Getting stream info... %s", self.videoSrc)
        logger.info("=======================================")
        self.streamInfo = FFprobe(self.videoSrc, self.loglevel).getStreamInfo()
        return self.streamInfo

    def getFramesInfo(self):
        logger.info("\n\n=======================================")
        logger.info("[easyVmaf] Getting frames info... %s", self.videoSrc)
        logger.info("=======================================")
        self.framesInfo = FFprobe(self.videoSrc, self.loglevel).getFramesInfo()
        self._updateFramesSummary()
        return self.framesInfo

    def getPacketsInfo(self):
        logger.info("\n\n=======================================")
        logger.info("[easyVmaf] Getting packets info... %s", self.videoSrc)
        logger.info("=======================================")
        self.packetsInfo = FFprobe(self.videoSrc, self.loglevel).getPacketsInfo()
        return self.packetsInfo

    def getFormatInfo(self):
        logger.info("\n\n=======================================")
        logger.info("[easyVmaf] Getting format info... %s", self.videoSrc)
        logger.info("=======================================")
        self.formatInfo = FFprobe(self.videoSrc, self.loglevel).getFormatInfo()
        logger.debug("%s", self.formatInfo)
        return self.formatInfo


class vmaf():
    """
    Video class to manage VMAF computation of video streams. This class allows:
        - Upscale or downscale the MAIN or REF videos automatically according to the Vmaf model (1080, 4K, etc)
        - Deinterlace automatically the MAIN and REF videos if needed
        - To SYNC (in time) the MAIN and REF videos using psnr computation
        - Frame rate conversion (if needed)
    """

    def __init__(self, mainSrc, refSrc, *, display='hd', vmaf_versions=('1',), views=None, hfr='auto', bitdepth='auto', enc_size=None, enc_bitdepth=None, model_options=(), output_fmt='json', loglevel="info", subsample=1, threads=0, print_progress=False, shortest=False, manual_fps=0, cambi_heatmap=False, gpu_mode=False, start_frame=None, frame_count=None):
        """
        Args (model selection and VMAF v1 parameters):
            display:       'hd' or '4k'; target resolution of the scaling
            vmaf_versions: VMAF generations to compute, '1' and/or '0.6'
            views:         v1 viewing distances ('3h', '5h'/'phone', '1.5h');
                           None selects the display defaults
            hfr:           'auto' (HFR v1 models when the effective frame rate
                           is >= 47 fps), 'on' or 'off'
            bitdepth:      measurement bit depth: 'auto', 8 or 10
            enc_size:      (width, height) or 'WxH' encoding resolution for
                           CAMBI; default: the distorted stream size
            enc_bitdepth:  encoding bit depth for CAMBI; default: from the
                           distorted pix_fmt
            model_options: 'feature.option=value' overrides for the v1 models

        Args (frame range):
            start_frame:   first frame to measure, numbered as in the log of the
                           full calculation; None: 0
            frame_count:   number of frames to measure; None: to the end.
                           With either one set, the log holds exactly those
                           frames of the full calculation, with the same
                           frame numbers and scores.

        Raises:
            UnsupportedModelConfigError: options that the selected models
                cannot honour (see validate_model_config)
            UnsupportedRangeError: a range with unsupported options or inputs
            ValueError: unknown display, version or view
        """
        self.display = str(display).lower()
        self.vmaf_versions = tuple(str(v) for v in vmaf_versions)
        self.views = views
        self.hfr = str(hfr).lower()
        self.bitdepth = str(bitdepth).lower()
        self.enc_size = _parseSize(enc_size) if enc_size is not None else None
        self.enc_bitdepth = int(enc_bitdepth) if enc_bitdepth is not None else None
        self.model_options = tuple(model_options)
        self.gpu_mode = gpu_mode
        self.cambi_heatmap = cambi_heatmap
        self._validateModelConfig(mainSrc)
        validate_range_config(start_frame, frame_count, subsample, cambi_heatmap, gpu_mode)
        self.start_frame = start_frame
        self.frame_count = frame_count
        self.loglevel = loglevel
        self.main = video(mainSrc, self.loglevel)
        self.ref = video(refSrc, self.loglevel)
        if self._hasRange():
            self._checkRangeInputs()
        self.subsample = subsample
        self.ffmpegQos = FFmpegQos(
            self.main.videoSrc, self.ref.videoSrc, self.loglevel,
            gpu_mode=gpu_mode)
        self.target_resolution = list(DISPLAY_RESOLUTION[self.display])
        self.offset = 0
        self.manual_fps = manual_fps
        self.output_fmt = output_fmt
        self.threads = threads
        self.print_progress = print_progress
        self.shortest = shortest
        self.cambi_heatmap_path = None
        self.pix_fmt = None
        self.output_fps = None
        self.hfr_active = False

    def _hasV1(self):
        return '1' in self.vmaf_versions

    def _hasRange(self):
        return self.start_frame is not None or self.frame_count is not None

    def _checkRangeInputs(self):
        """Reject inputs whose container cannot seek to the requested frame."""
        for stream, role in ((self.main, 'distorted'), (self.ref, 'reference')):
            format_name = stream.formatInfo.get('format_name', '')
            if not set(format_name.split(',')) & set(_RANGE_FORMATS):
                raise UnsupportedRangeError(
                    f"A frame range needs inputs that seek to an exact frame (MP4, MOV, "
                    f"Matroska or WebM); the {role} video {stream.videoSrc} is {format_name}")

    def _validateModelConfig(self, mainSrc):
        self.models = validate_model_config(
            self.display, self.vmaf_versions, self.views, self.hfr, self.bitdepth,
            self.enc_size, self.enc_bitdepth, self.model_options, self.gpu_mode)
        if self.cambi_heatmap and self._hasV1():
            path = self._cambiHeatmapPath(mainSrc)
            if any(c in path for c in _HEATMAP_PATH_FORBIDDEN):
                raise UnsupportedModelConfigError(
                    f"CAMBI heatmap path '{path}' cannot contain any of "
                    f"{' '.join(_HEATMAP_PATH_FORBIDDEN)} with VMAF v1 models; "
                    f"rename or move the distorted file")

    @staticmethod
    def _cambiHeatmapPath(mainSrc):
        return os.path.splitext(mainSrc)[0] + '_cambi_heatmap'

    @staticmethod
    def _chains(qos):
        """(distorted, reference) filter chains of qos: reverse sync workers
        open the reference first, so their main chain is the reference."""
        return (qos.ref, qos.main) if qos.invertedSrc else (qos.main, qos.ref)

    def _applyScaleFilters(self, qos):
        """Scale each input that is not at the display resolution."""
        distorted, reference = self._chains(qos)
        for stream, chain in ((self.main, distorted), (self.ref, reference)):
            if [stream.streamInfo['width'], stream.streamInfo['height']] != self.target_resolution:
                chain.setScaleFilter(self.target_resolution[0], self.target_resolution[1])

    def _normalizeChains(self, qos):
        """
        Bring both inputs of qos to a common frame rate, then to the display
        resolution: deinterlacing must see the original fields, which scaling
        would blend. The sync workers and the final calculation share it, so
        a sync offset lands on the same frame in both.

        Returns the effective frame rate of the distorted input.
        """
        if self.manual_fps == 0:
            output_fps = self._applyDeinterlaceFilters(qos)
        else:
            for chain in self._chains(qos):
                chain.setFpsFilter(self.manual_fps)
            output_fps = self.manual_fps
        self._applyScaleFilters(qos)
        return output_fps

    def _measurementPixFmt(self):
        """
        Pixel format both inputs are measured in: chroma subsampling of the
        reference; 10 bits with any v1 model (or the reference depth if higher),
        the reference depth with only v0.6, or the bitdepth override.
        """
        ref_pix_fmt = self.ref.streamInfo.get('pix_fmt')
        parsed = _parsePixFmt(ref_pix_fmt)
        if parsed is None:
            logger.warning("Unrecognized reference pixel format %s; measuring as 4:2:0",
                           ref_pix_fmt)
            parsed = ('420', 8)
        chroma, ref_depth = parsed
        if self.bitdepth != 'auto':
            depth = int(self.bitdepth)
        elif self._hasV1():
            depth = max(10, ref_depth)
        else:
            depth = ref_depth
        return f'yuv{chroma}p' if depth == 8 else f'yuv{chroma}p{depth}le'

    def _applyPixelFormat(self):
        """
        Convert each input whose native pixel format differs from the
        measurement format, as the first filter of its chain: otherwise FFmpeg
        picks the format and may degrade the reference (e.g. 4:2:2 to 4:2:0, or
        10 to 8 bits), or deinterlace at 8 bits before converting.
        The GPU path keeps its own format=yuv420p before hwupload_cuda.
        """
        if self.gpu_mode:
            self.pix_fmt = 'yuv420p'
            return
        self.pix_fmt = self._measurementPixFmt()
        # The shared ffmpegQos always keeps main=distorted, ref=reference.
        for stream, chain in ((self.main, self.ffmpegQos.main),
                              (self.ref, self.ffmpegQos.ref)):
            if stream.streamInfo.get('pix_fmt') != self.pix_fmt:
                chain.setFormatFilter(self.pix_fmt)

    def _deinterlaceFrame(self, factor, chain):
        """Returns the fps forced on the chain, or None if yadif sets its rate."""
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        chain.setDeintFrameFilter()
        if round(ref_fps, 2) != round(factor*main_fps, 2):
            chain.setFpsFilter(round(main_fps, 5))
            return round(main_fps, 5)
        return None

    def _deinterlaceField(self, factor, chain):
        """Returns the fps forced on the chain, or None if yadif sets its rate."""
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        chain.setDeintFieldFilter()
        if round(ref_fps, 2) != round(factor*main_fps, 2):
            chain.setFpsFilter(round(main_fps, 5))
            return round(main_fps, 5)
        return None

    def _applyDeinterlaceFilters(self, qos):
        """
        Apply deinterlace/fps filters to the given FFmpegQos instance.

        Returns the effective frame rate of MAIN (distorted) after the filters.
        """
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])
        distorted, reference = self._chains(qos)

        if self.ref.interlaced == self.main.interlaced:
            """ Not Deinterlace would be required. So this functions normalizes the fps between REF and MAIN
            """
            if round(ref_fps) < round(main_fps):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                distorted.setFpsFilter(round(ref_fps, 5))
                return round(ref_fps, 5)
            elif round(ref_fps) > round(main_fps):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                reference.setFpsFilter(round(main_fps, 5))
            else:
                distorted.setFpsFilter(round(main_fps, 5))
                reference.setFpsFilter(round(ref_fps, 5))
            return round(main_fps, 5)

        elif self.ref.interlaced and not self.main.interlaced:
            """
            REF interlaced  | MAIN progressive: MAIN keeps its frame rate
            """
            if round(ref_fps) == round(main_fps*2):
                self._deinterlaceFrame(2, reference)

            elif round(ref_fps) == round(main_fps):
                self._deinterlaceFrame(1, reference)

            elif round(ref_fps) == round(main_fps/2):
                self._deinterlaceField(0.5, reference)

            else:
                raise UnsupportedFramerateError(
                    f"No deinterlace filter available for the given framerate combination. "
                    f"ref={round(ref_fps, 5)}fps (interlaced={self.ref.interlaced}), "
                    f"main={round(main_fps, 5)}fps (interlaced={self.main.interlaced}). "
                    f"Consider using the --fps flag to force a frame rate manually."
                )
            return round(main_fps, 5)

        elif not self.ref.interlaced and self.main.interlaced:
            """
            Input Progressive (REF) | Output Interlaced (MAIN)
            Field deinterlacing doubles MAIN's frame rate unless an fps filter follows.
            """
            if round(ref_fps) == round(main_fps*2):
                # REF=50p, MAIN=25i: one MAIN frame per field matches REF's rate.
                forced = self._deinterlaceField(2, distorted)
                return forced or round(main_fps*2, 5)

            elif round(ref_fps) == round(main_fps):
                forced = self._deinterlaceFrame(1, distorted)
                return forced or round(main_fps, 5)

            elif round(ref_fps) == round(main_fps/2):
                # REF=25p, MAIN=25i reported at its field rate (50): one MAIN
                # frame per frame, the mirror of REF interlaced at 2x MAIN.
                forced = self._deinterlaceFrame(0.5, distorted)
                return forced or round(main_fps/2, 5)

            else:
                raise UnsupportedFramerateError(
                    f"No deinterlace filter available for the given framerate combination. "
                    f"ref={round(ref_fps, 5)}fps (interlaced={self.ref.interlaced}), "
                    f"main={round(main_fps, 5)}fps (interlaced={self.main.interlaced}). "
                    f"Consider using the --fps flag to force a frame rate manually."
                )

    def _computePsnrAtOffset(self, offset, reverse):
        """
        Compute PSNR between ref and main at a given time offset.
        Creates an independent FFmpegQos instance — safe to call concurrently.

        Args:
            offset:  time in seconds to trim the ref (or main if reverse) stream
            reverse: if True, main and ref roles are swapped

        Returns:
            (offset, psnr_value) tuple
        """
        # Always use CPU for PSNR sync computation regardless of self.gpu_mode
        if not reverse:
            qos = FFmpegQos(self.main.videoSrc, self.ref.videoSrc, self.loglevel,
                            gpu_mode=False)
        else:
            qos = FFmpegQos(self.ref.videoSrc, self.main.videoSrc, self.loglevel,
                            gpu_mode=False)
            qos.invertedSrc = True
        # One FFmpeg process per offset already fills the CPUs; internal
        # FFmpeg threads on top of that oversubscribe them.
        qos._single_thread = True

        # The ref slot holds the searched input. Trim it after the same filters
        # as setOffset() in the final calculation, so the offset selects the
        # same frame or field; the pre-trim only skips filtering what comes
        # well before it.
        preroll_start = offset - SYNC_PREROLL
        if preroll_start > 0:
            qos.ref.setPreTrimFilter(preroll_start)
        self._normalizeChains(qos)
        qos.ref.setTrimFilter(offset, 0.5)
        qos.main.setTrimFilter(0, 0.5)

        psnr_value = qos.getPsnr()
        return (offset, psnr_value)

    def syncOffset(self, syncWindow=3, start=0, reverse=False):
        """
        Method to get the offset needed to sync REF and MAIN (if any).
            syncWindow -->  Window Size in seconds to try to sync REF and MAIN videos. i.e., if the video to sync
                            last 600 seconds, the sync look up will be done just within a subsample of syncWindow size.
                            By default, the syncWindow is applied to REF.
            start -->  start time in seconds from the begining of the video where the syncWindow begin.
                        By default, the start time applies to REF.
            reverse --> If this option is set to TRUE. It is considered that MAIN is delayed in comparition to REF: 'syncWindow' and 'start' variables will be
                        applied to MAIN.
                        By default, it is supposed that the REF video is delayed in comparition with the MAIN video.

        It returns the offset value to get REF and MAIN synced and the PSNR computed.
        """

        # The window slides over the reference, or over the distorted video
        # with reverse; past its end FFmpeg has no frame left to compare.
        searched, role = (self.main, 'distorted') if reverse else (self.ref, 'reference')
        if start + syncWindow > searched.duration:
            raise ValueError(
                f"The sync window ends at {start + syncWindow:g} s, but the {role} video "
                f"lasts {searched.duration:g} s: lower the sync start or the sync window")

        logger.info("=" * 39)
        logger.info("Syncing... Computing PSNR values...")
        logger.info("=" * 39)
        logger.info("Distorted: %s @ %s fps | %s %s",
                    self.main.videoSrc,
                    round(getFrameRate(self.main.streamInfo['r_frame_rate']), 5),
                    self.main.streamInfo['width'],
                    self.main.streamInfo['height'])
        logger.info("Reference: %s @ %s fps | %s %s",
                    self.ref.videoSrc,
                    round(getFrameRate(self.ref.streamInfo['r_frame_rate']), 5),
                    self.ref.streamInfo['width'],
                    self.ref.streamInfo['height'])
        logger.info("=" * 39)
        logger.info("%-20s %s", "offset(s)", "psnr[dB]")

        fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        frameDuration = 1 / fps
        startFrame = int(round(start / frameDuration))
        framesInSyncWindow = int(round(syncWindow / frameDuration))

        offsets = [
            (startFrame + i) * frameDuration
            for i in range(framesInSyncWindow)
        ]

        max_workers = self.threads if self.threads > 0 else os.cpu_count()

        # The lazy interlace probe is not locked: if workers trigger it, each
        # one runs its own frames probe. Probe once per input before the pool.
        if self.manual_fps == 0:
            self.main.interlaced
            self.ref.interlaced

        # Results arrive in completion order (not offset order) — logged as they finish
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._computePsnrAtOffset, offset, reverse): offset
                for offset in offsets
            }
            for future in as_completed(futures):
                offset, psnr_value = future.result()
                results.append((offset, psnr_value))
                logger.info("%-20s %s", offset, psnr_value)

        # Sort to guarantee deterministic best-offset selection
        results.sort(key=lambda x: x[0])
        best_offset, best_psnr = max(results, key=lambda x: x[1])

        # Only workers swap sources for reverse search. Preserve the shared
        # distorted/reference roles; a negative offset trims distorted in setOffset().
        self.offset = -best_offset if reverse else best_offset
        return [self.offset, best_psnr]

    def setOffset(self, value=None):
        """
        Apply trim filters to synchronize main and distorted streams.

        Precondition: _normalizeChains() must have been applied to
        self.ffmpegQos before calling this method.
        Trim filters are appended to the existing filter chain — they must
        come last in the sequence.

        If offset == 0, no filters are applied (streams are already in sync).

        If offset > 0: Ref delayed compared to Main. Trimfilter cuts Ref.
        If offset < 0: Main delayed compared to Ref. Trimfilter cuts Main.
        """

        if value != None:
            """ overrides the value in self.offset"""
            self.offset = value

        trims = self._syncTrims()
        if trims:
            main_start, ref_start, duration = trims
            self.ffmpegQos.ref.setTrimFilter(ref_start, duration)
            self.ffmpegQos.main.setTrimFilter(main_start, duration)

    def _syncTrims(self):
        """
        (main start, ref start, duration) in seconds of the trims that sync
        the inputs at self.offset, or None when the offset is 0.
        """
        if self.offset > 0:
            offset = self.offset
            return 0, offset, min(self.main.duration, self.ref.duration-offset)
        if self.offset < 0:
            offset = abs(self.offset)
            return offset, 0, min(self.main.duration - offset, self.ref.duration)
        return None

    def _rangeBounds(self):
        """(first, end) frames measured for the range, context included;
        end is exclusive, None to the end."""
        start = self.start_frame or 0
        first = max(0, start - RANGE_CONTEXT_FRAMES)
        if self.frame_count is None:
            return first, None
        return first, start + self.frame_count + RANGE_CONTEXT_FRAMES

    def _applyRange(self):
        """
        Apply the sync trims and the frame range, instead of setOffset().

        Precondition: as setOffset(). Each chain is the one of the full
        calculation with three changes:
          1. a seek shortly before the range, keeping the original timestamps;
          2. setpts=PTS-<anchor> instead of setpts=PTS-STARTPTS, the anchor
             being the first frame the full calculation measures;
          3. a final trim to the range plus RANGE_CONTEXT_FRAMES on each side.
        The anchors come from running the chains up to their first measured
        frame: FFmpeg picks that frame, not a computation that could round
        differently.
        """
        fps = self.output_fps
        trims = self._syncTrims()
        chains = (self.ffmpegQos.main, self.ffmpegQos.ref)
        starts = trims[:2] if trims else (0, 0)

        probe = copy.deepcopy(self.ffmpegQos)
        for chain, start in zip((probe.main, probe.ref), starts):
            chain.setSeek(max(0.0, start - RANGE_PREROLL))
            if trims:
                chain.setPreTrimFilter(start)
        anchors = probe.getFirstFrameTimestamps()
        times = [float(pts * time_base) for pts, time_base in anchors]
        logger.info("Range anchors: distorted %s s, reference %s s", times[0], times[1])
        if not trims and abs(times[0] - times[1]) > _RANGE_BOUND_MARGIN / fps:
            # Unsynced, libvmaf pairs frames by timestamp from the first one;
            # a range starts each input at its own first frame.
            raise UnsupportedRangeError(
                f"The distorted and reference videos start at different timestamps "
                f"({times[0]:g} s and {times[1]:g} s); a frame range needs them synced: "
                f"set an offset or a sync window")

        first, end = self._rangeBounds()
        range_start = max(0.0, (first - _RANGE_BOUND_MARGIN) / fps)
        range_end = (end - _RANGE_BOUND_MARGIN) / fps if end is not None else None
        for chain, start, (pts, _), time in zip(chains, starts, anchors, times):
            chain.setSeek(max(0.0, time + first / fps - RANGE_PREROLL))
            if trims:
                chain.setPreTrimFilter(start)
            chain.setPtsShiftFilter(pts)
            if trims:
                chain.setEndTrimFilter(trims[2])
            chain.setRangeTrimFilter(range_start, range_end)

    def _rangeLogPath(self):
        """libvmaf log of a range: named after the frames requested, so that
        ranges of the same distorted video do not overwrite each other."""
        start = self.start_frame or 0
        last = start + self.frame_count - 1 if self.frame_count is not None else 'end'
        ext = self.output_fmt if self.output_fmt in ('xml', 'csv') else 'json'
        return f'{os.path.splitext(self.main.videoSrc)[0]}_vmaf_f{start}-{last}.{ext}'

    def _trimRangeLog(self, log_path):
        """
        Drop the context frames from the range log and number its frames as
        in the full calculation. Returns the number of frames kept.

        Raises:
            UnsupportedRangeError: if frames are missing before the end of
                the inputs (a seek that did not land on the requested frame),
                or if the range starts after the last frame
        """
        first, end = self._rangeBounds()
        start = self.start_frame or 0
        # libvmaf writes no log when no frame reaches it.
        measured = len(read_frames(log_path, self.output_fmt)) if os.path.exists(log_path) else 0
        if end is not None and first + measured < end:
            trims = self._syncTrims()
            if trims:
                last_time = trims[2]
            elif self.shortest:
                last_time = min(self.main.duration, self.ref.duration)
            else:
                last_time = max(self.main.duration, self.ref.duration)
            # Container durations are approximate: two frames of tolerance.
            if (first + measured + 2) / self.output_fps < last_time:
                raise UnsupportedRangeError(
                    f"The range measured {measured} frames from frame {first} instead of "
                    f"{end - first}, before the end of the inputs ({last_time:g} s): "
                    f"an input could not be read from the requested frame")
        kept = first + measured - start
        if self.frame_count is not None:
            kept = min(kept, self.frame_count)
        if kept <= 0:
            last = f" ({first + measured - 1})" if measured else ""
            raise UnsupportedRangeError(
                f"The range starts at frame {start}, after the last measured frame{last}")
        trim_log(log_path, self.output_fmt, start - first, kept, first)
        return kept

    @staticmethod
    def _cambiEncodingSize(width, height):
        """CAMBI-accepted encoding size, with a warning when it differs."""
        size = _cambiEncodingSize(width, height)
        if size != (width, height):
            logger.warning("CAMBI does not accept a %sx%s encoding size; using %sx%s, "
                           "the smallest accepted size with the same aspect ratio",
                           width, height, *size)
        return size

    def _resolveModels(self):
        """
        Select the models to compute once the effective frame rate is known,
        and add the v1 overrides: CAMBI encoding resolution and bit depth (the
        distorted stream is scaled before libvmaf, so CAMBI would otherwise
        measure banding on the scaled picture), model_options and the heatmap.
        """
        if self.hfr == 'auto':
            hfr = self.output_fps >= HFR_MIN_FPS
        else:
            hfr = self.hfr == 'on'
        self.hfr_active = hfr and self._hasV1()
        if self.hfr_active and self.output_fps > HFR_MAX_CALIBRATED_FPS:
            logger.warning("%s fps is above the ~50/60 fps the HFR VMAF v1 models are "
                           "calibrated for", round(self.output_fps, 5))
        runs = select_models(self.display, self.vmaf_versions, self.views, hfr=hfr)

        # In the final calculation main is always the distorted stream.
        if self.enc_size is not None:
            enc_width, enc_height = self.enc_size
        else:
            enc_width = self.main.streamInfo['width']
            enc_height = self.main.streamInfo['height']
        enc_width, enc_height = self._cambiEncodingSize(enc_width, enc_height)
        if self.enc_bitdepth is not None:
            enc_bitdepth = self.enc_bitdepth
        else:
            enc_bitdepth = _pixFmtBitdepth(self.main.streamInfo.get('pix_fmt'))
        overrides = (
            ('cambi.enc_width', str(enc_width)),
            ('cambi.enc_height', str(enc_height)),
            ('cambi.enc_bitdepth', str(enc_bitdepth)),
        ) + tuple(tuple(option.split('=', 1)) for option in self.model_options)

        heatmap = None
        if self.cambi_heatmap:
            heatmap = (('cambi.heatmaps_path',
                        FFmpegQos._escape_filter_value(self.cambi_heatmap_path)),)
        resolved = []
        for run in runs:
            if run.spec.vmaf_version == '1':
                extra = overrides
                if heatmap:
                    extra, heatmap = extra + heatmap, None
                run = replace(run, options=run.options + extra)
            resolved.append(run)
        self.models = resolved
        return resolved

    def _build_feature_string(self) -> Optional[str]:
        """
        Build the libvmaf feature string from the current configuration.
        Returns None if no additional features are requested (uses model defaults).

        Features are pipe-separated: 'name=A\\\\:p=v|name=B\\\\:p=v'
        """
        features: List[FeatureConfig] = []

        # PSNR is always included — used for sync offset reporting
        features.append(FeatureConfig('psnr'))

        # Separate CAMBI only when requested and no v1 model computes it:
        # v1 models get cambi.heatmaps_path as a model override instead.
        if self.cambi_heatmap and not self._hasV1():
            enc_width, enc_height = self._cambiEncodingSize(
                self.main.streamInfo['width'], self.main.streamInfo['height'])
            cambi_params = {
                'full_ref':   'true',
                'enc_width':  str(enc_width),
                'enc_height': str(enc_height),
                'src_width':  str(self.ref.streamInfo['width']),
                'src_height': str(self.ref.streamInfo['height']),
                'heatmaps_path': FFmpegQos._escape_filter_value(self.cambi_heatmap_path),
            }
            features.append(FeatureConfig('cambi', cambi_params))

        if not features:
            return None

        return '|'.join(f.to_string() for f in features)

    def getVmaf(self, autoSync=False):
        """
        Run VMAF computation between main (distorted) and ref (reference) streams.

        Filter application contract — always in this order:
            1. clearFilters()       — reset all filter chains on ffmpegQos
            2. _applyPixelFormat()  — format=<measurement pix_fmt> as the first
                                      filter of each chain that needs it (CPU only)
            3. _normalizeChains()   — deinterlace or convert the frame rate
                                      (--fps), then scale to the display
                                      resolution; records output_fps
            4. setOffset()          — apply trim filters for temporal sync,
               or _applyRange()     — with a frame range: seek, sync trims
                                      anchored to the full calculation, range
            5. _resolveModels()     — HFR choice from output_fps, v1 overrides
            6. ffmpegQos.getVmaf()  — with gpu, inserts hwupload_cuda last

        Note: syncOffset() (when autoSync=True) is called between steps 3 and 4.
        Its workers build their own FFmpegQos with the same _normalizeChains()
        and trim after it, like setOffset(), so the offset they choose selects
        the same frame here; self.ffmpegQos filter chains are left intact.

        Returns:
            VmafResult with the mean score of every model
        """
        self.ffmpegQos.clearFilters()
        self.ffmpegQos.main.clearFilters()
        self.ffmpegQos.ref.clearFilters()

        self._applyPixelFormat()

        if self.manual_fps:
            logger.warning("Forcing frame rate conversion manually")
        self.output_fps = self._normalizeChains(self.ffmpegQos)

        """Lookup for sync between Main and reference. Default: dissable
           It is suggested to run syncOffset manually before getVmaf()
        """
        if autoSync:
            self.syncOffset()
        if self._hasRange():
            self._applyRange()
        else:
            """Apply Offset filters, if offset =0 nothing happens """
            self.setOffset()

        if self.cambi_heatmap:
            self.cambi_heatmap_path = self._cambiHeatmapPath(self.main.videoSrc)
        self._resolveModels()
        self.features = self._build_feature_string()


        logger.info("=" * 39)
        logger.info("Computing VMAF...")
        logger.info("=" * 39)
        logger.info("Distorted: %s @ %s fps | %s %s",
                    self.main.videoSrc,
                    round(getFrameRate(self.main.streamInfo['r_frame_rate']), 5),
                    self.main.streamInfo['width'],
                    self.main.streamInfo['height'])
        logger.info("Reference: %s @ %s fps | %s %s",
                    self.ref.videoSrc,
                    round(getFrameRate(self.ref.streamInfo['r_frame_rate']), 5),
                    self.ref.streamInfo['width'],
                    self.ref.streamInfo['height'])
        logger.info("Offset:     %s", self.offset)
        logger.info("Display:    %s", self.display)
        logger.info("Models:     %s", ', '.join(
            f'{run.spec.name} ({run.libvmaf_model})' for run in self.models))
        logger.info("pix_fmt:    %s", self.pix_fmt)
        logger.debug("loglevel:   %s", self.loglevel)
        logger.info("subsample:  %s", self.subsample)
        logger.info("output_fmt: %s", self.output_fmt)
        if self._hasRange():
            logger.info("Range:      frames %s, %s", self.start_frame or 0,
                        self.frame_count if self.frame_count is not None else "to the end")
        logger.info("=" * 39)


        log_path = None
        if self._hasRange():
            log_path = self._rangeLogPath()
            # A range past the end writes no log: never read one left by an earlier run.
            if os.path.exists(log_path):
                os.remove(log_path)
        self.ffmpegQos.getVmaf(self.models, subsample=self.subsample, log_path=log_path,
                               output_fmt=self.output_fmt, threads=self.threads, print_progress=self.print_progress, shortest=self.shortest, features=self.features, gpu=self.gpu_mode)
        log_path = self.ffmpegQos.vmafpath
        frames_scored = self._trimRangeLog(log_path) if self._hasRange() else None
        return VmafResult(
            scores=read_scores(log_path, self.output_fmt, model_names(self.models)),
            models=list(self.models),
            display=self.display,
            pix_fmt=self.pix_fmt,
            hfr=self.hfr_active,
            log_path=log_path,
            cambi_heatmap_path=self.cambi_heatmap_path,
            offset=self.offset,
            start_frame=(self.start_frame or 0) if self._hasRange() else None,
            frame_count=self.frame_count,
            frames_scored=frames_scored,
        )


def getFrameRate(r_frame_rate):
    num, den = r_frame_rate.split('/')
    return int(num)/int(den)
