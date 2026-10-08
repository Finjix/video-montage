"""Dynamic font-based subtitle flowers, with libass-measured placement."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
import dynamic_flowers
import subtitle_fonts

ASSET_ROOT = Path(__file__).resolve().parents[3] / "assets/packaging/flowers"
MODES = ("random_ice", "ice1", "ice2", "fire1")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def effect_styles() -> dict:
    manifest = json.loads((ASSET_ROOT / "styles.json").read_text(encoding="utf-8"))
    for spec in manifest["styles"].values():
        path = ASSET_ROOT / spec["path"]
        if sha(path) != spec["sha256"]:
            raise ValueError(f"flower effect changed: {path}")
        spec["path"] = str(path.resolve())
    return manifest


def choose(mode: str, count: int, seed: int) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"subtitle_flower must be one of {MODES}")
    rng = random.Random(seed)
    return [rng.choice(("ice1", "ice2")) if mode == "random_ice" else mode for _ in range(count)]


def video_encoding(styles: list[str]) -> dict:
    # Fire's saturated, thin red strokes lose color in 4:2:0. This style is
    # explicitly selected only; the default random ice pool retains 4:2:0.
    return {"pixel_format": "yuv444p", "crf": 8, "overlay_format": "rgb"} if "fire1" in styles else {
        "pixel_format": "yuv420p", "crf": 18, "overlay_format": "auto"}


def measure_layout(ffmpeg: Path, directory: Path, header: str, occurrences: list[dict],
                   style: dict, width: int, height: int, font_dir: Path) -> tuple[list[tuple], int, int]:
    """Render each target occurrence as a white mask, keeping libass advances.

    The ordinary subtitle and the invisible title reserve exactly the same
    space as this mask; no estimated PIL/FreeType text advances are involved.
    """
    scale = width / style["ass"]["play_res_x"]
    center = style["ass"]["position_y"] * height / style["ass"]["play_res_y"]
    band_height = min(height, math.ceil(style["emphasis"]["ass_font_size"] * scale * 2 + 100))
    band_y = max(0, min(height - band_height, round(center - band_height / 2)))
    lines = []
    for i, item in enumerate(occurrences):
        start, end = i * 4, (i + 1) * 4  # centiseconds, one mask per 25-fps frame
        def stamp(value):
            return f"{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}"
        lines.append(f"Dialogue: 0,{stamp(start)},{stamp(end)},Default,,0,0,0,,"
                     + r"{\bord0\shad0\blur0\1c&HFFFFFF&}"
                     + item["marker_text"])
    mask_path = directory / "flower-layout.ass"
    mask_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    fonts = os.path.relpath(font_dir, directory).replace("\\", "/")
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "verbose", "-nostdin", "-f", "lavfi", "-i",
               f"color=black:s={width}x{height}:r=25:d={len(occurrences) / 25:.6f}",
               "-vf", f"ass={mask_path.name}:fontsdir={fonts},format=rgb24,crop={width}:{band_height}:0:{band_y}",
               "-frames:v", str(len(occurrences)), "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    bounds = []
    with (directory / "flower-layout.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE, stderr=log)
        try:
            size = width * band_height * 3
            for _ in occurrences:
                chunks, received = [], 0
                while received < size:
                    chunk = process.stdout.read(size - received)
                    if not chunk:
                        log.flush()
                        detail = (directory / "flower-layout.log").read_text(encoding="utf-8", errors="replace")[-2000:]
                        raise RuntimeError(f"FFmpeg did not render all flower layout masks: {detail}")
                    chunks.append(chunk)
                    received += len(chunk)
                pixels = np.frombuffer(b"".join(chunks), np.uint8).reshape(band_height, width, 3)
                ys, xs = np.where(pixels.max(axis=2) > 180)
                if not len(xs):
                    raise ValueError("empty flower placement mask")
                bounds.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
            if process.wait() != 0:
                raise RuntimeError("flower layout render failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
    font_log = (directory / "flower-layout.log").read_text(encoding="utf-8", errors="replace")
    subtitle_fonts.require_selected(font_log, style["font_family"], style["font_postscript"])
    return bounds, band_y, band_height


def prepare_track(ffmpeg: Path, directory: Path, ass_path: Path, occurrences: list[dict], style: dict,
                  font_dir: Path, duration_ms: int, width: int = 1440, height: int = 2560) -> dict | None:
    if not occurrences:
        return None
    header = ass_path.read_text(encoding="utf-8-sig").split("Dialogue:", 1)[0]
    bounds, band_y, band_height = measure_layout(ffmpeg, directory, header, occurrences, style, width, height, font_dir)
    specs = style["emphasis"]["effects"]
    blank = Image.new("RGBA", (width, band_height))
    blank.save(directory / "flower-blank.png")
    grouped = {}
    records = []
    for item, box in zip(occurrences, bounds):
        cue = item["cue_index"]
        group = grouped.setdefault(cue, {"image": blank.copy(), "start": round(item["start_ms"] / 10) * 10,
                                       "end": round(item["end_ms"] / 10) * 10})
        placement = dynamic_flowers.place_text(item["text"], specs[item["style_id"]], style["font"]["path"], box, group["image"])
        records.append({key: item[key] for key in ("cue_index", "occurrence_index", "start_ms", "end_ms", "style_id", "text")}
                       | {"bounds": [placement[0], placement[1] + band_y, placement[2], placement[3]]})
    entries, cursor = [], 0
    for cue, group in sorted(grouped.items()):
        if group["start"] > cursor:
            entries.append(("flower-blank.png", group["start"] - cursor))
        name = f"flower-cue-{cue}.png"
        group["image"].save(directory / name)
        entries.append((name, group["end"] - group["start"]))
        cursor = group["end"]
    entries.append(("flower-blank.png", max(100, duration_ms - cursor)))
    concat = ["ffconcat version 1.0"]
    for name, duration in entries:
        if duration <= 0:
            raise ValueError("flower cue has zero duration after ASS time rounding")
        concat += [f"file '{name}'", "option framerate 1000", f"duration {duration / 1000:.6f}"]
    concat += ["file 'flower-blank.png'", "option framerate 1000"]
    path = directory / "flower-track.ffconcat"
    path.write_text("\n".join(concat) + "\n", encoding="utf-8")
    return {"path": str(path), "y": band_y, "choices": records}
