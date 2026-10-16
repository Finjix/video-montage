"""Measure EBU R128 loudness and choose static, peak-safe segment gains."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

POLICY = "segment-loudness-balance/v1"
# Delivery loudness is -23 LUFS. The voice carries it; the BGM sits far enough below (see
# mix.auto_bgm_gain_db) that the finished mix measures within about 0.3 LU of the voice.
DEFAULTS = {"enabled": True, "target_lufs": -23.0, "true_peak_db": -3.0, "max_boost_db": 12.0}
MIN_SIGNAL_LUFS = -50.0


def configuration(value: object = None) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("audio_balance must be an object with supported options")
    config = {**DEFAULTS, **value}
    if type(config["enabled"]) is not bool:
        raise ValueError("audio_balance.enabled must be boolean")
    for key, bounds in (("target_lufs", (-30, -10)), ("true_peak_db", (-9, -1)), ("max_boost_db", (0, 18))):
        number = config[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
            raise ValueError(f"audio_balance.{key} must be finite and between {bounds[0]} and {bounds[1]}")
        config[key] = float(number)
    return config


def for_output(plan: dict, output: dict) -> dict:
    """Output options inherit the batch options, with the same validation."""
    base = configuration(plan.get("audio_balance"))
    override = output.get("audio_balance", {})
    if not isinstance(override, dict):
        raise ValueError("audio_balance output override must be an object")
    return configuration({**base, **override})


def measure(ffmpeg: Path, source: Path, filters: str) -> dict:
    """Analyze precisely the samples/channel layout used by the render; discard filter output."""
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-i", str(source),
               "-map", "0:a:0", "-vn", "-sn", "-dn", "-af",
               filters + ",loudnorm=I=-18:TP=-3:LRA=11:print_format=json",
               "-f", "null", os.devnull]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("audio balance measurement failed: " + result.stderr[-3000:])
    matches = re.findall(r'\{\s*"input_i"\s*:[^{}]*\}', result.stderr)
    if not matches:
        raise RuntimeError("audio balance measurement missing loudnorm statistics")
    stats = json.loads(matches[-1])
    measured = {}
    for key, field in (("integrated_lufs", "input_i"), ("true_peak_db", "input_tp")):
        number = float(stats[field])
        if math.isnan(number) or number == math.inf:
            raise RuntimeError("audio balance measurement invalid: " + field)
        # Silence and clips shorter than the R128 window can have no integrated measurement.
        measured[key] = round(number, 4) if math.isfinite(number) else None
    return measured


def choose_gains(measurements: list[dict], config: dict) -> tuple[float | None, list[dict]]:
    active = [row for row in measurements if row["integrated_lufs"] is not None
              and row["integrated_lufs"] >= MIN_SIGNAL_LUFS and row["true_peak_db"] is not None]
    # A shared attainable target keeps all speech at the same loudness without compression.
    target = min([config["target_lufs"]] + [min(row["integrated_lufs"] + config["max_boost_db"],
                  row["integrated_lufs"] + config["true_peak_db"] - row["true_peak_db"]) for row in active]) if active else None
    results = []
    for row in measurements:
        loudness, peak = row["integrated_lufs"], row["true_peak_db"]
        if peak is None:
            gain, status = 0.0, "silence"
        elif loudness is None or loudness < MIN_SIGNAL_LUFS:
            gain = min(0.0, config["true_peak_db"] - peak)
            status = "unmeasurable" if loudness is None else "below_signal_floor"
        else:
            gain, status = target - loudness, "balanced"
        results.append({**row, "gain_db": round(gain, 6), "status": status})
    return target, results


def verify(measured: dict, expected_lufs: float | None, config: dict) -> dict:
    peak = measured["true_peak_db"]
    loudness = measured["integrated_lufs"]
    # Allow small AAC reconstruction/measurement variation; normal QC still rejects clipping.
    peak_pass = peak is None or peak <= config["true_peak_db"] + 0.5
    loudness_pass = expected_lufs is None or expected_lufs < -60 or (
        loudness is not None and abs(loudness - expected_lufs) <= 1.0)
    return {**measured, "expected_lufs": expected_lufs, "peak_pass": peak_pass,
            "loudness_pass": loudness_pass, "decision": "pass" if peak_pass and loudness_pass else "reject"}
