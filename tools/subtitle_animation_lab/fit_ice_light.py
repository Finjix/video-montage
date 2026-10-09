"""Calibrate the contour-attached light sweep and analytic snow particles."""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from common import *
from light_shader import fit_light, freeze, shade_light
from fit_motion import expanded_ice_shader, fit_particles
from renderer import paint_particle


def fit(frames=None, iterations=90, spacing=20):
    static = read_json(LAB / "parameters/static.json")
    prior = expanded_ice_shader(static["shader"], True)
    origin, shape = (560, 2120), (480, 860)
    geometry = {**static["geometry"], "x": static["origin"][0] + static["geometry"]["x"] - origin[0],
                "y": static["origin"][1] + static["geometry"]["y"] - origin[1]}
    path = LAB / "parameters/ice_drift.json"
    result = read_json(path) if path.exists() else {"schema": "subtitle-animation-motion/v1", "effect": "ice_drift",
           "fps": FPS, "frames": [{"visible": False}] * 30, "calibration_scores": [0.] * 30}
    result.update({"model": "contour_light", "seed": 0, "reference_sha256": sha(LAB / "references/ice_drift.mp4"),
                   "method": "input font outlines with distance/height/horizontal-light/normal transfer function; analytic snow particles"})
    analysis = ROOT / "work/subtitle-animation-calibration/motion"
    analysis.mkdir(parents=True, exist_ok=True)
    for n, reference in enumerate(decode(LAB / "references/ice_drift.mp4", (*origin, shape[1], shape[0]))):
        if n >= 30:
            break
        if frames is not None and n not in frames:
            continue
        if n == 0:
            result["frames"][n] = {"visible": False}
            result["calibration_scores"][n] = 1.
            continue
        text = REFERENCE_TEXT[:min(4, n // 7 + 1)]
        drift = float(np.interp(n, [0, 7, 13, 19, 24, 29, 30], [44, 39, 28, 22, 12, 2, 0]))
        if n >= 17:
            # Mature first-glyph contours expose the additional horizontal drift.
            # Search only translation; font geometry is reused, never reference pixels.
            d0 = signed_distance(glyph_alpha("无", geometry, shape))
            source = shade(d0, {**static['shader'], 'geometry': geometry})
            target = reference[130:360, 100:290]
            best = (-1., drift)
            for candidate in np.arange(max(0., drift - 6), drift + 6.1, .5):
                moved = cv2.warpAffine(source, np.array([[1., 0., candidate], [0., 1., 0.]], np.float32), (shape[1], shape[0]))
                score = foreground_ssim(target, np.clip(np.rint(moved[130:360,100:290]),0,255).astype(np.uint8))
                if score > best[0]: best = score,float(candidate)
            drift = best[1]
        frame_geometry = {**geometry, 'x': geometry['x'] + drift}
        print(f'ice measured drift {n:02}: {drift:.3f}px',flush=True)
        shader, distance = fit_light(reference, text, frame_geometry, prior, iterations=iterations, node_spacing=spacing)
        raw = shade_light(distance, shader)
        candidates = fit_particles(reference, raw, distance, frame_geometry)
        emitted = np.zeros_like(raw)
        advance = dynamic_flowers.advance(REFERENCE_TEXT, frame_geometry, str(FONT))
        for particle in candidates:
            paint_particle(emitted, frame_geometry["x"] + particle["u"] * advance, frame_geometry["y"] + particle["dy"], particle["sigma"], particle["rgb"])
        best = None
        for gain in (0., .25, .5, .75, 1.):
            image = np.clip(np.rint(raw + emitted * gain), 0, 255).astype(np.uint8)
            score = foreground_ssim(reference, image)
            if best is None or score > best[0]:
                best = score, gain, image
        score, gain, image = best
        particles = [{**p, "rgb": (np.asarray(p["rgb"]) * gain).tolist()} for p in candidates] if gain else []
        meta = freeze(shader, LAB / "parameters", f"ice-light-{n:02}.npz")
        result["frames"][n] = {"visible": True, "geometry": frame_geometry, "drift_x": drift, "shader": meta, "particles": particles}
        result["calibration_scores"][n] = score
        result["calibration_entry_mean"] = float(np.mean(result["calibration_scores"]))
        write_json(path, result)
        cv2.imwrite(str(analysis / f"ice-light-{n:02}.png"), cv2.cvtColor(np.hstack([reference, image]), cv2.COLOR_RGB2BGR))
        print(f"ice light {n:02}: {score:.6f} particles={len(particles)} gain={gain}", flush=True)
    print('entry_mean',result['calibration_entry_mean'],flush=True)
    return result


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--frames')
    parser.add_argument('--iterations',type=int,default=90)
    parser.add_argument('--spacing',type=float,default=20)
    args=parser.parse_args()
    cv2.setNumThreads(2)
    fit(set(map(int,args.frames.split(','))) if args.frames else None,args.iterations,args.spacing)
