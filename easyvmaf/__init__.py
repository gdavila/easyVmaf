"""
easyVmaf — FFmpeg-based VMAF computation with automatic preprocessing.

Public API: the names exported here. The submodules (easyvmaf.vmaf,
easyvmaf.ffmpeg, ...) are internal and may change in any release.

    from easyvmaf import Vmaf

    calculation = Vmaf('dist.mp4', 'ref.mp4', display='hd')
    offset, psnr = calculation.sync(2)   # optional: or Vmaf(..., sync_offset=1.5)
    result = calculation.compute()
    result.scores    # {'vmaf_v1_hd': ..., 'vmaf_v1_phone': ...}
"""
from .vmaf import (
    Vmaf,
    validate_model_config,
    UnsupportedFramerateError,
    UnsupportedModelConfigError,
    validate_range_config,
    UnsupportedRangeError,
)
from .results import SyncResult, VmafResult
from .models import ModelRun, ModelSpec, CATALOG, select_models
from .ffmpeg import FFmpegExecutionError, check_ffmpeg

__version__ = "5.0.0"
__all__ = [
    "Vmaf",
    "VmafResult",
    "SyncResult",
    "validate_model_config",
    "validate_range_config",
    "UnsupportedModelConfigError",
    "UnsupportedRangeError",
    "UnsupportedFramerateError",
    "FFmpegExecutionError",
    "ModelSpec",
    "ModelRun",
    "CATALOG",
    "select_models",
    "check_ffmpeg",
    "__version__",
]
