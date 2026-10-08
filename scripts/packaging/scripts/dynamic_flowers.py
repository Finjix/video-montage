"""Reusable flower text, generated from font outlines and procedural shaders.

The shader is a smooth transfer function of signed distance, normalized glyph
height and edge normal. No reference pixels or fixed-word images are read by
this renderer. Font contours, advances and counters always come from the text.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_UNITS = 500
SUPERSAMPLE = 4


def parameters(spec: dict) -> dict:
    if "shader" in spec:
        return spec
    path = Path(spec["path"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != spec["sha256"]:
        raise ValueError(f"flower effect changed: {path}")
    return json.loads(payload)


@lru_cache(maxsize=4)
def font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, FONT_UNITS)


@lru_cache(maxsize=4)
def font_profile(path: str) -> tuple[float, float]:
    # Normalize a font's CJK ink height/bearing into the shader's calibrated
    # coordinates. This preserves each font's proportions, slant and counters.
    _, top, _, bottom = font(path).getbbox("国田永")
    scale = 466 / (bottom - top)
    return scale, 9 - top * scale


@lru_cache(maxsize=128)
def glyph(char: str, font_path: str) -> tuple[np.ndarray, float, int, int]:
    face = font(font_path)
    bounds = face.getbbox(char)
    left, top = min(0, bounds[0]), min(0, bounds[1])
    width = max(600, bounds[2] - left + 20)
    height = max(600, bounds[3] - top + 20)
    image = Image.new("L", (width, height))
    ImageDraw.Draw(image).text((-left, -top), char, font=face, fill=255)
    return np.asarray(image), face.getlength(char), left, top


def advance(text: str, geometry: dict, font_path: str) -> float:
    scale, _ = font_profile(font_path)
    return sum(font(font_path).getlength(char) for char in text) / FONT_UNITS * scale * geometry["size"] * geometry["sx"] + max(0, len(text) - 1) * geometry["tracking"]


def text_extents(text: str, geometry: dict, font_path: str) -> tuple[float, float, float, float]:
    scale, shift = font_profile(font_path)
    sx, sy = geometry["size"] / FONT_UNITS * scale * geometry["sx"], geometry["size"] / FONT_UNITS * scale
    x = geometry["x"]
    boxes = []
    for char in text:
        left, top, right, bottom = font(font_path).getbbox(char)
        boxes.append((x + left * sx, geometry["y"] + shift * geometry["size"] / FONT_UNITS + top * sy,
                      x + right * sx, geometry["y"] + shift * geometry["size"] / FONT_UNITS + bottom * sy))
        x += font(font_path).getlength(char) * sx + geometry["tracking"]
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def font_mask(text: str, geometry: dict, shape: tuple[int, int], font_path: str) -> np.ndarray:
    height, width = shape
    result = np.zeros((height * SUPERSAMPLE, width * SUPERSAMPLE), np.float32)
    x = geometry["x"]
    profile_scale, profile_shift = font_profile(font_path)
    sx = geometry["size"] / FONT_UNITS * profile_scale * geometry["sx"]
    sy = geometry["size"] / FONT_UNITS * profile_scale
    for char in text:
        pixels, step, left, top = glyph(char, font_path)
        transform = np.array([[sx * SUPERSAMPLE, 0, (x + left * sx) * SUPERSAMPLE],
                              [0, sy * SUPERSAMPLE, (geometry["y"] + profile_shift * geometry["size"] / FONT_UNITS + top * sy) * SUPERSAMPLE]], np.float32)
        painted = cv2.warpAffine(pixels.astype(np.float32) / 255, transform,
                                 (width * SUPERSAMPLE, height * SUPERSAMPLE), flags=cv2.INTER_LINEAR)
        result = np.maximum(result, painted)
        x += step * sx + geometry["tracking"]
    return result


def signed_distance(alpha: np.ndarray) -> np.ndarray:
    solid = (alpha >= .5).astype(np.uint8)
    inside = cv2.distanceTransform(solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    outside = cv2.distanceTransform(1 - solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distance = (inside - outside) / SUPERSAMPLE
    distance += np.where(solid > 0, -.5 / SUPERSAMPLE, .5 / SUPERSAMPLE)
    near = (inside <= 1) & (outside <= 1)
    distance[near] = (alpha[near] - .5) / SUPERSAMPLE
    return distance


def ink_bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(alpha > .5)
    if not len(xs):
        raise ValueError("flower text has no visible glyphs")
    return (math.floor(int(xs.min()) / SUPERSAMPLE), math.floor(int(ys.min()) / SUPERSAMPLE),
            math.ceil((int(xs.max()) + 1) / SUPERSAMPLE), math.ceil((int(ys.max()) + 1) / SUPERSAMPLE))


def sample_table(table: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    dx, dy = (x - x0)[:, :, None], (y - y0)[:, :, None]
    return (table[y0, x0] * (1 - dx) * (1 - dy) + table[y0, x0 + 1] * dx * (1 - dy)
            + table[y0 + 1, x0] * (1 - dx) * dy + table[y0 + 1, x0 + 1] * dx * dy)


def shade(distance: np.ndarray, shader: dict) -> np.ndarray:
    """Shade any glyph contours with the same distance/height/normal function."""
    geometry = shader["geometry"]
    height, width = distance.shape
    d_nodes, y_nodes = np.asarray(shader["distance"]), np.asarray(shader["height"])
    d = np.interp(distance, d_nodes, np.arange(len(d_nodes)))
    yy = (np.arange(height) - geometry["y"]) / geometry["size"]
    y = np.broadcast_to(np.interp(yy, y_nodes, np.arange(len(y_nodes)))[:, None], (height, width))
    d, y = np.minimum(d, len(d_nodes) - 1.0001), np.minimum(y, len(y_nodes) - 1.0001)
    tables = np.asarray(shader["rgb"], dtype=np.float64)
    result = sample_table(tables[0], d, y)
    if shader["normals"]:
        nx = cv2.Sobel(distance, cv2.CV_64F, 1, 0, ksize=3) / 8
        ny = cv2.Sobel(distance, cv2.CV_64F, 0, 1, ksize=3) / 8
        norm = np.maximum(np.hypot(nx, ny), .01)
        nx, ny = nx / norm, ny / norm
        gate = np.clip((3 - distance) / 2, 0, 1) * np.clip((distance + 18) / 4, 0, 1)
        features = [nx * gate, ny * gate]
        if shader.get("order", 1) == 2:
            features += [(nx * nx - ny * ny) * gate, 2 * nx * ny * gate]
        for table, feature in zip(tables[1:], features):
            result += sample_table(table, d, y) * feature[:, :, None]
    return np.clip(result, 0, 255)


def render_text(text: str, spec: dict, font_path: str, *, reference_canvas: bool = False) -> tuple[Image.Image, tuple]:
    if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
        raise ValueError("flower text must be a nonempty single line")
    spec = parameters(spec)
    shader = spec["shader"]
    geometry = dict(shader["geometry"])
    left, top, right, bottom = text_extents(text, geometry, font_path)
    if not reference_canvas:
        # Include negative italic bearings and long descenders without cutting
        # off the glyph or the 17px shader/glow envelope.
        dx, dy = max(0, 18 - left), max(0, 18 - top)
        geometry["x"] += dx
        geometry["y"] += dy
        right, bottom = right + dx, bottom + dy
    shader = {**shader, "geometry": geometry}
    width = round(geometry["x"] + advance(text, geometry, font_path) + spec["right_padding"])
    width = max(width, math.ceil(right + 18))
    height = max(spec["canvas"][1], math.ceil(bottom + 18))
    if reference_canvas:
        width = spec["canvas"][0]
    alpha = font_mask(text, geometry, (height, width), font_path)
    bounds = ink_bounds(alpha)
    distance = cv2.resize(signed_distance(alpha), (width, height), interpolation=cv2.INTER_AREA)
    color = shade(distance, shader)
    # Opaque glyphs and strokes, with translucent anti-aliasing / outer glow.
    peak = color.max(axis=2)
    color[(peak < 1) | (distance < -17)] = 0
    peak = color.max(axis=2)
    opacity = np.where(distance >= -spec["solid_radius"], 1., np.minimum(1., peak / spec["glow_peak"]))
    opacity = np.maximum(np.rint(opacity * 255), np.ceil(peak)).astype(np.uint8)
    straight_rgb = np.rint(color * 255 / np.maximum(opacity[:, :, None], 1))
    straight_rgb[opacity == 0] = 0
    image = Image.fromarray(np.dstack([np.clip(straight_rgb, 0, 255).astype(np.uint8), opacity]))
    return image, bounds


def place_text(text: str, spec: dict, font_path: str, bounds: tuple[int, int, int, int], canvas: Image.Image) -> tuple:
    rendered, face = render_text(text, spec, font_path)
    fx0, fy0, fx1, fy1 = face
    x0, y0, x1, y1 = bounds
    scale = (x1 - x0) / (fx1 - fx0)
    sprite = rendered.resize((max(1, round(rendered.width * scale)), max(1, round(rendered.height * scale))), Image.Resampling.LANCZOS)
    x = round(x0 - fx0 * scale)
    y = round((y0 + y1) / 2 - (fy0 + fy1) / 2 * scale)
    if x < 0 or x + sprite.width > canvas.width or y < 0 or y + sprite.height > canvas.height:
        raise ValueError("flower subtitle exceeds the render canvas; shorten the subtitle")
    canvas.alpha_composite(sprite, (x, y))
    return x, y, sprite.width, sprite.height
