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
import csv
import glob
import json
import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from statistics import mean
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .models import ModelRun


class SyncResult(NamedTuple):
    """Outcome of Vmaf.sync(): the offset found and its PSNR."""
    offset: float  # seconds: positive trims the reference, negative the distorted
    psnr: float    # PSNR in dB between the inputs at that offset


@dataclass
class VmafResult:
    """Outcome of one Vmaf.compute() run."""
    scores: Dict[str, float]           # metric name -> mean score over frames
    models: List[ModelRun]             # runs passed to libvmaf, in score order
    display: str                       # 'hd' | '4k'
    pix_fmt: str                       # measurement pixel format
    hfr: bool                          # True when the v1 _hfr variants were used
    log_path: str                      # libvmaf log (json, xml or csv)
    cambi_heatmap_path: Optional[str] = None
    sync_offset: float = 0.0
    # Frame range (Vmaf start_frame/frame_count); None for a full calculation.
    start_frame: Optional[int] = None  # first measured frame of the range
    frame_count: Optional[int] = None  # frames requested; None: to the end
    frames_scored: Optional[int] = None  # frames in the log; fewer at the end
    cuda: bool = False                 # True when libvmaf_cuda computed the scores
    # Hardware decoding: {'api': 'cuda', 'distorted': 'hw' | 'sw',
    # 'reference': 'hw' | 'sw'}; None without it.
    hw_decode: Optional[Dict[str, Any]] = None
    dynamic_range: str = 'sdr'         # 'sdr' | 'hdr': the model family
    # With HDR, the colorimetry of each input (detected, with the overrides):
    # {'reference': {'range', 'primaries', 'trc', 'matrix'}, 'distorted': {...}};
    # None with SDR.
    color: Optional[Dict[str, Dict[str, str]]] = None


def read_scores(log_path: str, output_fmt: str, names: Sequence[str]) -> Dict[str, float]:
    """
    Average the per-frame scores of the given model names in a libvmaf log.

    Only model names are read: feature columns differ between VMAF v0.6 and v1
    (cambi, speed_chroma_*, integer_adm3, ...) and are ignored.

    Args:
        log_path: libvmaf log written with log_fmt=output_fmt
        output_fmt: 'json', 'xml' or 'csv'
        names: metric names (ModelSpec.name) to read

    Returns:
        dict of name -> arithmetic mean over frames, in the order of names
    """
    frameScores = {name: [] for name in names}

    if output_fmt == 'csv':
        with open(log_path, mode='r', newline='') as csvFile:
            for row in csv.DictReader(csvFile):
                for name in names:
                    frameScores[name].append(float(row[name]))
    elif output_fmt == 'xml':
        root = ET.parse(log_path).getroot()
        for frame in root.findall('frames/frame'):
            for name in names:
                frameScores[name].append(float(frame.attrib[name]))
    else:
        with open(log_path) as jsonFile:
            for frame in json.load(jsonFile)['frames']:
                for name in names:
                    frameScores[name].append(frame['metrics'][name])

    return {name: mean(scores) for name, scores in frameScores.items()}


def read_frames(log_path: str, output_fmt: str) -> List[Tuple[int, Dict[str, float]]]:
    """(frame number, {metric: value}) of every frame of a libvmaf log, in order."""
    if output_fmt == 'csv':
        with open(log_path, mode='r', newline='') as csvFile:
            return [(int(row['Frame']),
                     {k: float(v) for k, v in row.items() if k and k != 'Frame' and v})
                    for row in csv.DictReader(csvFile)]
    if output_fmt == 'xml':
        root = ET.parse(log_path).getroot()
        return [(int(frame.get('frameNum')),
                 {k: float(v) for k, v in frame.attrib.items() if k != 'frameNum'})
                for frame in root.findall('frames/frame')]
    with open(log_path) as jsonFile:
        return [(frame['frameNum'], frame['metrics'])
                for frame in json.load(jsonFile)['frames']]


def _pool(values: Sequence[float]) -> Dict[str, float]:
    """libvmaf pooled metrics; its harmonic mean is n / sum(1 / (x + 1)) - 1."""
    return {
        'min': min(values),
        'max': max(values),
        'mean': mean(values),
        'harmonic_mean': len(values) / sum(1.0 / (v + 1.0) for v in values) - 1.0,
    }


def trim_log(log_path: str, output_fmt: str, start: int, count: int, first_frame: int):
    """
    Keep `count` frames of a libvmaf log from its frame `start`, in place.

    The kept frames are numbered first_frame + their position in the original
    log, and the pooled metrics are recomputed from them.

    Args:
        log_path: libvmaf log written with log_fmt=output_fmt
        output_fmt: 'json', 'xml' or 'csv'
        start: index of the first frame to keep in the log
        count: number of frames to keep (at least 1)
        first_frame: frame number of the first frame of the log
    """
    if output_fmt == 'csv':
        with open(log_path, mode='r', newline='') as csvFile:
            rows = list(csv.reader(csvFile))
        header, frames = rows[0], rows[1 + start:1 + start + count]
        column = header.index('Frame')
        for row in frames:
            row[column] = str(int(row[column]) + first_frame)
        with open(log_path, mode='w', newline='') as csvFile:
            csv.writer(csvFile, lineterminator='\n').writerows([header] + frames)
    elif output_fmt == 'xml':
        tree = ET.parse(log_path)
        root = tree.getroot()
        parent = root.find('frames')
        frames = parent.findall('frame')
        for frame in frames[:start] + frames[start + count:]:
            parent.remove(frame)
        kept = frames[start:start + count]
        for frame in kept:
            frame.set('frameNum', str(int(frame.get('frameNum')) + first_frame))
        _repool_xml(root, kept)
        tree.write(log_path)
    else:
        with open(log_path) as jsonFile:
            log = json.load(jsonFile)
        kept = log['frames'][start:start + count]
        for frame in kept:
            frame['frameNum'] += first_frame
        log['frames'] = kept
        _repool_json(log)
        with open(log_path, 'w') as jsonFile:
            json.dump(log, jsonFile, indent=2)


def _repool_xml(root, frames):
    """Recompute the pooled metrics of an xml log from its frames."""
    for metric in root.findall('pooled_metrics/metric'):
        name = metric.get('name')
        pooled = _pool([float(frame.get(name)) for frame in frames])
        for key, value in pooled.items():
            metric.set(key, '%.6f' % value)


def _repool_json(log):
    """Recompute the pooled metrics of a json log from its frames."""
    log['pooled_metrics'] = {
        name: {key: round(value, 6) for key, value in
               _pool([frame['metrics'][name] for frame in log['frames']]).items()}
        for name in log.get('pooled_metrics', {})}


def _joined_fps(counts: Sequence[int], speeds: Sequence[Optional[float]]) -> Optional[float]:
    """libvmaf's fps of joined logs: frames per CPU second (it measures with
    clock()), i.e. all the frames over the CPU time of every log."""
    if not all(speeds):
        return None
    return sum(counts) / sum(n / fps for n, fps in zip(counts, speeds))


def join_logs(log_paths: Sequence[str], output_fmt: str, out_path: str):
    """
    Write to out_path the frames of the libvmaf logs of consecutive ranges, in
    order, with the pooled metrics and the processing speed (fps) recomputed
    from all of them, in the same format. The rest (version, parameters)
    comes from the first log.

    Args:
        log_paths: logs written with log_fmt=output_fmt, in frame order
        output_fmt: 'json', 'xml' or 'csv'
        out_path: the joined log; written only once every log was read
    """
    if output_fmt == 'csv':
        header, rows = None, []
        for path in log_paths:
            with open(path, mode='r', newline='') as csvFile:
                part = list(csv.reader(csvFile))
            header = header or part[0]
            rows += part[1:]
        with open(out_path, mode='w', newline='') as csvFile:
            csv.writer(csvFile, lineterminator='\n').writerows([header] + rows)
    elif output_fmt == 'xml':
        trees = [ET.parse(path) for path in log_paths]
        root = trees[0].getroot()
        parent = root.find('frames')
        counts, speeds = [len(parent.findall('frame'))], []
        for tree in trees[1:]:
            frames = tree.getroot().findall('frames/frame')
            counts.append(len(frames))
            parent.extend(frames)
        for tree in trees:
            fyi = tree.getroot().find('fyi')
            speeds.append(float(fyi.get('fps')) if fyi is not None and fyi.get('fps') else None)
        _repool_xml(root, parent.findall('frame'))
        fps = _joined_fps(counts, speeds)
        if fps is not None:
            root.find('fyi').set('fps', '%.2f' % fps)
        trees[0].write(out_path)
    else:
        logs = []
        for path in log_paths:
            with open(path) as jsonFile:
                logs.append(json.load(jsonFile))
        log = logs[0]
        fps = _joined_fps([len(part['frames']) for part in logs],
                          [part.get('fps') for part in logs])
        log['frames'] = [frame for part in logs for frame in part['frames']]
        _repool_json(log)
        if fps is not None:
            log['fps'] = round(fps, 2)
        with open(out_path, 'w') as jsonFile:
            json.dump(log, jsonFile, indent=2)


def heatmap_files(path: str) -> List[str]:
    """CAMBI heatmaps of a libvmaf heatmaps_path directory, one per scale."""
    return sorted(glob.glob(os.path.join(glob.escape(path), 'cambi_heatmap_scale_*.gray')))


def trim_heatmaps(path: str, start: int, count: int, frames: int, block: int = 1 << 24):
    """
    Keep `count` pictures of each CAMBI heatmap from its picture `start`, in place.

    libvmaf writes picture n of a run at n * picture size; the file name gives
    the encoding size, not the picture size (cambi_high_res_speedup halves the
    picture), so the size comes from the file size and the frames measured.

    Args:
        path: heatmaps_path directory of the run
        start: index of the first picture to keep
        count: number of pictures to keep (at least 1)
        frames: frames measured by the run (frames of its libvmaf log)

    Raises:
        ValueError: if a heatmap does not hold one picture per measured frame
    """
    for heatmap in heatmap_files(path):
        size = os.path.getsize(heatmap)
        if size == 0 or size % frames:
            raise ValueError(f"CAMBI heatmap {heatmap} ({size} bytes) does not hold "
                             f"{frames} pictures, one per measured frame")
        picture = size // frames
        begin, end = start * picture, (start + count) * picture
        with open(heatmap, 'r+b') as f:
            for offset in range(begin, end, block):
                f.seek(offset)
                data = f.read(min(block, end - offset))
                f.seek(offset - begin)
                f.write(data)
            f.truncate(end - begin)


def join_heatmaps(paths: Sequence[str], out_path: str):
    """
    Concatenate, file by file, the CAMBI heatmaps of consecutive ranges, in
    order, into the heatmaps directory out_path, replacing the ones there.

    Raises:
        ValueError: if the ranges do not have the same heatmap files
    """
    names = [os.path.basename(p) for p in heatmap_files(paths[0])]
    for path in paths[1:]:
        if [os.path.basename(p) for p in heatmap_files(path)] != names:
            raise ValueError(f"CAMBI heatmaps of {path} differ from those of {paths[0]}")
    os.makedirs(out_path, exist_ok=True)
    for stale in heatmap_files(out_path):
        os.remove(stale)
    for name in names:
        with open(os.path.join(out_path, name), 'wb') as joined:
            for path in paths:
                with open(os.path.join(path, name), 'rb') as part:
                    shutil.copyfileobj(part, joined, 1 << 24)
