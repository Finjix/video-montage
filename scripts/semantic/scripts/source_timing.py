"""Reject media whose decoded frame clock cannot authorize frame/FPS audio cuts."""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

_CHECKED: dict[tuple, int] = {}


def require_constant_frame_rate(ffprobe: Path, source: Path, stream: dict) -> int:
    """Return the actual decoded video frame count after validating its clock."""
    stat = source.stat()
    rate = Fraction(stream["avg_frame_rate"])
    tick = Fraction(stream["time_base"])
    key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, str(rate), str(tick))
    if key in _CHECKED:
        return _CHECKED[key]
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_frames",
         "-show_entries", "frame=best_effort_timestamp", "-of", "json", str(source)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    frames = json.loads(result.stdout).get("frames", [])
    if rate <= 0 or tick <= 0 or not frames:
        raise ValueError("decoded source frame timestamps required")
    tolerance = max(tick / 2, Fraction(1, 1_000_000))
    for index, frame in enumerate(frames):
        stamp = frame.get("best_effort_timestamp")
        if stamp is None or abs(int(stamp) * tick - Fraction(index, 1) / rate) > tolerance:
            raise ValueError("variable frame rate or nonzero video start: frame/FPS audio cuts are unsafe")
    _CHECKED[key] = len(frames)
    return len(frames)
