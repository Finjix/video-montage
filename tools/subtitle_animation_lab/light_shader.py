"""Reusable distance/height/horizontal-light lattice on supplied font contours.

This is a color-transfer function, not a canvas texture or a fixed-word atlas.
The horizontal lattice describes the moving flash; the distance axis attaches
strokes, trails and glow to whatever glyphs the caller supplies.
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
import cv2
import numpy as np

from common import LAB, FONT, sha, write_json, dynamic_flowers, glyph_alpha, signed_distance, foreground_ssim


def coordinates(distance, shader):
    geometry = shader["geometry"]
    dn, yn, xn = [np.asarray(shader[key]) for key in ("distance", "height", "light_x")]
    d = np.minimum(np.interp(distance, dn, np.arange(len(dn))), len(dn) - 1.0001)
    yp = (np.arange(distance.shape[0])[:, None] - geometry["y"]) / geometry["size"]
    y = np.broadcast_to(np.minimum(np.interp(yp, yn, np.arange(len(yn))), len(yn) - 1.0001), distance.shape)
    xp = (np.arange(distance.shape[1])[None, :] - geometry["x"]) / shader["horizontal_width"]
    x = np.broadcast_to(np.minimum(np.interp(xp, xn, np.arange(len(xn))), len(xn) - 1.0001), distance.shape)
    return d, y, x


def layout(distance, shader, selected=None):
    d, y, x = coordinates(distance, shader)
    if selected is not None:
        d, y, x = d[selected], y[selected], x[selected]
    di, yi, xi = [np.floor(v).astype(np.int32) for v in (d, y, x)]
    dd, dy, dx = d - di, y - yi, x - xi
    nd, ny = len(shader["distance"]), len(shader["height"])
    indices, weights = [], []
    for ix in range(2):
        for iy in range(2):
            for id_ in range(2):
                indices.append(((xi + ix) * ny + yi + iy) * nd + di + id_)
                weights.append((dx if ix else 1 - dx) * (dy if iy else 1 - dy) * (dd if id_ else 1 - dd))
    return np.stack(indices, -1), np.stack(weights, -1).astype(np.float32)


def normals(distance, shader):
    nx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3) / 8
    ny = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3) / 8
    length = np.maximum(np.hypot(nx, ny), .01)
    nx, ny = nx / length, ny / length
    ratio = shader["normal_scale"]
    outer = shader["color_cutoff"]
    gate = np.clip((3 * ratio - distance) / (2 * ratio), 0, 1) * np.clip((distance + outer) / (4 * ratio), 0, 1)
    return np.stack([np.ones(distance.shape), nx * gate, ny * gate, (nx * nx - ny * ny) * gate, 2 * nx * ny * gate], -1).astype(np.float32)


@lru_cache(maxsize=48)
def load_table(filename, digest, directory=str(LAB / "parameters")):
    root = Path(directory).resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("Shader tables must be inside the parameter directory")
    if sha(path) != digest:
        raise ValueError(f"Light shader changed: {path}")
    with np.load(path, allow_pickle=False) as data:
        return data["coefficients"].astype(np.float32)


def shade_light(distance, shader):
    indices, weights = layout(distance, shader)
    fields = normals(distance, shader)
    coefficients = shader.get("coefficients")
    if coefficients is None:
        coefficients = load_table(shader["table_file"], shader["table_sha256"], shader.get("_parameter_dir", str(LAB / "parameters")))
    coefficients = np.asarray(coefficients, np.float32)
    result = np.zeros((*distance.shape, 3), np.float32)
    for f in range(5):
        value = np.zeros_like(result)
        for corner in range(8):
            value += coefficients[f][indices[..., corner]] * weights[..., corner, None]
        result += value * fields[..., f, None]
    fade = shader.get("edge_fade", 0.)
    if fade:
        result *= np.clip((distance + shader["color_cutoff"]) / fade, 0., 1.)[:, :, None]
    result[distance < -shader["color_cutoff"]] = 0
    return np.clip(result, 0, 255)


def fit_light(reference, text, geometry, prior_shader, iterations=100, node_spacing=32, cutoff=125):
    width = dynamic_flowers.advance(text, geometry, str(FONT))
    dn, yn = np.array(prior_shader["distance"]), np.array(prior_shader["height"])
    # The x coordinate follows the input phrase, rather than absolute pixels.
    xn = np.linspace(-cutoff / width, 1 + cutoff / width, max(8, round((width + cutoff * 2) / node_spacing) + 1))
    shader = {"lighting_grid": True, "geometry": geometry, "distance": dn.tolist(), "height": yn.tolist(),
              "light_x": xn.tolist(), "horizontal_width": width, "normal_scale": prior_shader["normal_scale"],
              "color_cutoff": cutoff}
    distance = signed_distance(glyph_alpha(text, geometry, reference.shape[:2]))
    selected = distance > -cutoff
    indices, weights = layout(distance, shader, selected)
    fields = normals(distance, shader)[selected]
    count = len(dn) * len(yn) * len(xn)
    weighted = [weights * fields[:, f:f + 1] for f in range(5)]
    target = reference[selected].astype(np.float32)

    def forward(coeff):
        result = np.zeros_like(target)
        for f in range(5):
            for corner in range(8):
                result += coeff[f][indices[:, corner]] * weighted[f][:, corner:corner + 1]
        return result

    def transpose(pixels):
        result = np.zeros((5, count, 3), np.float32)
        for f in range(5):
            for c in range(3):
                result[f, :, c] = np.bincount(indices.ravel(), (weighted[f] * pixels[:, c:c + 1]).ravel(), minlength=count)
        return result

    def penalty(coeff):
        grid = coeff.reshape(5, len(xn), len(yn), len(dn), 3)
        result = grid * .003
        for axis, strength in [(1, .07), (2, .08)]:
            front = [slice(None)] * 5
            middle, back = front.copy(), front.copy()
            front[axis], middle[axis], back[axis] = slice(None, -2), slice(1, -1), slice(2, None)
            second = grid[tuple(front)] - 2 * grid[tuple(middle)] + grid[tuple(back)]
            result[tuple(front)] += strength * second
            result[tuple(middle)] -= 2 * strength * second
            result[tuple(back)] += strength * second
        return result.reshape(coeff.shape)

    prior = np.asarray(prior_shader["rgb"], np.float32).reshape(5, len(yn) * len(dn), 3)
    coeff = np.tile(prior[:, None], (1, len(xn), 1, 1)).reshape(5, count, 3)
    diag = np.zeros((5, count, 1), np.float32)
    for f in range(5):
        diag[f, :, 0] = np.bincount(indices.ravel(), (weighted[f] ** 2).ravel(), minlength=count)
    diag += .6
    op = lambda c: transpose(forward(c)) + penalty(c)
    residual = transpose(target) - op(coeff)
    z = residual / diag
    direction = z.copy()
    rz = (residual * z).sum(axis=(0, 1))
    best, best_score = coeff.copy(), -1.
    for iteration in range(iterations):
        ad = op(direction)
        step = rz / np.maximum((direction * ad).sum(axis=(0, 1)), 1e-15)
        coeff += direction * step
        residual -= ad * step
        z = residual / diag
        new_rz = (residual * z).sum(axis=(0, 1))
        direction = z + direction * new_rz / np.maximum(rz, 1e-15)
        rz = new_rz
        if iteration % 20 == 19 or iteration == iterations - 1:
            image = np.zeros_like(reference)
            image[selected] = np.clip(np.rint(forward(coeff)), 0, 255).astype(np.uint8)
            score = foreground_ssim(reference, image)
            print(f"Light lattice {iteration + 1}: {score:.6f}", flush=True)
            if score > best_score:
                best, best_score = coeff.copy(), score
    shader["coefficients"] = best
    return shader, distance


def freeze(shader, parameter_dir: Path, filename: str):
    target = parameter_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, coefficients=shader["coefficients"].astype(np.float32))
    return {**{k: v for k, v in shader.items() if k != "coefficients"}, "table_file": filename, "table_sha256": sha(target)}
