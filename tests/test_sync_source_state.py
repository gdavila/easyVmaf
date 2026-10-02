"""Sync workers must not change the sources used for the final calculation."""

from types import SimpleNamespace

import pytest

from easyvmaf import ffmpeg, vmaf


@pytest.fixture
def calculation(monkeypatch):
    monkeypatch.setattr(ffmpeg.FFprobe, "getStreamInfo", lambda self: {
        "width": 320, "height": 180, "r_frame_rate": "10/1",
        "duration": "1.2", "start_time": "0",
    })
    return vmaf("distorted.mkv", "reference.mkv", "json", manual_fps=10,
                threads=1, gpu_mode=True)


def source_state(calculation):
    qos = calculation.ffmpegQos
    return (calculation.main.videoSrc, calculation.ref.videoSrc,
            qos.main.videoSrc, qos.ref.videoSrc, qos.invertedSrc)


@pytest.mark.parametrize("directions", [(False,), (True,), (True, True), (True, False)])
def test_search_preserves_shared_sources_and_roles(calculation, monkeypatch, directions):
    # Stub only the external process; exercise real worker and filter construction.
    commands = []

    def psnr(command, **kwargs):
        commands.append(command)
        graph = command[command.index("-lavfi") + 1]
        return b"average:50.0" if "trim=start=0.2:" in graph else b"average:20.0"

    monkeypatch.setattr(ffmpeg.subprocess, "check_output", psnr)
    original = source_state(calculation)
    original_inputs = (calculation.ffmpegQos.main, calculation.ffmpegQos.ref)
    for reverse in directions:
        commands.clear()
        offset, score = calculation.syncOffset(0.4, reverse=reverse)
        assert offset == pytest.approx(-0.2 if reverse else 0.2)
        assert score == 50
        assert source_state(calculation) == original
        assert (calculation.ffmpegQos.main, calculation.ffmpegQos.ref) == original_inputs
        assert len(commands) == 4
        for command in commands:
            inputs = [command[i + 1] for i, arg in enumerate(command) if arg == "-i"]
            assert inputs == (["reference.mkv", "distorted.mkv"] if reverse else
                              ["distorted.mkv", "reference.mkv"])
            assert "cuda" not in " ".join(command)
            assert "psnr=" in " ".join(command)


@pytest.mark.parametrize("gpu", [False, True])
def test_reverse_sync_final_command_trims_distorted_and_names_its_output(calculation,
                                                                        monkeypatch, gpu):
    calculation.gpu_mode = gpu
    monkeypatch.setattr(ffmpeg.subprocess, "check_output", lambda command, **kwargs:
                        b"average:50.0" if "trim=start=0.2:" in " ".join(command)
                        else b"average:20.0")
    monkeypatch.setattr(ffmpeg.subprocess, "Popen", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, communicate=lambda: (b"", None)))
    calculation.syncOffset(0.4, reverse=True)
    calculation.getVmaf()

    qos = calculation.ffmpegQos
    inputs = [qos._cmd[i + 1] for i, arg in enumerate(qos._cmd) if arg == "-i"]
    assert inputs == ["distorted.mkv", "reference.mkv"]
    assert "trim=start=0.2:duration=1.0" in ";".join(qos.main.filtersList)
    assert "trim=start=0:duration=1.0" in ";".join(qos.ref.filtersList)
    assert qos.vmafpath == "distorted_vmaf.json"
    assert qos.vmafFilter[0].startswith(
        "[{}][{}]libvmaf{}=".format(qos.main.lastOutputID, qos.ref.lastOutputID,
                                  "_cuda" if gpu else ""))
