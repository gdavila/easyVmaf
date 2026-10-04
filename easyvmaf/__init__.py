"""
easyVmaf — FFmpeg-based VMAF computation with automatic preprocessing.

Public API:
    from easyvmaf import vmaf, VmafResult
    from easyvmaf import validate_model_config, UnsupportedModelConfigError
    from easyvmaf import UnsupportedFramerateError
    from easyvmaf import validate_range_config, UnsupportedRangeError
    from easyvmaf import ModelSpec, CATALOG, select_models
    from easyvmaf.ffmpeg import FFprobe, FFmpegQos, inputFFmpeg

    result = vmaf('dist.mp4', 'ref.mp4', display='hd').getVmaf()
    result.scores    # {'vmaf_v1_hd': ..., 'vmaf_v1_phone': ...}
"""
from .vmaf import (
    vmaf,
    validate_model_config,
    UnsupportedFramerateError,
    UnsupportedModelConfigError,
    validate_range_config,
    UnsupportedRangeError,
)
from .results import VmafResult
from .models import ModelSpec, CATALOG, select_models
from .ffmpeg import FFprobe, FFmpegQos, inputFFmpeg

__version__ = "4.0.0"
__all__ = [
    "vmaf",
    "VmafResult",
    "validate_model_config",
    "UnsupportedFramerateError",
    "UnsupportedModelConfigError",
    "validate_range_config",
    "UnsupportedRangeError",
    "ModelSpec",
    "CATALOG",
    "select_models",
    "FFprobe",
    "FFmpegQos",
    "inputFFmpeg",
]
