"""Build reusable effect previews and optional real-scene packaging demonstrations."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
import package_video as pack
import packaging_design as design
import design_renderer as renderer
import subtitle_fonts


def track_images(directory, track, numbers):
    timeline, cursor, name = [], 0, None
    for line in Path(track["path"]).read_text(encoding="utf-8").splitlines():
        if line.startswith("file '"):
            name = line[6:-1]
        elif line.startswith("duration "):
            end = cursor + round(float(line.split()[1]) * 60)
            timeline.append((cursor, end, name)); cursor = end
    for number in numbers:
        filename = next(n for start, end, n in timeline if start <= number < end)
        with Image.open(directory / filename) as original:
            yield original.convert("RGBA")


def previews():
    destination = design.CATALOG.parent / "previews"
    destination.mkdir(exist_ok=True)
    catalog = json.loads(design.CATALOG.read_text(encoding="utf-8"))
    value = {"font": "w8", "game_names": ["无尽冬日"], "game_flower": "ice2"}
    font = subtitle_fonts.FONTS["w8"]["path"]
    for effect in design.ENTRANCES:
        setting = {"effect": effect, "duration_ms": 0 if effect == "none" else 400}
        sheet = Image.new("RGB", (1200, 480), "#183047")
        face = ImageFont.truetype(font, 20)
        cue = {"text": "无尽冬日", "start_ms": 0, "end_ms": 1200}
        presentation = {**value, "subtitle_sha256": "preview", "reason": "原字号与动画过程预览",
                        "cues": [{"index": 1, "text": cue["text"], "entrance": setting}]}
        with tempfile.TemporaryDirectory(prefix="design-preview-") as temporary:
            directory = Path(temporary)
            prepared = design.prepare(presentation, [cue], "preview", [], directory, 72)
            track = renderer.prepare_track(directory, [cue], prepared, font, 72)
            animated = []
            for frame, pixels in zip(range(0, 72, 2), track_images(directory, track, range(0, 72, 2))):
                tile = Image.new("RGB", (600, 440), "#183047")
                pixels.thumbnail((580, 420), Image.Resampling.LANCZOS)
                tile.paste(pixels, ((600 - pixels.width) // 2, (440 - pixels.height) // 2), pixels)
                animated.append(tile)
            animation = destination / f"{effect}.webp"
            animated[0].save(animation, save_all=True, append_images=animated[1:], duration=33, loop=0, lossless=True)
            catalog["entrances"][effect]["motion_preview"] = {"path": f"previews/{animation.name}", "sha256": design.digest(animation)}
            for i, (frame, pixels) in enumerate(zip((0, 5, 12, 24), track_images(directory, track, (0, 5, 12, 24)))):
                tile = Image.new("RGB", (300, 440), "#183047")
                pixels.thumbnail((300, 400), Image.Resampling.LANCZOS)
                tile.paste(pixels, ((300 - pixels.width) // 2, (440 - pixels.height) // 2), pixels)
                sheet.paste(tile, (i * 300, 40))
                ImageDraw.Draw(sheet).text((i * 300 + 10, 10), f"{effect} / {frame / 60:.2f}s", font=face, fill="white")
        path = destination / f"{effect}.png"; sheet.save(path)
        catalog["entrances"][effect]["preview"] = {"path": f"previews/{path.name}", "sha256": design.digest(path)}
    design.CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Refresh previews of existing subtitle entrances")
    parser.add_argument("--catalog", action="store_true", required=True)
    parser.parse_args()
    previews()

if __name__ == "__main__":
    main()
