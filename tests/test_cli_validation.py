"""CLI domains are checked before environment checks or video probing."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easyvmaf import cli


INVALID_VALUES = [
    (flag, value)
    for flag in ("-sw", "-ss", "-fps")
    for value in ("-1", "nan", "inf")
] + [
    (flag, value)
    for flag in ("-subsample", "-threads")
    for value in ("-1", "1.5")
] + [("-subsample", "0"), ("-model", "hd"), ("-model", "banana"),
     ("-output_fmt", "JSON"), ("-output_fmt", "banana")]


@pytest.mark.parametrize("flag,value", INVALID_VALUES)
def test_invalid_values_fail_before_environment_or_video_calls(
        monkeypatch, capsys, flag, value):
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", "missing.mp4",
                        "-r", "missing-ref.mp4", flag + "=" + value, "-json"])
    environment_check = Mock(side_effect=AssertionError("FFmpeg checked before validation"))
    constructor = Mock(side_effect=AssertionError("video constructed before validation"))
    monkeypatch.setattr(cli, "check_ffmpeg", environment_check)
    monkeypatch.setattr(cli, "vmaf", constructor)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert flag in captured.err
    environment_check.assert_not_called()
    constructor.assert_not_called()


@pytest.mark.parametrize("options,expected", [
    ([], (0, 0, 0, 1, 0, "HD", "json")),
    (["-sw", "0", "-ss", "0", "-fps", "0", "-subsample", "1", "-threads", "0"],
     (0, 0, 0, 1, 0, "HD", "json")),
    (["-sw", "0.125", "-ss", "1.25", "-fps", "23.976", "-subsample", "3",
      "-threads", "2", "-model", "4K"], (0.125, 1.25, 23.976, 3, 2, "4K", "json")),
    (["-output_fmt", "xml"], (0, 0, 0, 1, 0, "HD", "xml")),
    (["-output_fmt", "csv"], (0, 0, 0, 1, 0, "HD", "csv")),
])
def test_valid_defaults_and_options_are_forwarded_unmodified(
        tmp_path, monkeypatch, capsys, options, expected):
    window, start, fps, subsample, threads, model, output_format = expected
    distorted = tmp_path / "distorted.mp4"
    reference = tmp_path / "reference.mp4"
    distorted.touch()
    reference.touch()
    output = tmp_path / ("scores." + output_format)
    scores = {"vmaf_hd": 91, "vmaf_hd_neg": 90, "vmaf_hd_phone": 95, "vmaf_4k": 92}
    if output_format == "json":
        output.write_text(json.dumps({"frames": [{"metrics": scores}]}))
    elif output_format == "xml":
        attributes = " ".join('{}="{}"'.format(key, value) for key, value in scores.items())
        output.write_text("<root><frames><frame " + attributes + "/></frames></root>")
    else:
        output.write_text(",".join(scores) + "\n" + ",".join(str(v) for v in scores.values()) + "\n")
    calculation = SimpleNamespace(syncOffset=Mock(return_value=(0.25, 30)), getVmaf=Mock(),
                                  ffmpegQos=SimpleNamespace(vmafpath=str(output)))
    constructor = Mock(return_value=calculation)
    environment_check = Mock(return_value=dict(meets_minimum=True, builtin_models=True,
                             version_str="9.0", cuda_vmaf=False))
    monkeypatch.setattr(cli, "check_ffmpeg", environment_check)
    monkeypatch.setattr(cli, "vmaf", constructor)
    monkeypatch.setattr(sys, "argv", ["easyvmaf", "-d", str(distorted), "-r", str(reference),
                                      "-json"] + options)

    cli.main()

    environment_check.assert_called_once_with()
    constructor.assert_called_once()
    assert constructor.call_args.args == (str(distorted), str(reference))
    for option, value in dict(subsample=subsample, model=model, output_fmt=output_format,
                             threads=threads, manual_fps=fps).items():
        assert constructor.call_args.kwargs[option] == value
    calculation.getVmaf.assert_called_once_with()
    if window:
        calculation.syncOffset.assert_called_once_with(window, start, False)
    else:
        calculation.syncOffset.assert_not_called()
        assert calculation.offset == start
    result = json.loads(capsys.readouterr().out)
    assert result["vmaf"]["model"] == model
    assert result["vmaf"]["output_file"] == str(output)
