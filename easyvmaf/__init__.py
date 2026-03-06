"""
easyVmaf — FFmpeg-based VMAF computation with automatic preprocessing.

Public API:
    from easyvmaf import vmaf, UnsupportedFramerateError
    from easyvmaf.ffmpeg import FFprobe, FFmpegQos, inputFFmpeg
"""
from .vmaf import vmaf, UnsupportedFramerateError
from .ffmpeg import FFprobe, FFmpegQos, inputFFmpeg

__version__ = "2.1.0"
__all__ = [
    "vmaf",
    "UnsupportedFramerateError",
    "FFprobe",
    "FFmpegQos",
    "inputFFmpeg",
]
