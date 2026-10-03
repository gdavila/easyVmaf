"""Model catalog: catalog entries, model selection and the v0.6 model= string."""

import pytest

from easyvmaf.ffmpeg import FFmpegQos
from easyvmaf.models import CATALOG, DISPLAY_RESOLUTION, ModelRun, model_names, select_models


def ids(runs):
    return [run.libvmaf_model for run in runs]


def test_display_resolution():
    assert DISPLAY_RESOLUTION == {'hd': (1920, 1080), '4k': (3840, 2160)}


def test_catalog_entries():
    rows = [(s.name, s.libvmaf_model, s.hfr_model, s.vmaf_version, s.display, s.view,
             s.score_range, s.options, s.default) for s in CATALOG]
    assert rows == [
        ('vmaf_v1_hd', 'vmaf_v1.0.16_3d0h', 'vmaf_v1.0.16_hfr_3d0h',
         '1', 'hd', '3h', (0, 100), (), True),
        ('vmaf_v1_phone', 'vmaf_v1.0.16_5d0h', 'vmaf_v1.0.16_hfr_5d0h',
         '1', 'hd', '5h', (0, 100), (), True),
        ('vmaf_v1_4k', 'vmaf_v1.0.16_1d5h_2160', 'vmaf_v1.0.16_hfr_1d5h_2160',
         '1', '4k', '1.5h', (0, 100), (), True),
        ('vmaf_v1_4k_3h', 'vmaf_v1.0.16_3d0h_2160', 'vmaf_v1.0.16_hfr_3d0h_2160',
         '1', '4k', '3h', (0, 110), (), False),
        ('vmaf_hd', 'vmaf_v0.6.1', None, '0.6', 'hd', 'default', (0, 100), (), True),
        ('vmaf_hd_neg', 'vmaf_v0.6.1neg', None, '0.6', 'hd', 'neg', (0, 100), (), True),
        ('vmaf_hd_phone', 'vmaf_v0.6.1', None, '0.6', 'hd', 'phone', (0, 100),
         (('enable_transform', 'true'),), True),
        ('vmaf_4k', 'vmaf_4k_v0.6.1', None, '0.6', '4k', 'default', (0, 100), (), True),
    ]


@pytest.mark.parametrize("display, versions, names, models", [
    ('hd', ('1',), ['vmaf_v1_hd', 'vmaf_v1_phone'],
     ['vmaf_v1.0.16_3d0h', 'vmaf_v1.0.16_5d0h']),
    ('4k', ('1',), ['vmaf_v1_4k'], ['vmaf_v1.0.16_1d5h_2160']),
    ('hd', ('0.6',), ['vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone'],
     ['vmaf_v0.6.1', 'vmaf_v0.6.1neg', 'vmaf_v0.6.1']),
    ('4k', ('0.6',), ['vmaf_4k'], ['vmaf_4k_v0.6.1']),
    ('hd', ('1', '0.6'), ['vmaf_v1_hd', 'vmaf_v1_phone', 'vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone'],
     ['vmaf_v1.0.16_3d0h', 'vmaf_v1.0.16_5d0h', 'vmaf_v0.6.1', 'vmaf_v0.6.1neg', 'vmaf_v0.6.1']),
    ('hd', ('0.6', '1'), ['vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone', 'vmaf_v1_hd', 'vmaf_v1_phone'],
     ['vmaf_v0.6.1', 'vmaf_v0.6.1neg', 'vmaf_v0.6.1', 'vmaf_v1.0.16_3d0h', 'vmaf_v1.0.16_5d0h']),
    ('4k', ('1', '0.6'), ['vmaf_v1_4k', 'vmaf_4k'], ['vmaf_v1.0.16_1d5h_2160', 'vmaf_4k_v0.6.1']),
])
def test_default_selection(display, versions, names, models):
    runs = select_models(display, versions)
    assert model_names(runs) == names
    assert ids(runs) == models
    assert all(isinstance(run, ModelRun) and run.options == run.spec.options for run in runs)


def test_default_version_is_v1():
    assert model_names(select_models('hd')) == ['vmaf_v1_hd', 'vmaf_v1_phone']


@pytest.mark.parametrize("display, views, names", [
    ('hd', ('3h',), ['vmaf_v1_hd']),
    ('hd', ('5h',), ['vmaf_v1_phone']),
    ('hd', ('phone',), ['vmaf_v1_phone']),
    ('hd', ('5h', '3h'), ['vmaf_v1_hd', 'vmaf_v1_phone']),
    ('hd', ('5H', 'phone'), ['vmaf_v1_phone']),
    ('4k', ('3h',), ['vmaf_v1_4k_3h']),
    ('4k', ('1.5h', '3h'), ['vmaf_v1_4k', 'vmaf_v1_4k_3h']),
])
def test_explicit_views(display, views, names):
    assert model_names(select_models(display, ('1',), views)) == names


@pytest.mark.parametrize("display, view, valid", [
    ('4k', '5h', '1.5h, 3h'),
    ('4k', 'phone', '1.5h, 3h'),
    ('hd', '1.5h', '3h, 5h'),
    ('hd', 'far', '3h, 5h'),
])
def test_view_not_available_for_display(display, view, valid):
    with pytest.raises(ValueError) as excinfo:
        select_models(display, ('1',), (view,))
    message = str(excinfo.value)
    assert f"'{view}'" in message and f"'{display}'" in message
    assert f"Valid views: {valid}" in message


def test_views_ignored_for_v06_only():
    assert model_names(select_models('4k', ('0.6',), ('5h',))) == ['vmaf_4k']
    assert model_names(select_models('hd', ('0.6',), ('3h',))) == \
        ['vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone']


def test_views_apply_only_to_v1_in_mixed_selection():
    runs = select_models('hd', ('1', '0.6'), ('5h',))
    assert model_names(runs) == ['vmaf_v1_phone', 'vmaf_hd', 'vmaf_hd_neg', 'vmaf_hd_phone']


@pytest.mark.parametrize("display", ['hd', '4k'])
def test_hfr_changes_only_v1_ids(display):
    normal = select_models(display, ('1', '0.6'), hfr=False)
    hfr = select_models(display, ('1', '0.6'), hfr=True)
    assert model_names(hfr) == model_names(normal)
    for plain, fast in zip(normal, hfr):
        if plain.spec.vmaf_version == '1':
            assert fast.libvmaf_model == plain.libvmaf_model.replace('vmaf_v1.0.16_', 'vmaf_v1.0.16_hfr_')
        else:
            assert fast.libvmaf_model == plain.libvmaf_model


def test_hfr_4k_3h_model():
    run, = select_models('4k', ('1',), ('3h',), hfr=True)
    assert run.libvmaf_model == 'vmaf_v1.0.16_hfr_3d0h_2160'
    assert run.spec.name == 'vmaf_v1_4k_3h'
    assert run.spec.score_range == (0, 110)


def test_display_is_case_insensitive():
    assert select_models('HD', ('0.6',)) == select_models('hd', ('0.6',))
    assert select_models('4K') == select_models('4k')


@pytest.mark.parametrize("kwargs, text", [
    ({'display': 'sd'}, "Valid displays: hd, 4k"),
    ({'display': 'hd', 'vmaf_versions': ('2',)}, "Valid versions: 1, 0.6"),
    ({'display': 'hd', 'vmaf_versions': ()}, "At least one VMAF version"),
    ({'display': 'hd', 'views': ()}, "At least one view is required for display 'hd'"),
])
def test_invalid_arguments(kwargs, text):
    with pytest.raises(ValueError, match=text):
        select_models(**kwargs)


def test_duplicate_versions_are_selected_once():
    assert model_names(select_models('4k', ('1', '1'))) == ['vmaf_v1_4k']


def _serialize(runs):
    # Layer 1 contract (plan, phase 2): version, name, then options, joined by \\:
    return '|'.join(
        '\\\\:'.join([f'version={run.libvmaf_model}', f'name={run.spec.name}']
                     + [f'{key}={value}' for key, value in run.options])
        for run in runs
    )


@pytest.mark.parametrize("display, model", [('hd', 'HD'), ('4k', '4K')])
def test_v06_runs_reproduce_current_model_string(display, model):
    assert _serialize(select_models(display, ('0.6',))) == FFmpegQos._build_model_string(model)
