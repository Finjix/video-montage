"""Nameplates: the plate artwork cut to its visible size and placed beside the speaker, clear of every face.

A nameplate PNG is a 9:16 frame with the plate drawn where the designer put it (upper left). `package` cuts out the
plate once per file and places it at one of four spots: a side of the speaker (left/right) and a height (upper at the
designed height, lower ending above the subtitles). On its side the plate stands next to the speaker's face and moves
toward the frame edge only as far as it must to clear the faces (and, where it can, the shoulders). Faces are found
with YuNet (OpenCV, bundled model) on frames sampled across the window.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
from PIL import Image

from montage import catalog
from montage.common import ROOT, sha

VERSION = 2
WIDTH, HEIGHT = 1440, 2560
SPOTS = ("upper-left", "upper-right", "lower-left", "lower-right")
# Settled subtitles start at y >= 1648 and entrances reach 1561 for a few frames; a lower plate ends above them.
LOWER_BOTTOM = 1620
OPAQUE = 96          # plate pixels that hide what is under them; the outer glow does not
CLEAR = .02          # share of one face a plate may touch and still count as clear
DETECT = (360, 640)  # detection size; a face at frame scale stays >= 30 px wide
EVERY = 12           # frame step of the samples within a window
GAP = 30             # px between a plate and the (widened) face box beside it
STEP = 24            # px per move toward the frame edge while a plate still covers a face or the shoulders
SHOULDERS = .9       # half the shoulder width, in widths of the widened face box
TORSO = .12          # share of the plate that may lie over the estimated shoulders and torso
NEAR = 48            # spots whose distance to the face differs by less than this count as equally near
STEADY = 1 / 3       # a face counts when it is found on at least this share of the samples (min 2)
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
    """The spots with no speaker to stand beside: left and right keep the artwork's side margin, upper is its
    designed height, lower ends above the subtitles."""
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


def overlap(a, b) -> float:
    width, height = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - width * height
    return width * height / union


def steady(found: dict[int, list], numbers: list[int]) -> dict[int, list]:
    """Only the faces that recur across the window; a one-frame detection (game art, a pattern) is dropped."""
    need = max(2, round(len(numbers) * STEADY)) if len(numbers) > 1 else 1
    return {n: [box for box in found.get(n, [])
                if sum(any(overlap(box, other) >= .3 for other in found.get(m, [])) for m in numbers) >= need]
            for n in numbers}


def speaker(found: dict[int, list], numbers: list[int]) -> tuple[float, float, float, float] | None:
    """The speaker's face box: the median of the largest face on each sampled frame."""
    largest = [max(found[n], key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) for n in numbers if found.get(n)]
    return tuple(float(np.median([box[i] for box in largest])) for i in range(4)) if largest else None


def torso(face) -> tuple[float, float, float, float]:
    left, _, right, bottom = face
    centre, half = (left + right) / 2, SHOULDERS * (right - left)
    return centre - half, bottom, centre + half, HEIGHT


def share(opaque: np.ndarray, spot: tuple[int, int], box) -> float:
    """The share of the plate's opaque pixels inside `box`."""
    x, y = spot
    height, width = opaque.shape
    a, b = round(max(box[0], x)) - x, round(min(box[2], x + width)) - x
    c, d = round(max(box[1], y)) - y, round(min(box[3], y + height)) - y
    return float(opaque[c:d, a:b].sum()) / max(1, int(opaque.sum())) if a < b and c < d else 0.


def distance(plate: dict, spot: tuple[int, int], face) -> float:
    x, y = spot
    dx = max(0., face[0] - (x + plate["width"]), x - face[2])
    dy = max(0., face[1] - (y + plate["height"]), y - face[3])
    return float(np.hypot(dx, dy))


def place(plate: dict, opaque: np.ndarray, found: dict[int, list], numbers: list[int]) -> tuple[str, dict[str, dict]]:
    """Every spot at its nearest position beside the speaker that leaves the faces clear (else the least covering),
    and the chosen spot: the nearest clear one, the designed spot first among equals."""
    found = steady(found, numbers)
    order, base, face = preference(plate), spots(plate), speaker(found, numbers)
    low, high = base["upper-left"][0], base["upper-right"][0]

    def covers(x, y):
        return max((cover(opaque, (x, y), found.get(n, [])) for n in numbers), default=0.)

    def shoulders(x, y):
        bodies = [torso(max(found[n], key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))) for n in numbers if found.get(n)]
        return max((share(opaque, (x, y), body) for body in bodies), default=0.)
    result = {}
    for name in order:
        y = base[name][1]
        if face is None:
            positions = [base[name][0]]
        elif name.endswith("left"):
            near = min(high, max(low, round(face[0] - GAP - plate["width"])))
            positions = [*range(near, low, -STEP), low]
        else:
            near = max(low, min(high, round(face[2] + GAP)))
            positions = [*range(near, high, STEP), high]
        rows = [(x, covers(x, y)) for x in positions]
        clear = [(x, c) for x, c in rows if c <= CLEAR]
        if clear:
            x, c = next(((x, c) for x, c in clear if shoulders(x, y) <= TORSO), clear[0])
        else:
            x, c = min(rows, key=lambda row: row[1])
        result[name] = {"x": int(x), "y": int(y), "cover": round(c, 3), "shoulders": round(shoulders(x, y), 3),
                        "distance": round(distance(plate, (x, y), face)) if face else 0}
    clear = [name for name in order if result[name]["cover"] <= CLEAR]
    if clear:
        chosen = min(clear, key=lambda name: (result[name]["shoulders"] > TORSO, result[name]["distance"] // NEAR,
                                               order.index(name)))
    else:
        chosen = min(order, key=lambda name: result[name]["cover"])
    return chosen, result
