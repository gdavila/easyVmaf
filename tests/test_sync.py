"""Layer 2: parallel sync search, the final filter pipeline, model resolution and
libvmaf log reading, with FFmpeg stubbed."""

import json
import re
import threading
import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import STREAM
from easyvmaf import ffmpeg, vmaf
from easyvmaf.results import read_scores
from easyvmaf.vmaf import UnsupportedModelConfigError


def inputs(cmd):
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]


@pytest.fixture
def fake_ffmpeg(monkeypatch, unscored):
    """A 320x180 rendition of a 1080p reference; PSNR peaks when the trimmed input
    starts at 0.2 s. Probes and commands are recorded."""
    probes, commands, lock = Counter(), [], threading.Lock()

    def get_frames_info(self):
        with lock:
            probes[self.videoSrc] += 1
        # Slow enough for concurrent workers to race on the unlocked lazy cache.
        time.sleep(0.05)
        return [{"interlaced_frame": 0, "pkt_size": 100}] * 10

    def check_output(cmd, **kwargs):
        with lock:
            commands.append(list(cmd))
        graph = cmd[cmd.index("-lavfi") + 1]
        return b"average:50.0" if "trim=start=0.2:" in graph else b"average:20.0"

    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: dict(
        STREAM, **(dict(width=1920, height=1080) if "reference" in self.videoSrc else {})))
    monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo", get_frames_info)
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", check_output)
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
        return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
    return SimpleNamespace(probes=probes, commands=commands)


def test_repeated_searches_keep_shared_sources_and_roles(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", vmaf_versions=("0.6",), manual_fps=10,
                       threads=1, gpu_mode=True)
    qos = calculation.ffmpegQos
    shared = (qos, qos.main, qos.ref, qos.main.videoSrc, qos.ref.videoSrc, qos.invertedSrc)

    for reverse in (True, True, False):
        fake_ffmpeg.commands.clear()
        assert calculation.syncOffset(0.4, reverse=reverse) == [-0.2 if reverse else 0.2, 50.0]
        qos = calculation.ffmpegQos
        assert (qos, qos.main, qos.ref, qos.main.videoSrc, qos.ref.videoSrc,
                qos.invertedSrc) == shared
        assert len(fake_ffmpeg.commands) == 4
        for cmd in fake_ffmpeg.commands:
            # Reverse workers swap inputs; sync stays on CPU even in GPU mode.
            assert inputs(cmd) == (["reference.mkv", "distorted.mkv"] if reverse
                                   else ["distorted.mkv", "reference.mkv"])
            graph = cmd[cmd.index("-lavfi") + 1]
            # Only the distorted input needs upscaling, whichever slot it takes.
            assert re.findall(r"\[input(\d)_\d+\]scale=", graph) == ["1" if reverse else "0"]
            assert "cuda" not in graph


@pytest.mark.parametrize("manual_fps", [0, 10])
def test_interlace_is_probed_once_per_input_before_workers(fake_ffmpeg, manual_fps):
    calculation = vmaf("distorted.mkv", "reference.mkv", threads=4, manual_fps=manual_fps)

    calculation.syncOffset(1.0)

    assert len(fake_ffmpeg.commands) == 10
    # --fps skips the interlace probe entirely.
    expected = {"distorted.mkv": 1, "reference.mkv": 1} if manual_fps == 0 else {}
    assert dict(fake_ffmpeg.probes) == expected


def test_only_sync_workers_run_ffmpeg_single_threaded(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", threads=2, manual_fps=10)

    calculation.syncOffset(0.4)
    calculation.getVmaf()

    assert len(fake_ffmpeg.commands) == 4
    for cmd in fake_ffmpeg.commands:
        positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
        # Decoder threads are an input option: immediately before each -i.
        assert [cmd[i - 2:i] for i in positions] == [["-threads", "1"]] * 2
        assert cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    final = calculation.ffmpegQos._cmd
    assert "-threads" not in final
    assert "-filter_complex_threads" not in final


def test_gpu_vmaf_uploads_after_cpu_filters_and_trims_distorted_on_reverse(fake_ffmpeg):
    calculation = vmaf("distorted.mkv", "reference.mkv", vmaf_versions=("0.6",), manual_fps=10,
                       threads=1, gpu_mode=True)
    calculation.syncOffset(0.4, reverse=True)
    # A repeated run rebuilds the chains: clearFilters() resets the hwupload guard.
    calculation.getVmaf()
    calculation.getVmaf()

    qos = calculation.ffmpegQos
    assert inputs(qos._cmd) == ["distorted.mkv", "reference.mkv"]
    assert qos.vmafpath == "distorted_vmaf.json"
    upload = ["format", "setparams", "hwupload_cuda"]
    for chain, steps, trim in (
            (qos.main, ["scale", "fps", "trim"] + upload, "trim=start=0.2:duration=1.0"),
            (qos.ref, ["fps", "trim"] + upload, "trim=start=0:duration=1.0")):
        assert [re.match(r"\[[^]]+\](\w+)", f).group(1) for f in chain.filtersList] == steps
        assert trim in chain.filtersList[steps.index("trim")]
        assert chain.filtersList[-1].endswith("[{}]".format(chain.lastOutputID))
    assert qos.vmafFilter[0].startswith("[{}][{}]libvmaf_cuda=".format(
        qos.main.lastOutputID, qos.ref.lastOutputID))


def stream(width=1920, height=1080, fps="25/1", pix_fmt="yuv420p", interlaced=False):
    return dict(width=width, height=height, r_frame_rate=fps, duration="10.0",
                start_time="0", pix_fmt=pix_fmt, interlaced=interlaced)


@pytest.fixture
def final_vmaf(monkeypatch, unscored):
    """Run vmaf.getVmaf() on stubbed dist.mp4/ref.mp4 streams; returns (calculation, result)."""
    def run(distorted, reference, **options):
        streams = {"dist.mp4": distorted, "ref.mp4": reference}
        monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo",
                            lambda self: dict(streams[self.videoSrc]))
        monkeypatch.setattr(ffmpeg.FFprobe, "getFramesInfo", lambda self: [
            {"interlaced_frame": int(streams[self.videoSrc]["interlaced"]), "pkt_size": 1}])
        monkeypatch.setattr(ffmpeg.subprocess, "Popen", Mock(
            return_value=SimpleNamespace(returncode=0, communicate=lambda: (b"", None))))
        calculation = vmaf("dist.mp4", "ref.mp4", threads=1, **options)
        return calculation, calculation.getVmaf()
    return run


@pytest.mark.parametrize("reference, distorted, versions, expected", [
    # v1 measures chroma: converting a 4:2:2 reference to 4:2:0 hides the chroma loss.
    ("yuv422p", "yuv420p", ("1",), dict(main="yuv422p10le", ref="yuv422p10le")),
    # FFmpeg would otherwise degrade the 10-bit reference to the distorted 8 bits.
    ("yuv420p10le", "yuv420p", ("0.6",), dict(main="yuv420p10le", ref=None)),
], ids=["v1-422-reference", "v06-10bit-reference"])
def test_inputs_are_measured_in_the_reference_format(final_vmaf, reference, distorted,
                                                     versions, expected):
    calculation, result = final_vmaf(stream(1280, 720, pix_fmt=distorted),
                                     stream(pix_fmt=reference), vmaf_versions=versions)

    qos = calculation.ffmpegQos
    for name, chain in (("main", qos.main), ("ref", qos.ref)):
        # First filter: conversion before scaling or deinterlacing interpolates.
        first = re.match(r"\[[^]]+\]format=(\w+)\[", chain.filtersList[0])
        assert (first and first.group(1)) == expected[name]
    assert result.pix_fmt == expected["main"]


def test_cambi_uses_the_distorted_encoding_parameters(final_vmaf):
    """CAMBI would otherwise measure banding on the picture scaled to 1080p."""
    _, result = final_vmaf(stream(1280, 720), stream())

    assert [run.spec.name for run in result.models] == ["vmaf_v1_hd", "vmaf_v1_phone"]
    for run in result.models:
        assert dict(run.options).items() >= {"cambi.enc_width": "1280", "cambi.enc_height": "720",
                                             "cambi.enc_bitdepth": "8"}.items()


@pytest.mark.parametrize("distorted, reference, hfr", [
    (stream(fps="60000/1001"), stream(fps="60000/1001"), True),
    (stream(fps="25/1"), stream(fps="25/1"), False),
    # A 1080i29.97 reference deinterlaced by field to 59.94p.
    (stream(fps="60000/1001"), stream(fps="30000/1001", interlaced=True), True),
], ids=["59.94p", "25p", "1080i29.97-field-deinterlaced"])
def test_hfr_models_follow_the_effective_frame_rate(final_vmaf, distorted, reference, hfr):
    """HFR content scored with the standard v1 models, or 25p scored with HFR ones."""
    _, result = final_vmaf(distorted, reference)

    assert [("_hfr_" in run.libvmaf_model) for run in result.models] == [hfr, hfr]
    assert result.hfr is hfr


def test_gpu_mode_rejects_vmaf_v1():
    """libvmaf_cuda has no v1 feature extractors: FFmpeg would fail after probing."""
    with pytest.raises(UnsupportedModelConfigError):
        vmaf("dist.mp4", "ref.mp4", gpu_mode=True)


V1_FRAMES = [
    {"cambi_hrs_1080_cmxv_17_vlt_0.06_encw_1280": 0.32, "speed_chroma_uv_mxv_45": 1.66,
     "integer_adm3_csf_2": 0.97, "integer_motion3_mmxv_18": 4.7, "psnr_y": 35.2,
     "vmaf_v1_hd": 94.0, "vmaf_v1_phone": 96.5},
    {"cambi_hrs_1080_cmxv_17_vlt_0.06_encw_1280": 0.28, "speed_chroma_uv_mxv_45": 1.44,
     "integer_adm3_csf_2": 0.98, "integer_motion3_mmxv_18": 4.7, "psnr_y": 35.1,
     "vmaf_v1_hd": 95.0, "vmaf_v1_phone": 97.5},
]


def write_libvmaf_log(path, output_fmt, frames):
    """A log laid out as libvmaf writes it, feature columns included."""
    if output_fmt == "json":
        path.write_text(json.dumps({"frames": [
            {"frameNum": i, "metrics": metrics} for i, metrics in enumerate(frames)]}))
    elif output_fmt == "xml":
        rows = "".join('<frame frameNum="{}" {}/>'.format(i, " ".join(
            '{}="{:.6f}"'.format(*item) for item in metrics.items()))
            for i, metrics in enumerate(frames))
        path.write_text('<VMAF version="3.2.0"><frames>' + rows + "</frames></VMAF>")
    else:
        # libvmaf ends every CSV line with a separator.
        lines = ["Frame," + ",".join(frames[0]) + ","]
        lines += ["{},{},".format(i, ",".join("%.6f" % v for v in metrics.values()))
                  for i, metrics in enumerate(frames)]
        path.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("output_fmt", ["json", "xml", "csv"])
def test_read_scores_averages_models_among_v1_feature_columns(tmp_path, output_fmt):
    """Users pick the log format; v1 feature columns must not break reading."""
    log = tmp_path / ("dist_vmaf." + output_fmt)
    write_libvmaf_log(log, output_fmt, V1_FRAMES)

    scores = read_scores(str(log), output_fmt, ["vmaf_v1_hd", "vmaf_v1_phone"])

    assert scores == pytest.approx({"vmaf_v1_hd": 94.5, "vmaf_v1_phone": 97.0})
