"""
MIT License

Copyright (c) 2020 Gabriel Davila - gdavila.revelo@gmail.com

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
from FFmpeg import FFprobe
from FFmpeg import FFmpegQos


class video():
    """
    Video class to parse information of video streams obtained
    by _FFmpeg.FFprobe
    """
    def __init__(self, videoSrc, loglevel = "info"):
        self.videoSrc = videoSrc
        self.streamInfo = None
        self.framesInfo = None
        self.packetsInfo = None
        self.formatInfo = None
        self.interlacedFrames = None
        self.totalFrames = None
        self.bytesFramesTotal = None
        self.interlaced = None
        self.loglevel = loglevel
        self.getStreamInfo()
        self.getFormatInfo()
        self.getFramesInfo()
        self.duration = self.getDuration()

    def _updateFramesSummary(self):
        interlacedFrames_count = 0
        bytesFramesTotal =0
        if self.framesInfo == None: return
        for frame in self.framesInfo:
            interlacedFrames_count = interlacedFrames_count + int(frame['interlaced_frame'])
            bytesFramesTotal = bytesFramesTotal + int(frame['pkt_size'])
        self.interlacedFrames = interlacedFrames_count
        self.totalFrames = len(self.framesInfo)
        self.bytesFramesTotal = bytesFramesTotal
        if int(round (self.interlacedFrames/self.totalFrames)): 
            self.interlaced= True
        else: 
            self.interlaced= False
        return

    def getDuration(self):
        try:
            duration = round(float(self.streamInfo['duration'])-float(self.streamInfo['start_time']))
            if duration < 0:
                duration = round(float(self.streamInfo['duration']))
        except KeyError:
            duration = round(float(self.formatInfo['duration'])-float(self.formatInfo['start_time']))
            if duration < 0:
                duration = round(float(self.formatInfo['duration']))
        return duration


    def getStreamInfo(self):
        print("\n\n=======================================", flush=True)
        print("[easyVmaf] Getting stream info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)

        
        self.streamInfo = FFprobe(self.videoSrc, self.loglevel).getStreamInfo()
        return self.streamInfo
    
    def getFramesInfo(self):
        print("\n\n=======================================", flush=True)
        print("[easyVmaf] Getting frames info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)
        self.framesInfo = FFprobe(self.videoSrc, self.loglevel).getFramesInfo()
        self._updateFramesSummary()
        return self.framesInfo


    def getPacketsInfo(self):
        print("\n\n=======================================", flush=True)
        print("[easyVmaf] Getting packets info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)
        self.packetsInfo = FFprobe(self.videoSrc, self.loglevel).getPacketsInfo()
        return self.packetsInfo

    
    def getFormatInfo(self):
        print("\n\n=======================================", flush=True)
        print("[easyVmaf] Getting format info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)
        self.formatInfo = FFprobe(self.videoSrc, self.loglevel).getFormatInfo()
        return self.formatInfo


class vmaf():
    """
    Video class to manage VMAF computation of video streams by using
    by _FFmpeg.FFmpegQos. This class allows:
        - To upscale or downscale the MAIN or REF videos automatically according to the Vmaf model
        - Deinterlace automatically the MAIN and REF videos if needed
        - To SYNC the MAIN and REF videos using psnr computation 
        - TO DO: frame rate conversion 
    """
    def __init__(self, mainSrc, refSrc, model = "HD", phone = False, loglevel = "info", subsample = 1):
        self.loglevel = loglevel
        self.main = video(mainSrc,self.loglevel)
        self.ref = video(refSrc,self.loglevel)
        self.model = model
        self.phone = phone
        self.subsample = subsample
        self.ffmpegQos = FFmpegQos(self.main.videoSrc, self.ref.videoSrc, self.loglevel)
        self.target_resolution = None
        self.offset = 0
        self._initResolutions()


    def _initResolutions(self):
        """ 
        initialization of resolutions of each model
        """
        if self.model == 'HD' or self.model == 'HDneg':
            self.target_resolution = [1920, 1080]
        elif self.model == '4K':
            self.target_resolution = [3840,2160]
        else:
            exit("[easyVmaf] ERROR: Invalid vmaf model")


    def _autoScale(self):
        """ 
        scaling MAIN and REF if they doesnt match with the resolution requiered by the vmaf model
        """
        refResolution = [self.ref.streamInfo['width'], self.ref.streamInfo['height']]
        mainResolution = [self.main.streamInfo['width'], self.main.streamInfo['height']]
        if refResolution != self.target_resolution:
            if not self.ffmpegQos.invertedSrc:
                self.ffmpegQos.ref.setScaleFilter(self.target_resolution[0], self.target_resolution[1])
            if self.ffmpegQos.invertedSrc:
                self.ffmpegQos.main.setScaleFilter(self.target_resolution[0], self.target_resolution[1])
                   
        if mainResolution != self.target_resolution:
            if not self.ffmpegQos.invertedSrc:
                self.ffmpegQos.main.setScaleFilter(self.target_resolution[0], self.target_resolution[1])
            if self.ffmpegQos.invertedSrc:
                self.ffmpegQos.ref.setScaleFilter(self.target_resolution[0], self.target_resolution[1])


    def _deinterlaceFrame(self, factor, stream):
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        stream.setDeintFrameFilter()
        if round(ref_fps,2)!=round(factor*main_fps,2):
            stream.setFpsFilter(round(main_fps,5))
      

    def _deinterlaceField(self, factor, stream):
        
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        stream.setDeintFieldFilter()            
        if round(ref_fps,2)!=round(factor*main_fps,2):
            stream.setFpsFilter(round(main_fps,5))
        

    def _autoDeinterlace(self):
        ref_fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        main_fps = getFrameRate(self.main.streamInfo['r_frame_rate'])

        if self.ref.interlaced ==  self.main.interlaced : 
            """ 
            REF interlaced | MAIN interlaced
            """
            if round(ref_fps ) < round(main_fps):
                print("[easyVmaf] Warning: Frame rate conversion can produce bad vmaf scores", flush=True)
                self.ffmpegQos.main.setFpsFilter(round(ref_fps,5))
            elif round(ref_fps ) > round(main_fps):
                print("[easyVmaf] Warning: Frame rate conversion can produce bad vmaf scores", flush=True)
                self.ffmpegQos.ref.setFpsFilter(round(main_fps,5))
            else:
                pass

        elif self.ref.interlaced and not self.main.interlaced :
            """ 
            REF interlaced  | MAIN progressive
            """ 
            if round(ref_fps ) == round(main_fps*2): 
                # REF=60i, MAIN=30p
                # REF=59.97i, MAIN=30p, etc
                if not self.ffmpegQos.invertedSrc: self._deinterlaceFrame(2, self.ffmpegQos.ref )
                else: self._deinterlaceFrame(2, self.ffmpegQos.main )

            elif round(ref_fps) == round(main_fps): 
                # REF=30i, MAIN=30p
                # REF=29.97i, MAIN=30p, etc
                if not self.ffmpegQos.invertedSrc: self._deinterlaceFrame(1, self.ffmpegQos.ref )
                else: self._deinterlaceFrame(1, self.ffmpegQos.main )

            elif round(ref_fps) == round(main_fps/2): 
                # REF=30i, MAIN=60p
                # REF=29.97i, MAIN=60p, etc
                if not self.ffmpegQos.invertedSrc: self._deinterlaceField(0.5, self.ffmpegQos.ref )
                else: self._deinterlaceField(0.5, self.ffmpegQos.main )
            
            else:
                print("[easyVmaf] ERROR: No Filters available for the given Framerates", flush=True)

        elif not self.ref.interlaced and self.main.interlaced:
            """ 
            Input Progressive (REF) | Output Interlaced (MAIN)
            """
            if round(ref_fps ) == round(main_fps*2):
                # REF=60p, MAIN=30i
                # REF=60p, MAIN=29.97i, etc
                print("[easyVmaf] Warning: Frame rate conversion can produce bad vmaf scores", flush=True)
                if not self.ffmpegQos.invertedSrc: self._deinterlaceField(1, self.ffmpegQos.main)
                else: self._deinterlaceField(1, self.ffmpegQos.ref)

            elif round(ref_fps ) == round(main_fps):
                # REF=30p, MAIN=30i
                # REF=30p, MAIN=29.97i, etc
                if not self.ffmpegQos.invertedSrc: self._deinterlaceFrame(1, self.ffmpegQos.main)
                else: self._deinterlaceFrame(1, self.ffmpegQos.ref)

            else:
                print("[easyVmaf] ERROR: No Filters available for the given Framerates", flush=True)
            
            
        

    def syncOffset(self, syncWindow = 3, start=0, reverse = False):
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

        print("\n\n=======================================", flush=True)
        print("Syncing... Computing PSNR values... ", flush=True)
        print("=======================================", flush=True)
        print("Distorted:" , self.main.videoSrc , "@" , round(getFrameRate(self.main.streamInfo['r_frame_rate']),5) , "fps","|" ,self.main.streamInfo['width'], self.main.streamInfo['height'] , flush=True)
        print("Reference:" , self.ref.videoSrc , "@" , round(getFrameRate(self.ref.streamInfo['r_frame_rate']),5) , "fps","|" ,self.ref.streamInfo['width'], self.ref.streamInfo['height'],  flush=True)
        print("=======================================", flush=True)
        print("offset(s)","\t\t","psnr[dB]", flush=True)


        if reverse: self.ffmpegQos.invertSrcs()
        fps = getFrameRate(self.ref.streamInfo['r_frame_rate'])
        frameDuration = 1/fps
        startFrame = int(round(start/frameDuration))
        framesInSyncWindow = int(round(syncWindow/frameDuration))
        psnr ={'value':[], 'time':[]}


        for i in range(0,framesInSyncWindow ):
            offset = (startFrame + i) * frameDuration
            self.ffmpegQos.main.clearFilters()
            self.ffmpegQos.ref.clearFilters()
            self.ffmpegQos.ref.setTrimFilter(offset, 0.5 )
            self.ffmpegQos.main.setTrimFilter(0, 0.5 )
            self._autoScale()
            self._autoDeinterlace()
            psnr['value'].append(self.ffmpegQos.getPsnr())
            psnr['time'].append(offset)
            print (psnr['time'][i], "\t", psnr['value'][i], flush= True)

        maxPsnr = max(psnr['value'])
        index = psnr['value'].index(maxPsnr)
        offset = psnr['time'][index]
        if reverse: 
            self.ffmpegQos.invertSrcs()
            offset = -1 * offset
        self.offset = offset

        return [self.offset, maxPsnr]


    def setOffset(self, value=None):
        """
        Apply Offset to trim Filter. 
            If offset > 0: Ref delayed compared to  Main. Trimfilter cuts Ref
            if offset < 0: Main delayed compared to Ref. Trimfilter cuts Main
        """

        if value != None:
            """ overrides the value in self.offset"""
            self.offset = value

        if self.offset > 0:
            offset = self.offset
            duration = min(self.main.duration, self.ref.duration-offset)
            self.ffmpegQos.ref.setTrimFilter(offset, duration )
            self.ffmpegQos.main.setTrimFilter(0, duration )

        elif self.offset < 0:
            offset = abs(self.offset)
            duration = min(self.main.duration - offset, self.ref.duration)
            self.ffmpegQos.main.setTrimFilter(offset, duration )
            self.ffmpegQos.ref.setTrimFilter(0, duration)

    def getVmaf(self, autoSync = False):


        """ clean all filters first """
        self.ffmpegQos.clearFilters()
        self.ffmpegQos.main.clearFilters()
        self.ffmpegQos.ref.clearFilters()
        
        """AutoScale according to vmaf model and deinterlace the source if needed """
        self._autoScale()
        self._autoDeinterlace()

        """Lookup for sync between Main and reference. Default: dissable
           It is suggested to run syncOffset manually before getVmaf()
        """
        if autoSync: self.syncOffset()
        """Apply Offset filters, if offset =0 nothing happens """
        self.setOffset()

        print("\n\n=======================================", flush=True)
        print("Computing VMAF... ", flush=True)
        print("=======================================", flush=True)
        print("Distorted:" , self.main.videoSrc , "@" , round(getFrameRate(self.main.streamInfo['r_frame_rate']),5) , "fps","|" ,self.main.streamInfo['width'], self.main.streamInfo['height'] , flush=True)
        print("Reference:" , self.ref.videoSrc , "@" , round(getFrameRate(self.ref.streamInfo['r_frame_rate']),5) , "fps","|" ,self.ref.streamInfo['width'], self.ref.streamInfo['height'],  flush=True)
        print("Offset:", self.offset, flush=True)
        print("Model:", self.model, flush=True)
        print("Phone:", self.phone, flush=True)
        print("loglevel:", self.loglevel, flush=True)
        print("subsample:", self.subsample, flush=True)
        print("=======================================", flush=True)

        self.ffmpegQos.getVmaf(model = self.model, phone = self.phone, subsample= self.subsample)


def getFrameRate(r_frame_rate):
    num, den = r_frame_rate.split('/')
    return int(num)/int(den)
