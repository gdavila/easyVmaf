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
# VMAF model catalog. Pure data module (no FFmpeg dependency): every VMAF model
# easyVmaf can compute is a ModelSpec in CATALOG. select_models() picks the
# models for a display, VMAF generation and viewing distance, and returns
# ModelRun entries that layer 2 completes with per-run options and layer 1
# serializes into the libvmaf model= parameter.
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# Target resolution the inputs are scaled to for each display
DISPLAY_RESOLUTION = {'hd': (1920, 1080), '4k': (3840, 2160)}

VMAF_VERSIONS = ('1', '0.6')

# Alternative names accepted in select_models(views=...), v1 only
VIEW_ALIASES = {'phone': '5h'}


@dataclass(frozen=True)
class ModelSpec:
    name: str                  # metric key in the libvmaf log and in the scores
    libvmaf_model: str         # built-in model id, passed as version=
    vmaf_version: str          # '1' | '0.6'
    display: str               # 'hd' | '4k'
    view: str                  # '3h' | '5h' | '1.5h' (v0.6: 'default' | 'neg' | 'phone')
    score_range: Tuple[float, float] = (0.0, 100.0)
    options: Tuple[Tuple[str, str], ...] = ()  # model options, e.g. enable_transform
    hfr_model: Optional[str] = None            # _hfr variant, v1 only
    default: bool = True                       # selected by v1 when views is None


@dataclass(frozen=True)
class ModelRun:
    spec: ModelSpec
    libvmaf_model: str                         # spec.libvmaf_model or spec.hfr_model
    options: Tuple[Tuple[str, str], ...]       # spec.options + overrides (cambi.enc_width, ...)


def _v1(name, suffix, display, view, score_range=(0.0, 100.0), default=True):
    return ModelSpec(
        name=name,
        libvmaf_model=f'vmaf_v1.0.16_{suffix}',
        vmaf_version='1',
        display=display,
        view=view,
        score_range=score_range,
        hfr_model=f'vmaf_v1.0.16_hfr_{suffix}',
        default=default,
    )


# Order matters: within a display and version, select_models() returns models
# in catalog order. The v0.6 HD order matches the libvmaf model= string of
# easyVmaf 3.x (vmaf_hd, vmaf_hd_neg, vmaf_hd_phone).
CATALOG: Tuple[ModelSpec, ...] = (
    _v1('vmaf_v1_hd',    '3d0h',      'hd', '3h'),
    _v1('vmaf_v1_phone', '5d0h',      'hd', '5h'),
    _v1('vmaf_v1_4k',    '1d5h_2160', '4k', '1.5h'),
    _v1('vmaf_v1_4k_3h', '3d0h_2160', '4k', '3h', score_range=(0.0, 110.0), default=False),
    ModelSpec('vmaf_hd',       'vmaf_v0.6.1',    '0.6', 'hd', 'default'),
    ModelSpec('vmaf_hd_neg',   'vmaf_v0.6.1neg', '0.6', 'hd', 'neg'),
    ModelSpec('vmaf_hd_phone', 'vmaf_v0.6.1',    '0.6', 'hd', 'phone',
              options=(('enable_transform', 'true'),)),
    ModelSpec('vmaf_4k',       'vmaf_4k_v0.6.1', '0.6', '4k', 'default'),
)


def _unique(values: Iterable[str]) -> List[str]:
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def select_models(display: str,
                  vmaf_versions: Sequence[str] = ('1',),
                  views: Optional[Sequence[str]] = None,
                  hfr: bool = False) -> List[ModelRun]:
    """
    Select the models to compute for a display.

    Args:
        display: 'hd' or '4k' (case-insensitive)
        vmaf_versions: VMAF generations, '1' and/or '0.6'; results follow this order
        views: v1 viewing distances ('3h', '5h' or its alias 'phone', '1.5h').
            None selects the default v1 models of the display. Ignored for v0.6,
            which always returns the full set of its display.
        hfr: use the _hfr variant of every v1 model

    Returns:
        ModelRun list without overrides, in version order and catalog order

    Raises:
        ValueError: unknown display or version, or a view not available for
            the display
    """
    display = str(display).lower()
    if display not in DISPLAY_RESOLUTION:
        raise ValueError(
            f"Unknown display '{display}'. "
            f"Valid displays: {', '.join(DISPLAY_RESOLUTION)}"
        )
    versions = _unique(str(v) for v in vmaf_versions)
    if not versions:
        raise ValueError("At least one VMAF version is required")
    for version in versions:
        if version not in VMAF_VERSIONS:
            raise ValueError(
                f"Unknown VMAF version '{version}'. "
                f"Valid versions: {', '.join(VMAF_VERSIONS)}"
            )

    runs = []
    for version in versions:
        specs = [s for s in CATALOG if s.display == display and s.vmaf_version == version]
        if version == '1':
            specs = _filter_v1_views(specs, display, views)
        for spec in specs:
            model = spec.hfr_model if hfr and spec.hfr_model else spec.libvmaf_model
            runs.append(ModelRun(spec=spec, libvmaf_model=model, options=spec.options))
    return runs


def _filter_v1_views(specs: List[ModelSpec], display: str,
                     views: Optional[Sequence[str]]) -> List[ModelSpec]:
    if views is None:
        return [s for s in specs if s.default]
    valid = [s.view for s in specs]
    wanted = []
    for view in views:
        canonical = VIEW_ALIASES.get(str(view).lower(), str(view).lower())
        if canonical not in valid:
            raise ValueError(
                f"View '{view}' is not available for display '{display}'. "
                f"Valid views: {', '.join(valid)}"
            )
        wanted.append(canonical)
    if not wanted:
        raise ValueError(
            f"At least one view is required for display '{display}'. "
            f"Valid views: {', '.join(valid)}"
        )
    return [s for s in specs if s.view in wanted]


def model_names(runs: Sequence[ModelRun]) -> List[str]:
    """Metric names of the runs, in order."""
    return [run.spec.name for run in runs]
