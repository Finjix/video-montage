"""Offline calibration; reference pixels are never consulted by the renderer."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import *

STATIC_CROP = (590, 2200, 730, 320)


def stable_reference(path: Path) -> np.ndarray:
    return np.median(np.stack(list(decode(path, STATIC_CROP))[30:]), axis=0).astype(np.uint8)


def geometry_search(reference: np.ndarray, output: Path) -> dict:
    geometry = initial_geometry()
    best = foreground_ssim(reference, initial_rgb(REFERENCE_TEXT, geometry))
    print(f"Static initial: {best:.6f} {geometry}", flush=True)
    for steps in [(1., 1., .5, .25, .003), (.3, .3, .15, .08, .001), (.1, .1, .05, .025, .0003)]:
        for iteration in range(3):
            improved = False
            for name, step in zip(("x", "y", "size", "tracking", "sx"), steps):
                candidate_best = best
                selected = geometry
                for sign in (-1, 1):
                    trial = {**geometry, name: geometry[name] + sign * step}
                    score = foreground_ssim(reference, initial_rgb(REFERENCE_TEXT, trial))
                    if score > candidate_best:
                        candidate_best, selected = score, trial
                if candidate_best > best:
                    best, geometry, improved = candidate_best, selected, True
            print(f"Static geometry: {best:.6f} {geometry}", flush=True)
            if not improved:
                break
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "geometry.json", {"geometry": geometry, "score_before_shader_fit": best, "crop": STATIC_CROP})
    cv2.imwrite(str(output / "geometry-comparison.png"), cv2.cvtColor(np.hstack([reference, initial_rgb(REFERENCE_TEXT, geometry)]), cv2.COLOR_RGB2BGR))
    return geometry


def fit_shader(reference: np.ndarray, geometry: dict, output: Path, prior_shader=None, iterations=180,
               filename="static.json", text=REFERENCE_TEXT, horizontal_order=0, outer_radius=None) -> dict:
    """Fit a reusable signed-distance / height / edge-normal color shader.

    Matrix-free conjugate gradients keep calibration on bundled NumPy, without
    SciPy or a pixel atlas. Parameters describe contours, never fixed glyphs.
    """
    spec = base_spec()
    initial = prior_shader or spec["shader"]
    geometry_ratio = geometry["size"] / initial["geometry"]["size"]
    ratio = initial.get("normal_scale", 1.) * geometry_ratio
    dn, yn = np.array(initial["distance"]) * geometry_ratio, np.array(initial["height"])
    nd, ny = len(dn), len(yn)
    distance = signed_distance(glyph_alpha(text, geometry, reference.shape[:2]))
    yy = np.broadcast_to((np.arange(reference.shape[0])[:, None] - geometry["y"]) / geometry["size"], distance.shape)
    cutoff = outer_radius or 19 * ratio
    selected = distance > -cutoff
    d = np.minimum(np.interp(distance[selected], dn, np.arange(nd)), nd - 1.0001)
    y = np.minimum(np.interp(yy[selected], yn, np.arange(ny)), ny - 1.0001)
    di, yi = np.floor(d).astype(int), np.floor(y).astype(int)
    dx, dy = d - di, y - yi
    indices = np.stack([yi * nd + di, yi * nd + di + 1, (yi + 1) * nd + di, (yi + 1) * nd + di + 1], 1)
    weights = np.stack([(1 - dx) * (1 - dy), dx * (1 - dy), (1 - dx) * dy, dx * dy], 1).astype(np.float32)
    nx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3) / 8
    normal_y = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3) / 8
    norm = np.maximum(np.hypot(nx, normal_y), .01)
    nx, normal_y = nx / norm, normal_y / norm
    outer = outer_radius or 18 * ratio
    gate = np.clip((3 * ratio - distance) / (2 * ratio), 0, 1) * np.clip((distance + outer) / (4 * ratio), 0, 1)
    features = np.stack([np.ones(len(d)), (nx * gate)[selected], (normal_y * gate)[selected],
                         ((nx * nx - normal_y * normal_y) * gate)[selected], (2 * nx * normal_y * gate)[selected]], 1).astype(np.float32)
    horizontal_width = dynamic_flowers.advance(text, geometry, str(FONT))
    if horizontal_order:
        xx = np.broadcast_to((np.arange(reference.shape[1])[None, :] - geometry["x"]) / horizontal_width * 2 - 1, distance.shape)[selected]
        xx = np.clip(xx, -1.5, 1.5)
        base_fields = features.copy()
        features = np.concatenate([base_fields * xx[:, None] ** power for power in range(horizontal_order + 1)], axis=1)
    count = features.shape[1]
    weighted = [weights * features[:, feature:feature + 1] for feature in range(count)]
    target = reference[selected].astype(np.float32)

    def forward(coefficients):
        result = np.zeros_like(target)
        for feature in range(count):
            result += (coefficients[feature][indices] * weighted[feature][:, :, None]).sum(axis=1)
        return result

    def transpose(pixels):
        result = np.zeros((count, nd * ny, 3), np.float32)
        for feature in range(count):
            for channel in range(3):
                result[feature, :, channel] = np.bincount(indices.ravel(), (weighted[feature] * pixels[:, channel:channel + 1]).ravel(), minlength=nd * ny)
        return result

    def regularizer(coefficients):
        ridge = np.tile(np.array([.0001, .003, .003, .006, .006], np.float32), count // 5)[:, None, None]
        result = coefficients * ridge
        grid = coefficients.reshape(count, ny, nd, 3)
        second = grid[:, :-2] - 2 * grid[:, 1:-1] + grid[:, 2:]
        smooth = np.zeros_like(grid)
        smooth[:, :-2] += second
        smooth[:, 1:-1] -= 2 * second
        smooth[:, 2:] += second
        return result + .08 * smooth.reshape(coefficients.shape)

    diagonal = np.zeros((count, nd * ny), np.float32)
    for feature in range(count):
        diagonal[feature] = np.bincount(indices.ravel(), (weighted[feature] ** 2).ravel(), minlength=nd * ny)
    diagonal = np.maximum(diagonal + .5, .5)[:, :, None]
    operator = lambda coefficients: transpose(forward(coefficients)) + regularizer(coefficients)
    rhs = transpose(target)
    coefficients = np.zeros((count, ny * nd, 3), np.float32)
    prior = np.asarray(initial["rgb"], np.float32).reshape(-1, ny * nd, 3)
    coefficients[:len(prior)] = prior
    residual = rhs - operator(coefficients)
    z = residual / diagonal
    direction = z.copy()
    rz = (residual * z).sum(axis=(0, 1))
    best_coefficients = coefficients.copy()
    best_score = -1.
    for iteration in range(iterations):
        ad = operator(direction)
        step = rz / np.maximum((direction * ad).sum(axis=(0, 1)), 1e-15)
        coefficients += direction * step
        residual -= ad * step
        z = residual / diagonal
        new_rz = (residual * z).sum(axis=(0, 1))
        direction = z + direction * (new_rz / np.maximum(rz, 1e-15))
        rz = new_rz
        if iteration % 30 == 0:
            print(f"Shader CG {iteration}: residual {np.linalg.norm(residual):.3f}", flush=True)
        if iteration % 20 == 19 or iteration == iterations - 1:
            preview = np.zeros_like(reference)
            preview[selected] = np.clip(np.rint(forward(coefficients)), 0, 255).astype(np.uint8)
            candidate_score = foreground_ssim(reference, preview)
            if candidate_score > best_score:
                best_score, best_coefficients = candidate_score, coefficients.copy()
    coefficients = best_coefficients
    shader = {"geometry": geometry, "distance": dn.tolist(), "height": yn.tolist(), "normals": True, "normal_scale": ratio,
              "order": 2, "horizontal_order": horizontal_order, "horizontal_width": horizontal_width,
              "outer_normal_radius": outer, "color_cutoff": cutoff,
              "rgb": coefficients.reshape(count, ny, nd, 3).round(5).tolist()}
    rendered = shade(distance, shader).astype(np.uint8)
    rendered[distance < -cutoff] = 0
    score = foreground_ssim(reference, rendered)
    print(f"Static calibrated shader: {score:.6f}", flush=True)
    write_json(output / filename, {"schema": "subtitle-animation-static/v1", "geometry": geometry,
              "origin": list(STATIC_CROP[:2]), "canvas": list(reversed(reference.shape[:2])),
              "shader": shader, "score": score, "font_sha256": sha(FONT),
              "method": "supersampled font contours; signed-distance, height and edge-normal color shader"})
    cv2.imwrite(str(output / (Path(filename).stem + "-comparison.png")), cv2.cvtColor(np.hstack([reference, rendered]), cv2.COLOR_RGB2BGR))
    return shader


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    ref = stable_reference(LAB / "references/shout_wave.mp4")
    if (args.output_dir / "geometry.json").exists():
        geometry = read_json(args.output_dir / "geometry.json")["geometry"]
    else:
        geometry = geometry_search(ref, args.output_dir)
    fit_shader(ref, geometry, args.output_dir)
