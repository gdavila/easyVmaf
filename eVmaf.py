
from FFmpeg import FFprobe
from FFmpeg import FFmpegQos
import  argparse
from statistics import mean
import json

class video():
    """
    Video class to manage information of video streams obtained
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

    def getStreamInfo(self):
        self.streamInfo = FFprobe(self.videoSrc, self.loglevel).getStreamInfo()
        self.duration = round(float(self.streamInfo['duration'])-float(self.streamInfo['start_time']))
        if self.duration < 0:
            self.duration = round(float(self.streamInfo['duration']))
        return self.streamInfo
    
    def getFramesInfo(self):
        self.framesInfo = FFprobe(self.videoSrc, self.loglevel).getFramesInfo()
        self.updateFramesSummary()
        return self.framesInfo

    def updateFramesSummary(self):
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

    def getPacketsInfo(self):
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
    def __init__(self, mainSrc, refSrc, model = "HD", phone = False, loglevel = "info"):
        self.main = video(mainSrc)
        self.ref = video(refSrc)
        self.model = model
        self.phone = phone
        self.loglevel = loglevel
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
            """ Input interlaced | Output Interlaced"""
            pass

        elif self.ref.interlaced and not self.main.interlaced :
            """ REF interlaced | MAIN progressive
                Most common case
            """ 
            
            if round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])*2):
                """one Frame to one Frame deinterlace: 60i-> 30p"""
                if not self.ffmpegQos.invertedSrc:
                    print("60i-> 30p", "INVERTED FALSE", flush=True)
                    self.ffmpegQos.ref.setDeintFrameFilter()
                    self.ffmpegQos.main.setFpsFilter(round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5))
                if self.ffmpegQos.invertedSrc:
                    print("60i-> 30p", "INVERTED TRUE", flush=True)

                    self.ffmpegQos.main.setDeintFrameFilter()
                    self.ffmpegQos.ref.setFpsFilter(round(getFrameRate(self.ref.streamInfo['avg_frame_rate']),5))


            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """one Frame output per one Field input: 30i ->  30p"""
                if not self.ffmpegQos.invertedSrc: 
                    print("30i-> 30p", "INVERTED FALSE", flush=True)
                    self.ffmpegQos.ref.setDeintFrameFilter()
                if self.ffmpegQos.invertedSrc: 
                    print("30i-> 30p", "INVERTED true", flush=True)
                    self.ffmpegQos.main.setDeintFrameFilter()
            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])/2):
                """one Frame output one Frame deinterlace: 30i ->  60p"""

                if not self.ffmpegQos.invertedSrc: 
                    print("30i-> 60p", "INVERTED FALSE", flush=True)
                    self.ffmpegQos.ref.setDeintFieldFilter()
                if self.ffmpegQos.invertedSrc: 
                    print("30i-> 60p", "INVERTED TRUE", flush=True)
                    self.ffmpegQos.main.setDeintFieldFilter()
            else: 
                print("Unable to find the deinterlace filter, check fps of MAIN", flush=True)

        elif not self.ref.interlaced and self.main.interlaced:
            """ REF Progressive | MAIN Interlaced
                TO DO: Frame Conversion if main_fps != ref_fps 
                Deinterlace of MAIN Video
            """
            if round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])*2):
                """one Frame to one Frame deinterlace: 60p -> 30i"""
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFieldFilter()
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFieldFilter()
            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """one Frame to one Frame deinterlace: 30p -> 30i"""
                if not self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.main.setDeintFrameFilter()
                if self.ffmpegQos.invertedSrc: 
                    self.ffmpegQos.ref.setDeintFrameFilter()
            elif round(getFrameRate(self.ref.streamInfo['avg_frame_rate'])*2) == round(getFrameRate(self.main.streamInfo['avg_frame_rate'])):
                """one Frame to one Frame deinterlace: 30p -> 60i"""
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

    def setOffset(self, value=None):
        """
        Apply Offset to trim Filter. 
            If offset > 0: Ref delayed compared to  Main. Trimfilter cuts Ref
            if offset < 0: Main delayed compared to Ref. Trimfilter cuts Main
        """

        if value != None:
            self.offset = value
        duration = min(self.main.duration, self.ref.duration)
        if self.offset > 0:
            offset = self.offset
            self.ffmpegQos.ref.setTrimFilter(offset, duration-offset )
            self.ffmpegQos.main.setTrimFilter(0, duration-offset )

        elif self.offset < 0:
            offset = abs(self.offset)
            self.ffmpegQos.main.setTrimFilter(offset, duration-offset )
            self.ffmpegQos.ref.setTrimFilter(0, duration-offset )
        

    def syncOffset(self, syncWindow = 3, start=0, reverse = False):
        """
        Method to get the offset needed to sync REF and MAIN (if any)
            syncWindow -->  Window Size in seconds to try of syncing videos. i.e., if the video to sync
                            last 60 seconds, the sync will be done just within the syncWindow interval
            start -->  start time in seconds where the syncWindow begin.
            revere --> By default, it suppused that the REF is delayed regarding the MAIN. If this option
                        is set to TRUE. It is considered that MAIN is delayed regarding REF.
        """
        print ("INVERTED IN: ",self.ffmpegQos.invertedSrc, flush= True)
        if reverse: self.ffmpegQos.invertSrcs()
        print ("INVERTED AFTER: ",self.ffmpegQos.invertedSrc, flush= True)
        fps = getFrameRate(self.ref.streamInfo['avg_frame_rate'])
        frameDuration = 1/fps
        startFrame = int(round(start/frameDuration))
        framesInSyncWindow = int(round(syncWindow/frameDuration))
        psnr ={'value':[], 'time':[]}

        print ("", flush=True)
        print ("Getting PSNR values for sync...", flush=True)
        print ("", flush=True)

        for i in range(0,framesInSyncWindow ):
            offset = (startFrame + i) * frameDuration
            self.ffmpegQos.main.clearFilters()
            self.ffmpegQos.ref.clearFilters()
            self.ffmpegQos.ref.setTrimFilter(offset, 0.5 )
            self.ffmpegQos.main.setTrimFilter(0, 0.5 )

            """ This need to be improved to move the code outside the "for"
                It could be done by adding a "clearFilter method" to ffmpegQos but refactoring is needed
            """
            self._autoScale()
            self._autoDeinterlace()
            psnr['value'].append(self.ffmpegQos.getPsnr())
            psnr['time'].append(offset)
            print ("offset: ", psnr['time'][i], "psnr[dB]: ", psnr['value'][i], flush= True)
        maxPsnr = max(psnr['value'])
        index = psnr['value'].index(maxPsnr)
        offset = psnr['time'][index]
        if reverse: 
            self.ffmpegQos.invertSrcs()
            offset = -1 * offset
        self.offset = offset
        print ("INVERTED OUT: ",self.ffmpegQos.invertedSrc, flush=True)

        return [offset, maxPsnr]

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
        self.ffmpegQos.getVmaf()


def getFrameRate(avg_frame_rate):
    num, den = avg_frame_rate.split('/')
    return int(num)/int(den)

def get_args():
    '''This function parses and return arguments passed in'''
    parser = argparse.ArgumentParser(prog = 'eVmaf', description = 'script to easy compute VMAF using FFmpeg. It allows to deinterlace, scale and sync Ref and Distorted videos')
    parser.add_argument('-i' , dest='i', type = str, help = 'main  video sample to compute VMAF (distorted)')
    parser.add_argument('-r' , dest='r', type = str, help = 'Reference video sample to compute VMAF')
    parser.add_argument('-sw', dest='sw', type = int, default = 0, help='synchronisation windows in seconds(default=0)')
    parser.add_argument('-ss',dest='ss', type = int, default = 0, help="start time to sync(default=0).It specifies in seconds where the sync Windows begin. If [-r] is dissabled [ss] it is for reference video, otherwise it is for main video" )
    parser.add_argument('-reverse', help="Vmaf Model. Options: HD, 4K. Default: HD", action = 'store_true' )
    parser.add_argument('-model', dest='model', type = str, default = "HD", help="Vmaf Model. Options: HD, 4K. Default: HD" )
    parser.add_argument('-phone' , help =  'Activate phone vmaf (HD only)', action = 'store_true')
    parser.add_argument('-verbose' , help =  'Activate verbose loglevel. Default: info', action = 'store_true')

    return parser.parse_args()


if __name__ == '__main__':
    cmdParser=get_args()
    main = cmdParser.i
    reference = cmdParser.r
    syncWin = cmdParser.sw
    ss = cmdParser.ss
    reverse = cmdParser.reverse
    model = cmdParser.model
    phone = cmdParser.phone
    verbose = cmdParser.verbose
    if verbose: 
        loglevel = "verbose"
    else:
        loglevel = "info"

    myVmaf = vmaf(main, reference, loglevel=loglevel)
    print("SYNCING")
    offset, psnr = myVmaf.syncOffset(syncWin, ss, reverse)
    myVmaf.getVmaf()
    vmafpath = myVmaf.ffmpegQos.vmafpath
    vmafScore = []
    with open (vmafpath) as jsonFile:
        jsonData = json.load(jsonFile)
        for frame in jsonData['frames']:
            vmafScore.append(frame["metrics"]["vmaf"])
    
    print("\n \n \n \n \n ")
    print("Sync Info: ")
    print("offset: ", offset, "psnr: ", psnr)
    print("VMAF score: ", mean(vmafScore))
    print("VMAF json File Path: ", myVmaf.ffmpegQos.vmafpath )
