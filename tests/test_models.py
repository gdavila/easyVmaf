"""Model catalog: model selection and the v0.6 model= string."""

import pytest

from easyvmaf.ffmpeg import FFmpegQos
from easyvmaf.models import model_names, select_models


@pytest.mark.parametrize("display, versions, names", [
    ('hd', ('1',), ['vmaf_v1_hd', 'vmaf_v1_phone']),
    ('4k', ('1',), ['vmaf_v1_4k']),
    ('hd', ('0.6',), ['vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone']),
])
def test_default_selection(display, versions, names):
    """Users get a different set of scores than documented for a display and version."""
    assert model_names(select_models(display, versions)) == names


def test_default_version_is_v1():
    """Callers that omit the version silently get v0.6 instead of v1."""
    assert model_names(select_models('hd')) == ['vmaf_v1_hd', 'vmaf_v1_phone']


@pytest.mark.parametrize("display, view, name", [
    ('hd', 'phone', 'vmaf_v1_phone'),
    ('4k', '3h', 'vmaf_v1_4k_3h'),
])
def test_explicit_view(display, view, name):
    """--view phone stops working, or the 0-110 4K model becomes unreachable."""
    assert model_names(select_models(display, ('1',), (view,))) == [name]


def test_view_not_available_for_display():
    """A view the display does not have is dropped silently instead of rejected."""
    with pytest.raises(ValueError):
        select_models('4k', ('1',), ('5h',))


def test_views_filter_only_v1_in_mixed_selection():
    """--view drops v0.6 models when both generations are computed in one pass."""
    runs = select_models('hd', ('1', '0.6'), ('5h',))
    assert model_names(runs) == ['vmaf_v1_phone', 'vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone']


def test_hfr_switches_only_v1_ids():
    """HFR content is scored with the standard v1 models, or v0.6 gets a nonexistent id."""
    runs = select_models('hd', ('1', '0.6'), hfr=True)
    assert [run.libvmaf_model for run in runs] == [
        'vmaf_v1.0.16_hfr_3d0h', 'vmaf_v1.0.16_hfr_5d0h',
        'vmaf_v0.6.1', 'vmaf_v0.6.1neg', 'vmaf_v0.6.1',
    ]


def test_unknown_display():
    """An unknown display yields an empty model list instead of an error."""
    with pytest.raises(ValueError):
        select_models('sd')


def _serialize(runs):
    # Layer 1 contract (plan, phase 2): version, name, then options, joined by \\:
    return '|'.join(
        '\\\\:'.join([f'version={run.libvmaf_model}', f'name={run.spec.name}']
                     + [f'{key}={value}' for key, value in run.options])
        for run in runs
    )


@pytest.mark.parametrize("display, model", [('hd', 'HD'), ('4k', '4K')])
def test_v06_runs_reproduce_current_model_string(display, model):
    """The catalog changes the v0.6 libvmaf model= string, and with it the v0.6 scores."""
    assert _serialize(select_models(display, ('0.6',))) == FFmpegQos._build_model_string(model)
