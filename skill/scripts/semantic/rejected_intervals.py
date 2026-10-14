"""User-rejected source intervals, matched by source hash or name and native frames."""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / "assets/rejected-source-intervals.json"


def entries() -> list[dict]:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))["entries"]


def hits(segment: dict, entry: dict) -> bool:
    """Renaming a file or candidate cannot bypass a rejection."""
    hashes = {str(value).lower() for field in ("source_sha256", "derived_source_sha256") for value in entry.get(field, [])}
    name = str(entry.get("source_name_contains", "")).casefold()
    if (str(segment.get("source_sha256", "")).lower() not in hashes
            and not (name and name in Path(str(segment.get("source_path", ""))).stem.casefold())):
        return False
    start, end = segment["source_in_frame"], segment["source_out_frame_exclusive"]
    if entry.get("source_frame_ranges_exclusive"):
        return any(start < right and end > left for left, right in entry["source_frame_ranges_exclusive"])
    seconds = segment["source_fps_den"] / segment["source_fps_num"]
    guard = float(entry.get("guard", 0.35))
    return start * seconds < float(entry["end"]) + guard and end * seconds > float(entry["start"]) - guard
