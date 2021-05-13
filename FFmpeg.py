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


import config 
import subprocess
import json
import os

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
    cmd = config.ffprobe
    def __init__ (self, videoSrc, loglevel = "info"):
        self.videoSrc = videoSrc
        self.loglevel = loglevel
        self.streamInfo = None
        self.framesInfo = None
        self.packetsInfo = None

    ''' private methods '''
    def _commit(self, opt):
        self.cmd =  f'{FFprobe.cmd} -hide_banner -loglevel {self.loglevel} -print_format json {opt} -select_streams v -i \"{self.videoSrc}\" -read_intervals %+5'
    
    def _run(self):
        if self.loglevel == "verbose":
            print(self.cmd, flush=True)
        return json.loads(subprocess.check_output(self.cmd, shell=True))
    
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
    cmd = config.ffmpeg

    def __init__ (self,  main, ref , loglevel = "info"):
        self.loglevel = loglevel
        self.cmd = None
        self.main = inputFFmpeg(main, input_id =0)
        self.ref = inputFFmpeg(ref, input_id =1)
        self.psnrFilter = []
        self.vmafFilter = []
        self.invertedSrc = False
        self.vmafpath = None

    def _commit(self):
        """build the final cmd to run"""
        baseCmd = f'{FFmpegQos.cmd} -y -hide_banner -stats -loglevel {self.loglevel} '
        inputsCmd = self._commitInputs()
        filterCmd = self._commitFilters()
        outputCmd = self._commitOutputs()
        self.cmd = f'{baseCmd} {inputsCmd} {filterCmd} {outputCmd}'

    def _commitInputs(self):
        """build the cmd for the inputs files"""
        inputCmd = f'-i \"{self.main.videoSrc}\" -i \"{self.ref.videoSrc}\" -map 0:v -map 1:v'
        return  inputCmd

    def _commitOutputs(self):
        return "-f null -"

    def _commitFilters(self, filterName = 'lavfi'):
        """build the cmd for the filters"""
        filterCmd = f'-{filterName} \"{";".join(self.main.filtersList + self.ref.filtersList + self.psnrFilter + self.vmafFilter)}\"'
        return filterCmd

    def getPsnr(self, stats_file = False):
        """ 
        It adds PSNR filter to lavfi chain and run the ffmpeg cmd.
        The output is returned and saved as stats_file_psnr.log
        """
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID
        if stats_file == True:
            stats_file = os.path.splitext(self.main.videoSrc)[0]+ '_psnr.log'
        else: stats_file = 'stats_file_psnr.log'

        self.psnrFilter = [f'[{main}][{ref}]psnr=stats_file={stats_file}']       
        self._commit()

        if self.loglevel == "verbose": print(self.cmd, flush=True)
        stdout = (subprocess.check_output(self.cmd,stderr=subprocess.STDOUT, shell=True)).decode('utf-8')
        stdout = stdout.split(" ")
        psnr = [s for s in stdout if "average" in s][0].split(":")[1]
        return float(psnr)


    def getVmaf(self, log_path= None, model= 'HD', phone = False, subsample = 1,output_fmt='json', threads = 0):
        main = self.main.lastOutputID
        ref = self.ref.lastOutputID
        if output_fmt=='xml':
            log_fmt = "xml"
            if log_path == None:
                log_path = os.path.splitext(self.main.videoSrc)[0]+ '_vmaf.xml'
        else:
            log_fmt = "json"
            if log_path == None:
                log_path = os.path.splitext(self.main.videoSrc)[0]+ '_vmaf.json'
        self.vmafpath = log_path
        if model =='HD': 
            model_path = config.vmaf_HD
            phone_model = int (phone)
        elif model =='HDneg': 
            model_path = config.vmaf_HDneg
            phone_model = int (phone)
        elif model == '4K': 
            model_path = config.vmaf_4K
            phone_model = 0
        if threads == 0: threads = os.cpu_count()

        self.vmafFilter = [f'[{main}][{ref}]libvmaf=log_fmt={log_fmt}:model_path={model_path}:phone_model={phone_model}:n_subsample={subsample}:log_path={log_path}:n_threads={threads}']

        self._commit()
        if self.loglevel == "verbose": print(self.cmd, flush=True)
        process = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, shell=True)
        process.communicate()
        return process
    
    def clearFilters(self):
        self.psnrFilter = []
        self.vmafFilter = []      

    def invertSrcs(self):
        temp1 = self.main.videoSrc
        temp2 = self.ref.videoSrc
        invertedSrc = self.invertedSrc
        self.__init__(temp2,temp1, self.loglevel)
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
    def __init__(self, videoSrc, input_id ):
        self.name = f'input{input_id}_'
        self.id = input_id
        self.videoSrc = videoSrc
        self.filtersList =[]
        self.extraOptions = []
        self.lastOutputID =  f'{str(self.id)}:v'

    def _setFilter(self,filter):
        self.filtersList.append(filter)

    def _newInOutForFilter(self):
        self.n = len(self.filtersList)
        if self.n == 0:
            inputID = f'{str(self.id)}:v'
            outputID = f'{self.name}{str(self.n)}' 
        else:
            inputID =  f'{self.name}{str(self.n-1)}'
            outputID = f'{self.name}{str(self.n)}'
        return inputID, outputID

    def _updateOutputId(self, outputID ):
        self.lastOutputID = outputID
    
    def setScaleFilter(self, width, height, algo = 'bicubic'):
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
        self.filtersList =[]
        self.lastOutputID =  f'{str(self.id)}:v'
