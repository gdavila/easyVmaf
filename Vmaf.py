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
        self.interlacedFrames = None
        self.totalFrames = None
        self.bytesFramesTotal = None
        self.duration = None
        self.interlaced = None
        self.loglevel = loglevel
        self.getStreamInfo()
        self.getFramesInfo()

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

    def getStreamInfo(self):
        print("\n\n=======================================", flush=True)
        print("Getting stream info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)

        self.streamInfo = FFprobe(self.videoSrc, self.loglevel).getStreamInfo()
        self.duration = round(float(self.streamInfo['duration'])-float(self.streamInfo['start_time']))
        if self.duration < 0:
            self.duration = round(float(self.streamInfo['duration']))
        return self.streamInfo
    
    def getFramesInfo(self):
        print("\n\n=======================================", flush=True)
        print("Getting frames info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)
        self.framesInfo = FFprobe(self.videoSrc, self.loglevel).getFramesInfo()
        self._updateFramesSummary()
        return self.framesInfo


    def getPacketsInfo(self):
        print("\n\n=======================================", flush=True)
        print("Getting packets info...", self.videoSrc ,flush=True)
        print("=======================================", flush=True)
        self.packetsInfo = FFprobe(self.videoSrc, self.loglevel).getPacketsInfo()
        return self.packetsInfo


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
        self.main = video(mainSrc)
        self.ref = video(refSrc)
        self.model = model
        self.phone = phone
        self.loglevel = loglevel
        self.subsample = subsample
        self.ffmpegQos = FFmpegQos(self.main.videoSrc, self.ref.videoSrc, self.loglevel)
        self.target_resolution = None
        self.offset = 0
        self._initResolutions()


    def _initResolutions(self):
        """ 
        initialization of resolutions of each model
        """
        if self.model == 'HD':
            self.target_resolution = [1920, 1080]
        elif self.model == '4K':
            self.target_resolution = [3840,2160]


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


    def _autoDeinterlace(self):
        """ 
        Deinterlace MAIN and REF if it is requiered
        """

        if self.ref.interlaced and  self.main.interlaced : 
            """ 
            Input interlaced | Output Interlaced
            """
            pass

        elif self.ref.interlaced and not self.main.interlaced :
            """ 
            Input interlaced (REF) | Output progressive (MAIN)
            """ 
            if round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])*2):
                """
                one Frame at output per two fields at input: 
                i. e., 60i-> 30p
                => Deinterlace input (60i to 60p) and upscale the fps at output (30p to 60p)
                """
                if not self.ffmpegQos.invertedSrc:
                    self.ffmpegQos.ref.setDeintFrameFilter()
                    self.ffmpegQos.main.setFpsFilter(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5)
                if self.ffmpegQos.invertedSrc:
                    self.ffmpegQos.main.setDeintFrameFilter()
                    self.ffmpegQos.ref.setFpsFilter(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5)


            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """
                one Frame at output per one Frame at input: 
                i. e., 30i ->  30p
                => Deinterlace input (30i to 30p)
                """
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFrameFilter()
                    self.ffmpegQos.main.setFpsFilter(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5)

                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFrameFilter()
                    self.ffmpegQos.ref.setFpsFilter(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5)


            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])/2):
                """
                one Frame at output pero one Field at input: 
                i. e., 30i ->  60p
                => Deinterlace input (30i to 60p)
                """
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFieldFilter()
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFieldFilter()
            else: 
                print("Unable to find the deinterlace filter, check fps of MAIN", flush=True)

        elif not self.ref.interlaced and self.main.interlaced:
            """ 
            Input Progressive (REF) | Output Interlaced (MAIN)
            """
            if round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])*2):
                """
                i,e,.: 60p -> 30i
                => Deinterlace output (30i to 60p)
                """
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFieldFilter()
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFieldFilter()
            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """
                i,e,. 30p -> 30i
                => Deinterlace output (30i to 30p)
                """
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFrameFilter()
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFrameFilter()
            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])*2) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """
                i,e,. 30p -> 60i
                => Deinterlace output (60i to 60p) and downscale the fps (60p to 30p)
                """
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFrameFilter()
                    self.ffmpegQos.main.setFpsFilter(round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5))
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFrameFilter()
                    self.ffmpegQos.ref.setFpsFilter(round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5))
              
        elif not self.main.interlaced and not self.ref.interlaced:
            """ Input Progressive Output Progresive
                TO DO: Frame Conversion if main_fps != ref_fps
            """
            pass

        

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
        print("Distorted:" , self.main.videoSrc , "@" , round(getFrameRate(self.main.streamInfo['avg_frame_rate']),5) , "fps","|" ,self.main.streamInfo['width'], self.main.streamInfo['height'] , flush=True)
        print("Reference:" , self.ref.videoSrc , "@" , round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5) , "fps","|" ,self.ref.streamInfo['width'], self.ref.streamInfo['height'],  flush=True)
        print("=======================================", flush=True)
        print("offset(s)","\t\t","psnr[dB]", flush=True)


        if reverse: self.ffmpegQos.invertSrcs()
        fps = getFrameRate(self.ref.streamInfo['avg_frame_rate'])
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
        print("Distorted:" , self.main.videoSrc , "@" , round(getFrameRate(self.main.streamInfo['avg_frame_rate']),5) , "fps","|" ,self.main.streamInfo['width'], self.main.streamInfo['height'] , flush=True)
        print("Reference:" , self.ref.videoSrc , "@" , round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5) , "fps","|" ,self.ref.streamInfo['width'], self.ref.streamInfo['height'],  flush=True)
        print("Offset:", self.offset, flush=True)
        print("Model:", self.model, flush=True)
        print("Phone:", self.phone, flush=True)
        print("loglevel:", self.loglevel, flush=True)
        print("subsample:", self.subsample, flush=True)
        print("=======================================", flush=True)

        self.ffmpegQos.getVmaf(model = self.model, phone = self.phone, subsample= self.subsample)


def getFrameRate(avg_frame_rate):
    num, den = avg_frame_rate.split('/')
    return int(num)/int(den)
