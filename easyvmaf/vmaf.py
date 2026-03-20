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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging
import math
import os

logger = logging.getLogger(__name__)


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

    def __init__(self, mainSrc, refSrc, output_fmt, model="HD", phone=False, loglevel="info", subsample=1, threads=0, print_progress=False, end_sync=False,  manual_fps=0, cambi_heatmap=False, gpu_mode=False):
        self.loglevel = loglevel
        self.main = video(mainSrc, self.loglevel)
        self.ref = video(refSrc, self.loglevel)
        self.model = model
        self.phone = phone
        self.subsample = subsample
        self.gpu_mode = gpu_mode
        self.ffmpegQos = FFmpegQos(
            self.main.videoSrc, self.ref.videoSrc, self.loglevel,
            gpu_mode=gpu_mode)
        self.target_resolution = None
        self.offset = 0
        self.manual_fps = manual_fps
        self._initResolutions()
        self.output_fmt = output_fmt
        self.threads = threads
        self.print_progress = print_progress
        self.end_sync = end_sync
        self.cambi_heatmap = cambi_heatmap
        self._filters_applied = False


    def _initResolutions(self):
        """
        initialization of resolutions for each vmaf model
        """
        if self.model == 'HD':
            self.target_resolution = [1920, 1080]
        elif self.model == '4K':
            self.target_resolution = [3840, 2160]
        else:
            raise ValueError(f"Invalid VMAF model: {self.model!r}. Supported: HD, 4K")

    def _applyScaleFilters(self, qos):
        """Apply scale filters to the given FFmpegQos instance."""
        refResolution = [self.ref.streamInfo['width'],
                         self.ref.streamInfo['height']]
        mainResolution = [self.main.streamInfo['width'],
                          self.main.streamInfo['height']]
        if refResolution != self.target_resolution:
            if not qos.invertedSrc:
                qos.ref.setScaleFilter(
                    self.target_resolution[0], self.target_resolution[1])
            if qos.invertedSrc:
                qos.main.setScaleFilter(
                    self.target_resolution[0], self.target_resolution[1])

        if mainResolution != self.target_resolution:
            if not qos.invertedSrc:
                qos.main.setScaleFilter(
                    self.target_resolution[0], self.target_resolution[1])
            if qos.invertedSrc:
                qos.ref.setScaleFilter(
                    self.target_resolution[0], self.target_resolution[1])

    def _autoScale(self):
        """
        scaling MAIN and REF if they dont match with the resolution requiered by the vmaf model (target resolution)
        """
        if self._filters_applied:
            logger.warning(
                "_autoScale() called without clearFilters() since last application. "
                "Filter chains may contain duplicates. Call clearFilters() first."
            )
        self._applyScaleFilters(self.ffmpegQos)
        self._filters_applied = True

    def _deinterlaceFrame(self, factor, stream):
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        stream.setDeintFrameFilter()
        if round(ref_fps, 2) != round(factor*main_fps, 2):
            stream.setFpsFilter(round(main_fps, 5))

    def _deinterlaceField(self, factor, stream):

        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        stream.setDeintFieldFilter()
        if round(ref_fps, 2) != round(factor*main_fps, 2):
            stream.setFpsFilter(round(main_fps, 5))

    def _applyDeinterlaceFilters(self, qos):
        """Apply deinterlace/fps filters to the given FFmpegQos instance."""
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        if self.ref.interlaced == self.main.interlaced:
            """ Not Deinterlace would be required. So this functions normalizes the fps between REF and MAIN
            """
            if round(ref_fps) < round(main_fps):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                qos.main.setFpsFilter(round(ref_fps, 5))
            elif round(ref_fps) > round(main_fps):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                qos.ref.setFpsFilter(round(main_fps, 5))
            else:
                qos.main.setFpsFilter(round(main_fps, 5))
                qos.ref.setFpsFilter(round(ref_fps, 5))

        elif self.ref.interlaced and not self.main.interlaced:
            """
            REF interlaced  | MAIN progressive
            """
            if round(ref_fps) == round(main_fps*2):
                if not qos.invertedSrc:
                    self._deinterlaceFrame(2, qos.ref)
                else:
                    self._deinterlaceFrame(2, qos.main)

            elif round(ref_fps) == round(main_fps):
                if not qos.invertedSrc:
                    self._deinterlaceFrame(1, qos.ref)
                else:
                    self._deinterlaceFrame(1, qos.main)

            elif round(ref_fps) == round(main_fps/2):
                if not qos.invertedSrc:
                    self._deinterlaceField(0.5, qos.ref)
                else:
                    self._deinterlaceField(0.5, qos.main)

            else:
                raise UnsupportedFramerateError(
                    f"No deinterlace filter available for the given framerate combination. "
                    f"ref={round(ref_fps, 5)}fps (interlaced={self.ref.interlaced}), "
                    f"main={round(main_fps, 5)}fps (interlaced={self.main.interlaced}). "
                    f"Consider using the -fps flag to force a frame rate manually."
                )

        elif not self.ref.interlaced and self.main.interlaced:
            """
            Input Progressive (REF) | Output Interlaced (MAIN)
            """
            if round(ref_fps) == round(main_fps*2):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                if not qos.invertedSrc:
                    self._deinterlaceField(1, qos.main)
                else:
                    self._deinterlaceField(1, qos.ref)

            elif round(ref_fps) == round(main_fps):
                if not qos.invertedSrc:
                    self._deinterlaceFrame(1, qos.main)
                else:
                    self._deinterlaceFrame(1, qos.ref)

            elif round(ref_fps) == round(main_fps/2):
                logger.warning("Frame rate conversion can produce bad vmaf scores")
                if not qos.invertedSrc:
                    self._deinterlaceField(0.5, qos.main)
                else:
                    self._deinterlaceField(0.5, qos.ref)

            else:
                raise UnsupportedFramerateError(
                    f"No deinterlace filter available for the given framerate combination. "
                    f"ref={round(ref_fps, 5)}fps (interlaced={self.ref.interlaced}), "
                    f"main={round(main_fps, 5)}fps (interlaced={self.main.interlaced}). "
                    f"Consider using the -fps flag to force a frame rate manually."
                )

    def _autoDeinterlace(self):
        """
        This functions normalizes the framerate between MAIN and REF video streams (if needed)
        """
        self._applyDeinterlaceFilters(self.ffmpegQos)

    def _forceFps(self):
        logger.warning("Forcing frame rate conversion manually")
        self.ffmpegQos.main.setFpsFilter(self.manual_fps)
        self.ffmpegQos.main.setFpsFilter(self.manual_fps)

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

        qos.ref.setTrimFilter(offset, 0.5)
        qos.main.setTrimFilter(0, 0.5)
        self._applyScaleFilters(qos)
        if self.manual_fps == 0:
            self._applyDeinterlaceFilters(qos)
        else:
            qos.main.setFpsFilter(self.manual_fps)
            qos.ref.setFpsFilter(self.manual_fps)

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

        # Restore invertedSrc state on shared ffmpegQos so that getVmaf() works correctly
        if reverse:
            self.ffmpegQos.invertedSrc = True
            self.ffmpegQos.main.videoSrc, self.ffmpegQos.ref.videoSrc = \
                self.ffmpegQos.ref.videoSrc, self.ffmpegQos.main.videoSrc

        self.offset = -best_offset if reverse else best_offset
        return [self.offset, best_psnr]

    def setOffset(self, value=None):
        """
        Apply trim filters to synchronize main and distorted streams.

        Precondition: _autoScale() and _autoDeinterlace() (or _forceFps())
        must have been applied to self.ffmpegQos before calling this method.
        Trim filters are appended to the existing filter chain — they must
        come last in the sequence.

        If offset == 0, no filters are applied (streams are already in sync).

        If offset > 0: Ref delayed compared to Main. Trimfilter cuts Ref.
        If offset < 0: Main delayed compared to Ref. Trimfilter cuts Main.
        """

        if value != None:
            """ overrides the value in self.offset"""
            self.offset = value

        if self.offset > 0:
            offset = self.offset
            duration = min(self.main.duration, self.ref.duration-offset)
            self.ffmpegQos.ref.setTrimFilter(offset, duration)
            self.ffmpegQos.main.setTrimFilter(0, duration)

        elif self.offset < 0:
            offset = abs(self.offset)
            duration = min(self.main.duration - offset, self.ref.duration)
            self.ffmpegQos.main.setTrimFilter(offset, duration)
            self.ffmpegQos.ref.setTrimFilter(0, duration)

    def _build_feature_string(self) -> Optional[str]:
        """
        Build the libvmaf feature string from the current configuration.
        Returns None if no additional features are requested (uses model defaults).

        Features are pipe-separated: 'name=A\\\\:p=v|name=B\\\\:p=v'
        """
        features: List[FeatureConfig] = []

        # PSNR is always included — used for sync offset reporting
        features.append(FeatureConfig('psnr'))

        # CAMBI only when requested by the user
        if self.cambi_heatmap:
            cambi_params = {
                'full_ref':   'true',
                'enc_width':  str(self.main.streamInfo['width']),
                'enc_height': str(self.main.streamInfo['height']),
                'src_width':  str(self.ref.streamInfo['width']),
                'src_height': str(self.ref.streamInfo['height']),
            }
            features.append(FeatureConfig('cambi', cambi_params))

        if not features:
            return None

        return '|'.join(f.to_string() for f in features)

    def getVmaf(self, autoSync=False):
        """
        Run VMAF computation between main (distorted) and ref (reference) streams.

        Filter application contract — always in this order:
            1. clearFilters()     — reset all filter chains on ffmpegQos
            2. _autoScale()       — scale both streams to model target resolution
            3. _autoDeinterlace() — normalize frame rate and deinterlace if needed
               OR _forceFps()     — if manual_fps is set
            4. setOffset()        — apply trim filters for temporal sync

        Note: syncOffset() (when autoSync=True) is called between steps 3 and 4.
        After task-07, syncOffset() uses independent FFmpegQos instances per
        worker and does not mutate self.ffmpegQos filter chains, so step 3
        filters remain intact when setOffset() runs.

        Calling _autoScale() or _autoDeinterlace() without a preceding
        clearFilters() will stack duplicate filters — always clear first.
        """
        self.ffmpegQos.clearFilters()
        self.ffmpegQos.main.clearFilters()
        self.ffmpegQos.ref.clearFilters()
        self._filters_applied = False

        """AutoScale according to vmaf model and deinterlace the source if needed """
        self._autoScale()

        if self.manual_fps == 0:
            self._autoDeinterlace()
        else:
            self._forceFps()

        """Lookup for sync between Main and reference. Default: dissable
           It is suggested to run syncOffset manually before getVmaf()
        """
        if autoSync:
            self.syncOffset()
        """Apply Offset filters, if offset =0 nothing happens """
        self.setOffset()

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
        logger.info("Model:      %s", self.model)
        logger.info("Phone:      %s", self.phone)
        logger.debug("loglevel:   %s", self.loglevel)
        logger.info("subsample:  %s", self.subsample)
        logger.info("output_fmt: %s", self.output_fmt)
        logger.info("=" * 39)


        vmafProcess = self.ffmpegQos.getVmaf(model=self.model, subsample=self.subsample,
                                             output_fmt=self.output_fmt, threads=self.threads, print_progress=self.print_progress, end_sync=self.end_sync, features=self.features, cambi_heatmap=self.cambi_heatmap, gpu=self.gpu_mode)
        return vmafProcess


def getFrameRate(r_frame_rate):
    num, den = r_frame_rate.split('/')
    return int(num)/int(den)
