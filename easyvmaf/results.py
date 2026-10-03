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
from typing import Dict, List, Optional, Sequence

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
