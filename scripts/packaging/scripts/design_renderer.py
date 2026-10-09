"""Transparent, deterministic subtitle tracks for reviewed designs."""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import dynamic_flowers
import flower_effects
import packaging_design as design
import subtitle_fonts

WIDTH, HEIGHT, FPS = 1440, 2560, 60
PARAMETERS = design.ROOT / "assets/packaging/animations/parameters"


@lru_cache(maxsize=12)
def motion_parameters(effect: str) -> dict:
    return json.loads((PARAMETERS / f"{effect}.json").read_text(encoding="utf-8"))


def rgba(rgb: np.ndarray) -> Image.Image:
    """Unpremultiply calibrated RGB-on-black without carrying a black rectangle."""
    color = np.clip(rgb, 0, 255)
    alpha = np.ceil(color.max(axis=2)).astype(np.uint8)
    straight = np.clip(np.rint(color * 255 / np.maximum(1, alpha[:, :, None])), 0, 255).astype(np.uint8)
    straight[alpha == 0] = 0
    return Image.fromarray(np.dstack([straight, alpha]))




def outlined_mask(mask: Image.Image, radius: float) -> Image.Image:
    """Dilate the filled glyph, including corners missed by font outline stroking."""
    def dilated(integer):
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (integer * 2 + 1, integer * 2 + 1))
        return Image.fromarray(cv2.dilate(np.asarray(mask), kernel))
    integer = math.floor(radius)
    inside = dilated(integer)
    # Keep the calibrated fractional thickness instead of rounding it away.
    return Image.blend(inside, dilated(integer + 1), radius - integer) if radius > integer else inside


def text_sprite(text: str, font_path: str, size: int, color="yellow", spans=None, max_width=None, line_breaks=None) -> Image.Image:
    """Fixed-size, single-line glyphs; resegment long cues before rendering."""
    if line_breaks or text.splitlines() != [text]:
        raise ValueError("subtitles must stay on one line; split reviewed cue into sequential captions")
    chars = [{"color": color, "flower": None, "pixels": size} for _ in text]
    for span in spans or []:
        for i in range(span["start"], span["end"]):
            for key in ("color", "flower", "pixels"):
                if key in span:
                    chars[i][key] = span[key]
    fonts = {style["pixels"]: ImageFont.truetype(font_path, style["pixels"]) for style in chars}
    runs = []
    for char, style in zip(text, chars):
        font = fonts[style["pixels"]]
        if not char.isspace():
            missing, glyph = font.getmask("\U0010ffff"), font.getmask(char)
            if glyph.size == missing.size and bytes(glyph) == bytes(missing):
                raise ValueError(f"selected subtitle font has no glyph for {char!r}")
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + char, style)
        else:
            runs.append((char, style))
    largest = max(fonts)
    margin = max(48, round(largest * 1.2))
    gap = max(10, round(largest * .09))
    # Reserve the glow/outline outside each face; never shrink a long subtitle.
    reserve = round(largest * .4)
    limit = float("inf") if max_width is None else max_width - reserve
    units, cursor = [], 0
    for content, style in runs:
        for unit in ([content] if style["flower"] else list(content)):
            units.append((unit, style, cursor))
            cursor += len(unit)

    def line_parts(items):
        line, used = [], 0.
        for unit, style, _ in items:
            advance = fonts[style["pixels"]].getlength(unit)
            spacing = gap if line and (style["flower"] or line[-1][1]["flower"]) else 0
            if line and line[-1][1] == style and not spacing:
                line[-1] = (line[-1][0] + unit, style, line[-1][2])
            else:
                line.append((unit, style, used + spacing))
            used += spacing + advance
        return line, used

    complete = line_parts(units)
    if complete[1] > limit:
        raise ValueError("subtitle cannot fit on one line at font_size 8/9; split reviewed cue into sequential captions")
    lines = [complete]
    metrics = []
    for line, width in lines:
        boxes = [fonts[style["pixels"]].getbbox(content, anchor="ls") for content, style, _ in line]
        metrics.append((min(box[1] for box in boxes), max(box[3] for box in boxes)))
    leading = round(largest * .25)
    width = math.ceil(max(w for _, w in lines) + margin * 2)
    height = sum(bottom - top for top, bottom in metrics) + leading * (len(lines) - 1) + margin * 2
    canvas = Image.new("RGBA", (width, height))
    effects = flower_effects.effect_styles()["styles"]
    y = margin
    for (line, advance), (top, bottom) in zip(lines, metrics):
        baseline = y - top
        for content, style, offset in line:
            font = fonts[style["pixels"]]
            x = (width - advance) / 2 + offset
            box = font.getbbox(content, anchor="ls")
            if style["flower"]:
                bounds = (round(x + box[0]), round(baseline + box[1]), round(x + box[2]), round(baseline + box[3]))
                if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                    dynamic_flowers.place_text(content, effects[style["flower"]], font_path, bounds, canvas)
            else:
                mask = Image.new("L", canvas.size)
                ImageDraw.Draw(mask).text((x, baseline), content, font=font, anchor="ls", fill=255)
                outline = Image.new("RGBA", canvas.size, "black")
                outline.putalpha(outlined_mask(mask, subtitle_fonts.OUTLINE_PIXELS))
                canvas.alpha_composite(outline)
                fill = Image.new("RGBA", canvas.size, design.COLORS[style["color"]])
                fill.putalpha(mask)
                canvas.alpha_composite(fill)
        y += bottom - top + leading
    bounds = canvas.getbbox()
    return canvas.crop(bounds) if bounds else canvas


def fit_sprite(sprite: Image.Image, layout: dict, *, expansion=1.0) -> Image.Image:
    limit = WIDTH * layout["max_width"]
    horizontal = 2 * min(layout["x"], 1 - layout["x"]) * WIDTH - 24
    vertical = 2 * min(layout["y"], 1 - layout["y"]) * HEIGHT - 24
    if sprite.width > limit or sprite.width * expansion > horizontal or sprite.height * expansion > vertical:
        raise ValueError("font_size 8/9 cannot fit this layout/motion; revise layout or split subtitle")
    return sprite



def reference_piece(animation, n: int) -> tuple[Image.Image, float, float]:
    from reference_animation import vertical_blur
    sprite = animation.sprite
    if n >= 30:
        return rgba(sprite.rgb), 0., 0.
    item = animation.parameters["frames"][n]
    if not item.get("visible", True):
        return Image.new("RGBA", (1, 1)), 0., 0.
    if animation.effect == "bounce_up":
        pixels, radius = vertical_blur(sprite.rgb, item["kernels"], item.get("sigma_x", 0.))
        x, y = sprite.x + item.get("dx", 0), sprite.y + item["dy"] - radius
    elif animation.effect == "shout_wave":
        pixels, x, y = animation.shout_frame(n)
    else:
        pixels, x, y = animation.ice_frame(n)
    return rgba(pixels), x - sprite.x, y - sprite.y


def paint_event(event: dict, frame: int) -> tuple[Image.Image, int, int]:
    sprite, setting = event["sprite"], event["entrance"]
    elapsed = frame - event["start_frame"]
    duration = event["entrance_frames"]
    progress = 1. if duration == 0 else min(1., elapsed / duration)
    effect = setting["effect"]
    dx = dy = 0.
    scale = 1.
    if "reference" in event:
        n = 30 if progress >= 1 else min(29, int(progress * 30))
        image, rx, ry = reference_piece(event["reference"], n)
        ratio = event["reference_scale"]
        sprite = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
        x = event["base_x"] + rx * ratio
        y = event["base_y"] + ry * ratio
        return sprite, round(x), round(y)
    if progress < 1:
        if effect == "fade":
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda a: round(a * progress)))
        elif effect == "bounce_up":
            item = motion_parameters(effect)["frames"][min(29, int(progress * 30))]
            if not item.get("visible", True):
                return Image.new("RGBA", (1, 1)), event["base_x"], event["base_y"]
            dy = max(-120., min(120., item.get("dy", 0) * sprite.height / 220))
            if progress < .2:
                sprite = sprite.filter(ImageFilter.GaussianBlur((.2 - progress) * 10))
        elif effect == "shout_wave":
            item = motion_parameters(effect)["frames"][min(29, int(progress * 30))]
            scale = min(1.55, max(.7, item.get("main_scale", 1 + .5 * (1 - progress))))
            echo = Image.new("RGBA", (max(sprite.width + 30, round(sprite.width * 1.4)), max(sprite.height + 30, round(sprite.height * 1.4))))
            for expansion, opacity in [(1.2, .22), (1.4, .12)]:
                ghost = sprite.resize((max(1, round(sprite.width * expansion)), max(1, round(sprite.height * expansion))), Image.Resampling.LANCZOS)
                ghost.putalpha(ghost.getchannel("A").point(lambda a: round(a * opacity * (1 - progress))))
                echo.alpha_composite(ghost, ((echo.width - ghost.width) // 2, (echo.height - ghost.height) // 2))
            echo.alpha_composite(sprite, ((echo.width - sprite.width) // 2, (echo.height - sprite.height) // 2))
            sprite = echo
        elif effect == "ice_drift":
            sprite = sprite.copy()
            mask = np.asarray(sprite.getchannel("A")).copy()
            visible = round(sprite.width * min(1., progress * 1.4 + .1))
            mask[:, visible:] = 0
            sprite.putalpha(Image.fromarray(mask))
            dx = (1 - progress) * -24
            draw = ImageDraw.Draw(sprite)
            for i in range(10):
                x = (sprite.width * ((i * .137 + progress * .12) % 1))
                y = sprite.height * ((i * .217 + progress * .35) % 1)
                draw.line((x - 3, y, x + 3, y), fill=(220, 250, 255, 220), width=1)
                draw.line((x, y - 3, x, y + 3), fill=(220, 250, 255, 220), width=1)
    if scale != 1:
        sprite = sprite.resize((max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))), Image.Resampling.LANCZOS)
    return sprite, round(event["cx"] - sprite.width / 2 + dx), round(event["cy"] - sprite.height / 2 + dy)


def prepare_track(directory: Path, cues: list[dict], value: dict, font_path: str, frames: int) -> dict:
    events, resources = [], {str(design.CATALOG): design.digest(design.CATALOG)}
    for name in ("design_renderer.py", "packaging_design.py", "subtitle_fonts.py", "reference_animation.py", "animation_common.py", "animation_shader.py", "dynamic_flowers.py"):
        path = Path(__file__).parent / name
        resources[str(path)] = design.digest(path)
    for i, (cue, setting) in enumerate(zip(cues, value["cues"]), 1):
        size = subtitle_fonts.pixels(value["font"], setting["font_size"])
        spans = [dict(span, pixels=subtitle_fonts.pixels(value["font"], span.get("font_size", setting["font_size"]))) for span in setting["spans"]]
        effect = setting["entrance"]["effect"]
        expansion = 2.4 if effect == "shout_wave" else 1.
        width = min(WIDTH * setting["layout"]["max_width"],
                    (2 * min(setting["layout"]["x"], 1 - setting["layout"]["x"]) * WIDTH - 24) / expansion)
        sprite = text_sprite(cue["text"], font_path, size, setting["color"], spans, max_width=width, line_breaks=setting.get("line_breaks"))
        event = {"kind": "subtitle", "index": i, "text": cue["text"], "sprite": sprite,
                 "font_size": size, "editor_font_size": setting["font_size"],
                 "span_font_sizes": [{"start": s["start"], "end": s["end"], "font_size": s.get("font_size", setting["font_size"]), "pixels": s["pixels"]} for s in spans],
                 "layout": setting["layout"], "entrance": setting["entrance"],
                 "start_frame": round(cue["start_ms"] * FPS / 1000),
                 "end_frame_exclusive": round(cue["end_ms"] * FPS / 1000)}
        full_ice = next((s for s in spans if s["start"] == 0 and s["end"] == len(cue["text"]) and s.get("flower") == "ice2"), None)
        if value["font"] == "w8" and full_ice and len(cue["text"]) <= 32 and effect in ("bounce_up", "shout_wave", "ice_drift") and "\n" not in cue["text"]:
            from reference_animation import Animation
            reference = Animation(effect, cue["text"])
            event["font_size"] = full_ice["pixels"]
            event["reference"] = reference
            event["sprite"] = rgba(reference.sprite.rgb)
        events.append(event)
    resources[font_path] = design.digest(Path(font_path))
    for event in events:
        event["entrance_frames"] = round(event["entrance"]["duration_ms"] * FPS / 1000)
        effect = event["entrance"]["effect"]
        expansion = 2.4 if effect == "shout_wave" else 1.
        if "reference" in event:
            # Fit every calibrated frame, retaining its relative displacement.
            pieces = [reference_piece(event["reference"], n) for n in range(31)]
            left = min(x for _, x, _ in pieces); top = min(y for _, _, y in pieces)
            right = max(x + image.width for image, x, _ in pieces)
            bottom = max(y + image.height for image, _, y in pieces)
            static = event["sprite"]
            cx, cy = event["layout"]["x"] * WIDTH, event["layout"]["y"] * HEIGHT
            calibrated_size = motion_parameters("static")["geometry"]["size"]
            required_scale = event["font_size"] / calibrated_size
            scale = min(required_scale,
                        event["layout"]["max_width"] * WIDTH / (right - left),
                        (cx - 12) / max(1, static.width / 2 - left),
                        (WIDTH - cx - 12) / max(1, right - static.width / 2),
                        (cy - 12) / max(1, static.height / 2 - top),
                        (HEIGHT - cy - 12) / max(1, bottom - static.height / 2))
            if scale < required_scale - 1e-6:
                raise ValueError("calibrated animation cannot fit at selected font_size; choose another entrance or layout")
            event["reference_scale"] = scale
            event["sprite"] = static.resize((max(1, round(static.width * scale)), max(1, round(static.height * scale))), Image.Resampling.LANCZOS)
        else:
            event["sprite"] = fit_sprite(event["sprite"], event["layout"], expansion=expansion)
        event["cx"], event["cy"] = event["layout"]["x"] * WIDTH, event["layout"]["y"] * HEIGHT
        event["base_x"] = round(event["cx"] - event["sprite"].width / 2)
        event["base_y"] = round(event["cy"] - event["sprite"].height / 2)
        if effect in ("bounce_up", "shout_wave", "ice_drift"):
            path = PARAMETERS / f"{effect}.json"
            resources[str(path)] = design.digest(path)
            if "reference" in event:
                static_path = PARAMETERS / "static.json"
                resources[str(static_path)] = design.digest(static_path)
                def tables(item):
                    if isinstance(item, dict):
                        if "table_file" in item:
                            path = PARAMETERS / item["table_file"]
                            if design.digest(path) != item["table_sha256"]:
                                raise ValueError("calibrated animation table changed")
                            resources[str(path)] = item["table_sha256"]
                        for child in item.values():
                            tables(child)
                    elif isinstance(item, list):
                        for child in item:
                            tables(child)
                tables(event["reference"].parameters)
                tables(json.loads(static_path.read_text(encoding="utf-8")))
    for style_id in {s["flower"] for c in value["cues"] for s in c["spans"] if s.get("flower")} | {value["game_flower"]}:
        if style_id in ("ice1", "ice2", "fire1"):
            path = flower_effects.ASSET_ROOT / f"{style_id}.json"
            resources[str(path)] = design.digest(path)
    boundaries = {0, frames}
    for event in events:
        boundaries.update((event["start_frame"], event["end_frame_exclusive"]))
        boundaries.update(range(event["start_frame"], min(event["end_frame_exclusive"], event["start_frame"] + event["entrance_frames"] + 1)))
    # Determine the shared cropped track band from every motion frame.
    band_top, band_bottom = HEIGHT, 0
    records = []
    for event in events:
        union = [WIDTH, HEIGHT, 0, 0]
        samples = set(range(event["start_frame"], event["start_frame"] + event["entrance_frames"] + 1))
        samples.add(event["end_frame_exclusive"] - 1)
        samples.update(r.get("start_frame", 0) for r in value["protected_regions"] if event["start_frame"] <= r.get("start_frame", 0) < event["end_frame_exclusive"])
        for frame in sorted(samples):
            image, x, y = paint_event(event, frame)
            bbox = image.getbbox()
            if not bbox:
                continue
            bounds = [x + bbox[0], y + bbox[1], x + bbox[2], y + bbox[3]]
            if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > WIDTH or bounds[3] > HEIGHT:
                raise ValueError(f"{event['kind']} {event['index']} motion exceeds canvas; revise its layout")
            for region in value["protected_regions"]:
                if region.get("start_frame", 0) <= frame < region.get("end_frame_exclusive", frames):
                    l, t, r, b = region["box"]
                    if max(bounds[0], l * WIDTH) < min(bounds[2], r * WIDTH) and max(bounds[1], t * HEIGHT) < min(bounds[3], b * HEIGHT):
                        raise ValueError(f"{event['kind']} {event['index']} overlaps a protected region")
            union = [min(union[0], bounds[0]), min(union[1], bounds[1]), max(union[2], bounds[2]), max(union[3], bounds[3])]
        band_top, band_bottom = min(band_top, union[1]), max(band_bottom, union[3])
        records.append({key: event[key] for key in ("kind", "index", "text", "start_frame", "end_frame_exclusive", "entrance_frames", "entrance", "editor_font_size", "font_size", "span_font_sizes")}
                       | {"bounds": union, "base_position": [event["base_x"], event["base_y"]],
                          "settled_size": list(event["sprite"].size),
                          "reference_scale": event.get("reference_scale")})
    if not events:
        raise ValueError("design has no visible events")
    band_top, band_bottom = max(0, band_top - 2), min(HEIGHT, band_bottom + 2)
    band_height = max(2, band_bottom - band_top)
    concat = ["ffconcat version 1.0"]
    saved = {}
    sorted_boundaries = sorted(boundaries)
    last_name = None
    for frame, end in zip(sorted_boundaries, sorted_boundaries[1:]):
        if end <= frame:
            continue
        canvas = Image.new("RGBA", (WIDTH, band_height))
        for event in events:
            if event["start_frame"] <= frame < event["end_frame_exclusive"]:
                image, x, y = paint_event(event, frame)
                canvas.alpha_composite(image, (x, y - band_top))
        key = hashlib.sha256(canvas.tobytes()).hexdigest()
        if key not in saved:
            name = f"design-{len(saved):05}.png"
            canvas.save(directory / name)
            saved[key] = name
        last_name = saved[key]
        concat += [f"file '{last_name}'", "option framerate 60", f"duration {(end - frame) / FPS:.9f}"]
    concat += [f"file '{last_name}'", "option framerate 60"]
    target = directory / "design-track.ffconcat"
    target.write_text("\n".join(concat) + "\n", encoding="utf-8")
    return {"path": str(target), "y": band_top, "record": {"schema": "video-montage-rendered-design/v1",
            "font": value["font"], "subtitle_sha256": value["subtitle_sha256"], "reason": value["reason"],
            "ordinary_outline": {"color": "#000000", "editor_width": subtitle_fonts.EDITOR_OUTLINE_WIDTH,
                                 "radius_pixels": subtitle_fonts.OUTLINE_PIXELS, "flower_outline": "existing_effect"},
            "events": records, "resources": [{"path": path, "sha256": sha} for path, sha in sorted(resources.items())],
            "game_names": value["game_names"], "cues": value["cues"], "graphic_layers": value["graphic_layers"]}}
