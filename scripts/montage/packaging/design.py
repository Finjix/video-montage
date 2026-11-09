"""Validate subtitle designs against the installed effect library; no task-created effects."""
from __future__ import annotations

import copy
import math
import re

from montage.packaging import fonts as subtitle_fonts
from montage.packaging import segmentation as subtitle_segmentation

COLORS = {"yellow": "#FFDE00", "white": "#FFFFFF"}
FLOWERS = ("ice1", "ice2", "fire1")
ENTRANCES = ("none", "bounce_up", "shout_wave", "ice_drift")
FONT_SIZES = (8, 9)


def font_size(value, label="font_size"):
    if type(value) is not int or value not in FONT_SIZES:
        raise ValueError(f"{label} must be 8 or 9")
    return value


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
        raise ValueError("layout.size has been removed; use font_size 8 or 9")
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
    if cue["text"].splitlines() != [cue["text"]]:
        raise ValueError("subtitles must stay on one line; split into sequential captions")
    subtitle_segmentation.require_line_limit(cue["text"])
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
    games = game_spans(cue["text"], names, default_flower)
    for game in games:
        overlapping = [s for s in flowers if max(s["start"], game["start"]) < min(s["end"], game["end"])]
        if overlapping:
            if len(overlapping) != 1 or overlapping[0]["start"] > game["start"] or overlapping[0]["end"] < game["end"]:
                raise ValueError("a game name must use one complete flower span")
            chosen_size = overlapping[0].setdefault("font_size", max(game_size, size))
        else:
            game["font_size"] = max(game_size, size)
            spans.append(game)
            chosen_size = game["font_size"]
        if chosen_size != 9:
            raise ValueError("game names must use font_size 9")
        for span in spans:
            if "font_size" in span and max(span["start"], game["start"]) < min(span["end"], game["end"]):
                if span["font_size"] != chosen_size:
                    raise ValueError("a complete game name must use one font_size, 9")
    # Match the renderer's ordered span overrides. Only actual game-name
    # characters may differ from the ordinary text's uniform size.
    character_sizes = [size] * len(cue["text"])
    for span in spans:
        if "font_size" in span:
            character_sizes[span["start"]:span["end"]] = [span["font_size"]] * (span["end"] - span["start"])
    game_characters = {i for game in games for i in range(game["start"], game["end"])}
    ordinary_sizes = {value for i, value in enumerate(character_sizes)
                      if i not in game_characters and not cue["text"][i].isspace()}
    if len(ordinary_sizes) > 1:
        raise ValueError("only game names may use a different font_size within a cue; ordinary emphasis must display separately at one uniform size")
    colors = [color] * len(cue["text"])
    flower_characters = set()
    for span in spans:
        if "color" in span:
            colors[span["start"]:span["end"]] = [span["color"]] * (span["end"] - span["start"])
        if span.get("flower"):
            flower_characters.update(range(span["start"], span["end"]))
    ordinary_colors = {c for i, c in enumerate(colors)
                       if i not in flower_characters and normalized(cue["text"][i])}
    if "white" in ordinary_colors and "yellow" not in ordinary_colors:
        raise ValueError("white subtitles require yellow emphasis in the same cue; standalone white is forbidden")
    breaks = row.get("line_breaks", [])
    if not isinstance(breaks, list) or breaks:
        raise ValueError("line_breaks is unsupported: subtitles must stay on one line")
    return {**copy.deepcopy(row), "color": color, "font_size": size, "spans": spans, "line_breaks": breaks, "layout": layout(row.get("layout")),
            "entrance": entrance(row.get("entrance"), cue["end_ms"] - cue["start_ms"])}


def batch_effects(designs: list[dict]) -> dict:
    """Validate whole-batch animation variety, preserving quiet reading cues."""
    counts = {}
    for design in designs:
        for cue in design["cues"]:
            effect = cue.get("entrance", {}).get("effect", "none")
            if effect != "none":
                counts[effect] = counts.get(effect, 0) + 1
    required = min(3, len(designs)) if len(designs) > 1 else 0
    if len(counts) < required:
        raise ValueError("batch packaging must vary existing subtitle animations; a single repeated effect cannot satisfy the batch")
    total = sum(counts.values())
    if len(designs) > 1 and total >= 6 and max(counts.values(), default=0) > total * .7:
        raise ValueError("one subtitle animation dominates more than 70 percent of the batch")
    return {"animation_counts": counts, "required_animation_types": required}


def prepare(value: dict, cues: list[dict], frames: int) -> dict:
    """Validate one video's design against the library rules; returns the normalized design."""
    allowed = {"font", "game_names", "game_flower", "game_font_size", "cues", "protected_regions"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("design fields: " + ", ".join(sorted(allowed)))
    if value.get("font") not in subtitle_fonts.FONTS:
        raise ValueError(f"font must be one of {sorted(subtitle_fonts.FONTS)}")
    names = value.get("game_names", [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names) or len(names) != len(set(names)):
        raise ValueError("game_names must be distinct non-empty strings")
    game_flower = value.get("game_flower", "ice2")
    if game_flower not in FLOWERS:
        raise ValueError(f"game_flower must be one of {FLOWERS}")
    requested = value.get("cues")
    if (not isinstance(requested, list) or len(requested) != len(cues)
            or any(not isinstance(row, dict) or row.get("index") != i for i, row in enumerate(requested, 1))):
        raise ValueError("design cues must cover every subtitle cue in order (1-based index)")
    game_size = font_size(value.get("game_font_size", 9), "game_font_size")
    if game_size == 8:
        raise ValueError("game_font_size must be 9")
    styles = [cue_style(row, cue, names, game_flower, game_size) for row, cue in zip(requested, cues)]
    standalone = [style for style, cue in zip(styles, cues) if normalized(cue["text"]) in {normalized(n) for n in names}]
    for style, cue in zip(styles, cues):
        if style in standalone:
            interval = cue["end_ms"] - cue["start_ms"]
            if interval < 800 or interval - style["entrance"]["duration_ms"] < 600:
                raise ValueError("a standalone game-name cue needs 800 ms on screen and 600 ms settled")
    if len(standalone) > 1:
        raise ValueError("at most one standalone game-name cue per video")
    for name in names:
        compact = [normalized(c["text"]) for c in cues]
        if "".join(compact).count(normalized(name)) != sum(text.count(normalized(name)) for text in compact):
            raise ValueError(f"game name split across subtitle cues: {name}")
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
    return {**copy.deepcopy(value), "game_names": names, "game_flower": game_flower, "game_font_size": game_size,
            "cues": styles, "protected_regions": protected}
