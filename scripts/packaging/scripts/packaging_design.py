"""Validate selections from the installed effect library; no task-created effects."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

import subtitle_fonts

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "assets/packaging/design/catalog.json"
COLORS = {"yellow": "#FFDE00", "white": "#FFFFFF"}
FLOWERS = ("ice1", "ice2", "fire1")
ENTRANCES = ("none", "fade", "bounce_up", "shout_wave", "ice_drift")
TEMPLATES = ()
FONT_SIZES = (8, 9, 10)


def font_size(value, label="font_size"):
    if type(value) is not int or value not in FONT_SIZES:
        raise ValueError(f"{label} must be 8, 9 or 10")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]", "", text).casefold()


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return value


def layout(value: dict | None) -> dict:
    value = value or {}
    if not isinstance(value, dict):
        raise ValueError("layout must be an object")
    if "size" in value:
        raise ValueError("layout.size has been removed; use font_size 8, 9 or 10")
    return {"x": number(value.get("x", .5), "layout.x", .02, .98),
            "y": number(value.get("y", .69), "layout.y", .02, .98),
            "max_width": number(value.get("max_width", .9), "layout.max_width", .1, .96)}


def entrance(value, available_ms: int) -> dict:
    if value is None:
        value = {"effect": "none"}
    if not isinstance(value, dict) or value.get("effect", "none") not in ENTRANCES:
        raise ValueError(f"entrance.effect must be one of {ENTRANCES}")
    if set(value) - {"effect", "duration_ms"}:
        raise ValueError("entrance only selects existing effects and duration_ms; custom effects/parameters are unsupported")
    effect = value.get("effect", "none")
    duration = value.get("duration_ms", 0 if effect == "none" else 350)
    if type(duration) is not int or (effect == "none" and duration != 0) or (effect != "none" and duration < 50):
        raise ValueError("entrance duration must be integer milliseconds; none has duration 0")
    # At least half the cue remains fully settled and readable.
    if duration > available_ms / 2:
        raise ValueError("entrance must settle within the first half of its display interval")
    return {"effect": effect, "duration_ms": duration}


def game_spans(text: str, names: list[str], flower: str) -> list[dict]:
    matches, occupied = [], set()
    for name in sorted(names, key=len, reverse=True):
        cursor = 0
        while (start := text.find(name, cursor)) >= 0:
            end = start + len(name)
            if not occupied.intersection(range(start, end)):
                matches.append({"start": start, "end": end, "flower": flower, "game_name": name})
                occupied.update(range(start, end))
            cursor = end
    return sorted(matches, key=lambda span: span["start"])


def cue_style(row: dict, cue: dict, names: list[str], default_flower: str, game_size=9) -> dict:
    if not isinstance(row, dict) or row.get("text") != cue["text"]:
        raise ValueError("subtitle design text must match the reviewed cue exactly")
    color = row.get("color", "yellow")
    size = font_size(row.get("font_size", 8))
    if color not in COLORS:
        raise ValueError("ordinary subtitle colors must be yellow or white")
    spans = copy.deepcopy(row.get("spans", []))
    if not isinstance(spans, list):
        raise ValueError("subtitle spans must be a list")
    for span in spans:
        if (not isinstance(span, dict) or type(span.get("start")) is not int or type(span.get("end")) is not int
                or not 0 <= span["start"] < span["end"] <= len(cue["text"])):
            raise ValueError("subtitle span must identify a valid character interval")
        if span.get("color", color) not in COLORS or span.get("flower") not in (None, *FLOWERS):
            raise ValueError("unsupported subtitle span color or flower")
        if "font_size" in span:
            font_size(span["font_size"])
    flowers = [span for span in spans if span.get("flower")]
    for i, span in enumerate(flowers):
        if any(max(span["start"], other["start"]) < min(span["end"], other["end"]) for other in flowers[:i]):
            raise ValueError("flower spans must not overlap")
    for game in game_spans(cue["text"], names, default_flower):
        overlapping = [s for s in flowers if max(s["start"], game["start"]) < min(s["end"], game["end"])]
        if overlapping:
            if len(overlapping) != 1 or overlapping[0]["start"] > game["start"] or overlapping[0]["end"] < game["end"]:
                raise ValueError("a game name must use one complete flower span")
            chosen_size = overlapping[0].setdefault("font_size", max(game_size, size))
        else:
            game["font_size"] = max(game_size, size)
            spans.append(game)
            chosen_size = game["font_size"]
        if chosen_size not in (9, 10):
            raise ValueError("game names must use font_size 9 or 10")
        for span in spans:
            if "font_size" in span and max(span["start"], game["start"]) < min(span["end"], game["end"]):
                if span["font_size"] != chosen_size:
                    raise ValueError("a complete game name must use one font_size, 9 or 10")
    breaks = row.get("line_breaks", [])
    if not isinstance(breaks, list) or len(breaks) > 1 or any(type(n) is not int or not 0 < n < len(cue["text"]) for n in breaks):
        raise ValueError("line_breaks allows one character boundary for a two-line subtitle")
    if any(s.get("flower") and s["start"] < n < s["end"] for n in breaks for s in spans):
        raise ValueError("line_breaks must preserve complete game/flower text")
    return {**copy.deepcopy(row), "color": color, "font_size": size, "spans": spans, "line_breaks": breaks, "layout": layout(row.get("layout")),
            "entrance": entrance(row.get("entrance"), cue["end_ms"] - cue["start_ms"])}


def prepare(value: dict, cues: list[dict], subtitle_sha256: str, layers: list[dict], base: Path, frames: int) -> dict:
    if isinstance(value, dict) and value.get("needs_design_review"):
        raise ValueError("subtitle text changed; revise subtitle_design.cues and remove needs_design_review after review")
    if not isinstance(value, dict) or value.get("subtitle_sha256") != subtitle_sha256:
        raise ValueError("subtitle_design must bind the reviewed subtitle SHA-256")
    allowed = {"subtitle_sha256", "font", "game_names", "game_flower", "game_font_size", "reason", "cues",
               "protected_regions", "graphic_layers", "needs_design_review"}
    if set(value) - allowed:
        raise ValueError("subtitle_design only selects existing library resources; unsupported design fields: "
                         + ", ".join(sorted(set(value) - allowed)))
    if value.get("font") not in subtitle_fonts.FONTS:
        raise ValueError("subtitle_design.font must explicitly select w8, smiley or fangtang")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("subtitle_design requires a concrete design reason")
    names = value.get("game_names")
    if (not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() or "\n" in n for n in names)
            or len(names) != len(set(names))):
        raise ValueError("game_names must be distinct nonempty strings (or an empty list for a nongame video)")
    game_flower = value.get("game_flower", "ice2")
    if game_flower not in FLOWERS:
        raise ValueError(f"game_flower must be one of {FLOWERS}")
    requested = value.get("cues")
    if (not isinstance(requested, list) or len(requested) != len(cues)
            or any(not isinstance(row, dict) or row.get("index") != i for i, row in enumerate(requested, 1))):
        raise ValueError("subtitle_design.cues must cover every reviewed cue in order, using 1-based indices")
    game_size = font_size(value.get("game_font_size", 9), "game_font_size")
    if game_size == 8:
        raise ValueError("game_font_size must be 9 or 10")
    styles = [cue_style(row, cue, names, game_flower, game_size) for row, cue in zip(requested, cues)]
    # A split game name cannot satisfy the all-occurrences guarantee.
    for name in names:
        compact = [normalized(c["text"]) for c in cues]
        if "".join(compact).count(normalized(name)) != sum(text.count(normalized(name)) for text in compact):
            raise ValueError(f"game name is split across subtitle cues; revise subtitles before packaging: {name}")
    if not isinstance(layers, list):
        raise ValueError("graphic_layers must be a list")
    if layers:
        raise ValueError("decorative graphic_layers have been removed; use reviewed subtitle styling only")
    protected = value.get("protected_regions", [])
    if not isinstance(protected, list):
        raise ValueError("protected_regions must be a list")
    for region in protected:
        if not isinstance(region, dict) or not isinstance(region.get("box"), list) or len(region["box"]) != 4:
            raise ValueError("protected region needs normalized [left,top,right,bottom]")
        left, top, right, bottom = [number(v, "protected coordinate", 0, 1) for v in region["box"]]
        if left >= right or top >= bottom:
            raise ValueError("protected region has empty bounds")
        start, end = region.get("start_frame", 0), region.get("end_frame_exclusive", frames)
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= frames:
            raise ValueError("protected region interval is invalid")
    return {**copy.deepcopy(value), "game_flower": game_flower, "game_font_size": game_size, "cues": styles,
            "graphic_layers": [], "protected_regions": protected}


def verify_sources(design: dict, cues: list[dict], asset_copy: dict, speech: str, request: str = "") -> None:
    """Check declared copy against already hash-bound Codex asset evidence."""
    assets = {row["sha256"]: row.get("text", "") for row in asset_copy.get("assets", [])}
    known = [speech, request, *assets.values()]
    for name in design["game_names"]:
        if not any(name in text for text in known):
            raise ValueError(f"game name lacks verified source evidence: {name}")

def refresh(design: dict, cues: list[dict], subtitle_sha256: str) -> dict:
    """Keep unchanged decisions. Changed text needs new explicit design decisions."""
    result = copy.deepcopy(design)
    old = result.get("cues", [])
    if len(old) != len(cues) or any(row.get("text") != cue["text"] for row, cue in zip(old, cues)):
        result["needs_design_review"] = True
    result["subtitle_sha256"] = subtitle_sha256
    return result


def evidence_frames(record: dict, speed: float, output_frames: int) -> list[int]:
    frames = set()
    for event in record.get("events", []):
        start = event["start_frame"] / speed
        end = event["end_frame_exclusive"] / speed
        settle = (event["start_frame"] + event["entrance_frames"]) / speed
        motion_end = min(end, settle)
        frames.update(range(max(0, math.floor(start) - 1), min(output_frames, math.ceil(motion_end) + 2)))
        frames.update([max(0, min(output_frames - 1, math.floor((settle + end) / 2))),
                       max(0, min(output_frames - 1, math.ceil(end) - 1)),
                       max(0, min(output_frames - 1, math.ceil(end)))])
    # Intersections include entrances and static overlaps between independent layers.
    events = record.get("events", [])
    for i, a in enumerate(events):
        for b in events[:i]:
            left, right = max(a["start_frame"], b["start_frame"]), min(a["end_frame_exclusive"], b["end_frame_exclusive"])
            if left < right:
                frames.add(min(output_frames - 1, round((left + right) / 2 / speed)))
    return sorted(frames)


def review_ok(record: dict | None, finding: dict | None) -> bool:
    return not record or bool(finding and finding.get("design_pass") is True
                             and isinstance(finding.get("design_reason"), str) and finding["design_reason"].strip())
