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
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from .models import ModelRun


@dataclass
class VmafResult:
    """Outcome of one vmaf.getVmaf() run."""
    scores: Dict[str, float]           # metric name -> mean score over frames
    models: List[ModelRun]             # runs passed to libvmaf, in score order
    display: str                       # 'hd' | '4k'
    pix_fmt: str                       # measurement pixel format
    hfr: bool                          # True when the v1 _hfr variants were used
    log_path: str                      # libvmaf log (json, xml or csv)
    cambi_heatmap_path: Optional[str] = None
    offset: float = 0.0
    # Frame range (vmaf start_frame/frame_count); None for a full calculation.
    start_frame: Optional[int] = None  # first measured frame of the range
    frame_count: Optional[int] = None  # frames requested; None: to the end
    frames_scored: Optional[int] = None  # frames in the log; fewer at the end


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
        for metric in root.findall('pooled_metrics/metric'):
            name = metric.get('name')
            pooled = _pool([float(frame.get(name)) for frame in kept])
            for key, value in pooled.items():
                metric.set(key, '%.6f' % value)
        tree.write(log_path)
    else:
        with open(log_path) as jsonFile:
            log = json.load(jsonFile)
        kept = log['frames'][start:start + count]
        for frame in kept:
            frame['frameNum'] += first_frame
        log['frames'] = kept
        log['pooled_metrics'] = {
            name: {key: round(value, 6) for key, value in
                   _pool([frame['metrics'][name] for frame in kept]).items()}
            for name in log.get('pooled_metrics', {})}
        with open(log_path, 'w') as jsonFile:
            json.dump(log, jsonFile, indent=2)
