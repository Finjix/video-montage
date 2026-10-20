"""Loudness measurement, per-segment balance, BGM gain and decoded-audio checks."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from montage.common import FFMPEG, run

TARGET_LUFS = -23.0       # finished video, BGM included
TOLERANCE_LU = 1.0
TRUE_PEAK_DB = -3.0       # per balanced voice segment
DELIVERY_PEAK_DB = -1.0
MAX_BOOST_DB = 12.0
MIN_SIGNAL_LUFS = -50.0
MIN_VOICE_OVER_MUSIC_DB = 4.0


def measure(path: Path, filters: str = "anull", seek: float | None = None) -> dict:
    """EBU R128 integrated loudness and true peak of the filtered first audio stream."""
    command = [str(FFMPEG), "-hide_banner", "-nostdin", "-nostats"]
    if seek is not None:
        command += ["-ss", f"{seek:.6f}"]
    command += ["-i", str(path), "-map", "0:a:0", "-vn", "-af", filters + ",loudnorm=I=-18:TP=-3:LRA=11:print_format=json",
                "-f", "null", os.devnull]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r'\{\s*"input_i"\s*:[^{}]*\}', result.stderr)
    if result.returncode or not found:
        raise RuntimeError(f"loudness measurement failed: {result.stderr[-1500:]}")
    stats = json.loads(found[-1])
    values = {}
    for key, field in (("lufs", "input_i"), ("true_peak", "input_tp")):
        number = float(stats[field])
        values[key] = round(number, 3) if math.isfinite(number) else None
    return values


def balance(measurements: list[dict], target: float = TARGET_LUFS) -> list[float]:
    """Static gain per segment toward one attainable common loudness (no compression, peak-safe)."""
    active = [m for m in measurements if m["lufs"] is not None and m["lufs"] >= MIN_SIGNAL_LUFS and m["true_peak"] is not None]
    common = min([target] + [min(m["lufs"] + MAX_BOOST_DB, m["lufs"] + TRUE_PEAK_DB - m["true_peak"]) for m in active])
    gains = []
    for m in measurements:
        if m["true_peak"] is None:
            gains.append(0.0)
        elif m["lufs"] is None or m["lufs"] < MIN_SIGNAL_LUFS:
            gains.append(round(min(0.0, TRUE_PEAK_DB - m["true_peak"]), 3))
        else:
            gains.append(round(common - m["lufs"], 3))
    return gains


def pcm(path: Path, rate: int = 16000) -> np.ndarray:
    data = run([FFMPEG, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate),
                "-f", "s16le", "-"], binary=True)
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def clipped(samples: np.ndarray) -> int:
    return int(np.count_nonzero(np.abs(samples) >= 0.999))


def edge_db(samples: np.ndarray, frame: int, rate: int = 16000) -> tuple[float, float]:
    """RMS dBFS of the 40 ms before and after an output frame boundary."""
    width = round(rate * 0.04)
    center = round(frame * rate / 60)
    def db(chunk):
        return 20 * math.log10(max(float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0, 1e-8))
    return round(db(samples[max(0, center - width):center]), 1), round(db(samples[center:center + width]), 1)


def voice_over_music_db(voice: np.ndarray, music: np.ndarray, gain_db: float) -> float:
    """10th percentile of 0.5 s voice/music RMS ratios over the speech windows (music looped)."""
    if music.size < 16000:
        raise ValueError("BGM decode empty")
    gain = 10 ** (gain_db / 20)
    ratios = []
    for start in range(0, voice.size - 8000 + 1, 8000):
        speech = voice[start:start + 8000]
        spoken = float(np.sqrt(np.mean(speech * speech)))
        if spoken < 10 ** (-35 / 20):
            continue
        index = np.arange(start, start + 8000) % music.size
        level = float(np.sqrt(np.mean(music[index] * music[index]))) * gain
        ratios.append(20 * math.log10(max(spoken, 1e-8) / max(level, 1e-8)))
    return round(float(np.quantile(ratios, .1)), 2) if ratios else float("-inf")


def auto_bgm_gain_db(voice: np.ndarray, music: np.ndarray) -> float:
    """Loudest BGM gain that keeps the voice at least 4 dB over the music (the gate is linear in the gain)."""
    unity = voice_over_music_db(voice, music, 0.0)
    if not math.isfinite(unity):
        raise ValueError("no speech windows to balance the BGM against")
    return min(0.0, math.floor((unity - MIN_VOICE_OVER_MUSIC_DB) * 10) / 10)
