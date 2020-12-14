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

ffmpeg = '/usr/local/bin/ffmpeg'
ffprobe = '/usr/local/bin/ffprobe'

# uncomment for vmaf version < v2.0.0.0
#vmaf_4K = '/usr/local/share/model/vmaf_4k_v0.6.1.pkl'
#vmaf_HD = '/usr/local/share/model/vmaf_v0.6.1.pkl'
#vmaf_HDneg = '/usr/local/share/model/vmaf_v0.6.1neg.pkl'


# vmaf v2.0.0
vmaf_4K = '/usr/local/share/model/vmaf_4k_v0.6.1.json'
vmaf_HD = '/usr/local/share/model/vmaf_float_v0.6.1.json'
vmaf_HDneg = '/usr/local/share/model//vmaf_float_v0.6.1neg.json'
