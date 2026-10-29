"""Nameplates: the plate artwork cut to its visible size and a spot for each window that leaves the faces clear.

A nameplate PNG is a 9:16 frame with the plate drawn where the designer put it (upper left). `package` cuts out the
plate once per file and places it at one of four spots; faces are found with YuNet (OpenCV, bundled model) on
frames sampled across the window.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
from PIL import Image

from montage import catalog
from montage.common import ROOT, sha

VERSION = 1
WIDTH, HEIGHT = 1440, 2560
SPOTS = ("upper-left", "upper-right", "lower-left", "lower-right")
# Settled subtitles start at y >= 1648 and entrances reach 1561 for a few frames; a lower plate ends above them.
LOWER_BOTTOM = 1620
OPAQUE = 96          # plate pixels that hide what is under them; the outer glow does not
CLEAR = .02          # share of one face a plate may touch and still count as clear
DETECT = (360, 640)  # detection size; a face at frame scale stays >= 30 px wide
EVERY = 12           # frame step of the samples within a window
MODEL = ROOT / "assets/packaging/faces/face_detection_yunet_2023mar.onnx"


def trim(nameplate: dict, person_id: str, folder: Path) -> dict:
    """The visible plate of a 9:16 nameplate PNG at frame scale, saved as `<folder>/<person>.png`; `x`, `y` are its
    designed position."""
    with Image.open(nameplate["path"]) as image:
        art = image.convert("RGBA")
    box = art.getchannel("A").getbbox()
    if not box:
        raise ValueError(f"nameplate {nameplate['path']} has no visible content")
    scale = WIDTH / art.width
    plate = art.crop(box)
    if scale != 1:
        plate = plate.resize((round(plate.width * scale), round(plate.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    plate.save(buffer, format="PNG")
    target = folder / f"{person_id.split(':', 1)[1]}.png"
    if not target.is_file() or target.read_bytes() != buffer.getvalue():
        folder.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        partial.write_bytes(buffer.getvalue())
        os.replace(partial, target)
    return {"path": str(target), "sha256": sha(target), "x": round(box[0] * scale), "y": round(box[1] * scale),
            "width": plate.width, "height": plate.height}


def spots(plate: dict) -> dict[str, tuple[int, int]]:
    """Left and right keep the artwork's side margin, upper is its designed height, lower ends above the subtitles."""
    margin = min(plate["x"], WIDTH - plate["x"] - plate["width"])
    left, right = margin, WIDTH - margin - plate["width"]
    upper = plate["y"]
    lower = max(upper, LOWER_BOTTOM - plate["height"])
    return {"upper-left": (left, upper), "upper-right": (right, upper), "lower-left": (left, lower), "lower-right": (right, lower)}


def preference(plate: dict) -> list[str]:
    """The designed spot first, then the other side at the same height, then lower on the designed side."""
    home = "left" if plate["x"] <= WIDTH - plate["x"] - plate["width"] else "right"
    away = "right" if home == "left" else "left"
    return [f"upper-{home}", f"upper-{away}", f"lower-{home}", f"lower-{away}"]


def mask(plate: dict) -> np.ndarray:
    with Image.open(plate["path"]) as image:
        return np.asarray(image.convert("RGBA").getchannel("A")) >= OPAQUE


def samples(window: dict) -> list[int]:
    start, end = window["start_frame"], window["end_frame_exclusive"]
    return sorted({*range(start, end, EVERY), end - 1})


def faces(video: Path, numbers: list[int]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Face boxes in frame pixels on the given frames, widened to the hair and ears."""
    import cv2
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    detector = cv2.FaceDetectorYN.create(str(MODEL), "", DETECT, 0.6, 0.3, 50)
    scale = WIDTH / DETECT[0]
    found = {}
    for number, image in catalog.grab(video, numbers, size=DETECT).items():
        _, rows = detector.detect(np.ascontiguousarray(np.asarray(image)[:, :, ::-1]))
        found[number] = [((x - .15 * w) * scale, (y - .35 * h) * scale, (x + 1.15 * w) * scale, (y + 1.1 * h) * scale)
                         for x, y, w, h in (rows[:, :4].tolist() if rows is not None else [])]
    return found


def cover(opaque: np.ndarray, spot: tuple[int, int], boxes) -> float:
    """The largest share of one face the plate hides at `spot`."""
    x, y = spot
    height, width = opaque.shape
    worst = 0.
    for left, top, right, bottom in boxes:
        left, top, right, bottom = max(left, 0), max(top, 0), min(right, WIDTH), min(bottom, HEIGHT)
        area = (right - left) * (bottom - top)
        a, b = round(max(left, x)) - x, round(min(right, x + width)) - x
        c, d = round(max(top, y)) - y, round(min(bottom, y + height)) - y
        if area > 0 and a < b and c < d:
            worst = max(worst, float(opaque[c:d, a:b].sum()) / area)
    return worst


def place(plate: dict, opaque: np.ndarray, found: dict[int, list], numbers: list[int]) -> tuple[str, dict[str, float]]:
    """The first spot in preference order that leaves every sampled face clear, else the one hiding the least."""
    order, where = preference(plate), spots(plate)
    covers = {name: round(max((cover(opaque, where[name], found.get(n, [])) for n in numbers), default=0.), 3)
              for name in order}
    clear = [name for name in order if covers[name] <= CLEAR]
    return (clear[0] if clear else min(order, key=lambda name: covers[name])), covers
