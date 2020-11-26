#!/usr/bin/env python3

import sys
import argparse
import numpy as np
import matplotlib.pyplot as plt
from math import log10
import json
import os
from statistics import mean, harmonic_mean


def plot_vmaf(vmafs,output_path):
    # Create datapoints
    output_path=output_path
    file_name=os.path.basename(output_path)
    x = [x for x in range(len(vmafs))]
    hmean=round(harmonic_mean(vmafs),2)
    amean=round(mean(vmafs),2)
    plot_size = len(vmafs)

    # Plot
    figure_width = 3 + round((4 * log10(plot_size)))
    
    plt.figure(figsize=(figure_width, 5))
    [plt.axhline(i, color='grey', linewidth=0.4) for i in range(0, 100)]
    [plt.axhline(i, color='black', linewidth=0.6) for i in range(0, 100, 5)]
    plt.plot(x, vmafs, label=f'Frames: {len(vmafs)}\nMean:{amean}\nHarmonic Mean:{hmean}\n', linewidth=0.7)

    plt.plot([1, plot_size], [amean, amean], ':', color='black')
    plt.annotate(f'Mean: {amean}', xy=(0, amean), color='black')
    plt.ylabel('VMAF')
    plt.legend(loc='upper center', bbox_to_anchor=(
        0.5, -0.05), fancybox=True, shadow=True)
    
    plt.ylim(60, 100)
    plt.tight_layout()
    plt.margins(0)
    plt.title(file_name)
    # Save
    plt.savefig(output_path+".png", dpi=500)


def plot_vmaf_xml(file,output_path):
    vmafs = []
    with open(file, 'r') as f:
        file = f.readlines()
        file = [x.strip() for x in file if 'vmaf="' in x]
        vmafs = []
        for i in file:
            vmf = i[i.rfind('="') + 2: i.rfind('"')]
            vmafs.append(float(vmf))

        vmafs = [round(float(x), 3) for x in vmafs if type(x) == float]
        plot_vmaf(vmafs,output_path)
    return(vmafs)


def plot_vmaf_json(file,output_path):
    vmafs = []
    with open(file, 'r') as f:
        data = json.load(f)
        vmafs = json_extract(data, 'vmaf')

    vmafs = [round(float(x), 3) for x in vmafs if type(x) == float]
    plot_vmaf(vmafs,output_path)
    return(vmafs)


def json_extract(obj, key):
    """Recursively fetch values from nested JSON."""
    arr = []

    def extract(obj, arr, key):
        """Recursively search for values of key in JSON tree."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    extract(v, arr, key)
                elif k == key:
                    arr.append(v)
        elif isinstance(obj, list):
            for item in obj:
                extract(item, arr, key)
        return arr

    values = extract(obj, arr, key)
    return values
