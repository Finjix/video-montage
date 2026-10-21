"""Pure frame intervals shared by catalog and plan checks; no media dependencies."""
from pathlib import Path
from montage.common import read, REJECTED

SILENT_EDGE = 0.045


def expand(spans: list[list[int]]) -> list[int]:
    return [frame for start, end in spans for frame in range(start, end + 1)]


# ----------------------------------------------------------------------------------- rejected intervals

def rejected_entries() -> list[dict]:
    return read(REJECTED)["entries"]


def rejected_hits(source: dict, start: int, end: int) -> list[str]:
    """User-rejected intervals overlapping native frames [start, end) of a source (hash or name match)."""
    hits = []
    fps = source["fps_num"] / source["fps_den"]
    for entry in rejected_entries():
        hashes = {str(value).lower() for field in ("source_sha256", "derived_source_sha256") for value in entry.get(field, [])}
        name = str(entry.get("source_name_contains", "")).casefold()
        if source["sha256"] not in hashes and not (name and name in Path(source["path"]).stem.casefold()):
            continue
        if entry.get("source_frame_ranges_exclusive"):
            overlap = any(start < right and end > left for left, right in entry["source_frame_ranges_exclusive"])
        else:
            guard = float(entry.get("guard", 0.35))
            overlap = start / fps < float(entry["end"]) + guard and end / fps > float(entry["start"]) - guard
        if overlap:
            hits.append(entry["entry_id"])
    return hits


