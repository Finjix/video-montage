"""Fit reusable transforms and convolution kernels to the three reference clips."""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np

from common import *
from renderer import text_sprite, vertical_blur, horizontal_blur, place, transform, Animation, paint_particle


def fit_bounce(parameter_dir: Path, analysis_dir: Path) -> dict:
    sprite = text_sprite(REFERENCE_TEXT, str(parameter_dir.resolve()))
    base = sprite.rgb
    nonzero = np.where(base.max(axis=2) > 25)[0]
    base_center = (int(nonzero.min()) + int(nonzero.max())) / 2
    radius, length, center = 128, 1024, 352
    data = []
    scores = []
    reference = LAB / "references/bounce_up.mp4"
    for n, ref in enumerate(decode(reference, (590, 0, 730, HEIGHT))):
        if n >= 30:
            break
        ys = np.where(ref.max(axis=2) > 25)[0]
        if not len(ys):
            data.append({"visible": False})
            scores.append(1.)
            continue
        origin_y = round((int(ys.min()) + int(ys.max())) / 2 - base_center)
        crop_y = origin_y - center
        target = np.zeros((length, 730, 3), np.float32)
        y0, y1 = max(0, crop_y), min(HEIGHT, crop_y + length)
        target[y0 - crop_y:y1 - crop_y] = ref[y0:y1]
        valid = np.zeros(length, bool)
        valid[y0 - crop_y:y1 - crop_y] = True
        best = None
        for sigma_x in (0., .4, .8, 1.2):
            source = np.zeros_like(target)
            blurred = cv2.GaussianBlur(base, (0, 1), sigma_x, borderType=cv2.BORDER_CONSTANT) if sigma_x else base
            source[center:center + base.shape[0]] = blurred
            sf, tf = np.fft.rfft(source, axis=0), np.fft.rfft(target, axis=0)
            power = (np.abs(sf) ** 2).sum(axis=1)
            cross = (sf.conj() * tf).sum(axis=1)
            for regularization in (1e-6, 1e-5, 1e-4, .001):
                response = cross / (power + power.max(axis=0) * regularization)
                impulse = np.fft.irfft(response, n=length, axis=0)
                kernel = np.concatenate([impulse[-radius:], impulse[:radius + 1]], axis=0).T
                pixels, pad = vertical_blur(base, kernel.tolist(), sigma_x)
                canvas = np.zeros_like(target)
                place(canvas, pixels, 0., center - pad)
                canvas[~valid] = 0
                score = foreground_ssim(target.astype(np.uint8), np.clip(np.rint(canvas), 0, 255).astype(np.uint8))
                if best is None or score > best[0]:
                    best = (score, kernel, sigma_x, canvas)
        score, kernel, sigma_x, canvas = best
        print(f"bounce_up {n:02}: {score:.6f} y={origin_y} sigma_x={sigma_x}", flush=True)
        data.append({"visible": True, "dy": origin_y - sprite.y, "sigma_x": sigma_x,
                     "kernels": kernel.round(7).tolist()})
        scores.append(score)
        if n in (4, 5, 7, 10, 13, 18, 24, 29):
            analysis_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(analysis_dir / f"bounce-{n:02}.png"), cv2.cvtColor(np.hstack([target, canvas]).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    result = {"schema": "subtitle-animation-motion/v1", "effect": "bounce_up", "fps": FPS,
              "frames": data, "calibration_scores": scores, "calibration_entry_mean": float(np.mean(scores)),
              "reference_sha256": sha(reference), "method": "vertical position keyframes and per-channel vertical motion PSF"}
    write_json(parameter_dir / "bounce_up.json", result)
    return result


def nonnegative_least_squares(gram: np.ndarray, rhs: np.ndarray, iterations=160) -> np.ndarray:
    """Small active coefficient fit, without an additional runtime dependency."""
    value = np.zeros_like(rhs)
    diagonal = np.maximum(np.diag(gram), 1e-9)
    for _ in range(iterations):
        old = value.copy()
        for j in range(len(value)):
            value[j] = max(0., value[j] + (rhs[j] - gram[j] @ value) / diagonal[j])
        if np.max(np.abs(value - old)) < 1e-6:
            break
    return value




def fit_shout(parameter_dir: Path, analysis_dir: Path) -> dict:
    sprite = text_sprite(REFERENCE_TEXT, str(parameter_dir.resolve()))
    origin, shape = (420, 2130), (460, 1080)
    data, scores = [], []
    reference = LAB / "references/shout_wave.mp4"
    for n, ref in enumerate(decode(reference, (*origin, shape[1], shape[0]))):
        if n >= 30:
            break
        if n < 10:
            _, xs = np.where(ref.max(axis=2) > 25)
            center = (xs.max() - xs.min() + 1) / 572
        else:
            center = [1.1, 1.08, 1.07, 1.06, 1.04, 1.02, 1., 1., 1., 1., 1., 1., 1., 1., 1., 1., 1., 1., 1., 1.][n - 10]
        if n >= 28:
            scales = np.array([1.])
        else:
            fine = np.arange(center - .055, center + .056, .002)
            coarse = np.arange(max(center + .05, 1.05), 1.801, .0125) if n >= 9 else []
            scales = np.unique(np.round(np.r_[fine, coarse], 7))
        prototypes = np.stack([transform(sprite.rgb, sprite, float(scale), shape, origin) for scale in scales])
        target = ref.astype(np.float32)
        coefficients = np.zeros((len(scales), 3), np.float32)
        for channel in range(3):
            matrix = prototypes[:, ::2, ::2, channel].reshape(len(scales), -1).copy()
            wanted = target[::2, ::2, channel].ravel()
            local = cv2.GaussianBlur(target[:, :, channel], (11, 11), 1.5)[::2, ::2].ravel()
            weighting = 1 / np.sqrt(np.maximum(local, 20.))
            matrix *= weighting
            wanted = wanted * weighting
            gram = matrix @ matrix.T
            gram.flat[::len(gram) + 1] += .1
            rhs = matrix @ wanted
            coefficients[:, channel] = nonnegative_least_squares(gram, rhs, 400)
        generated = (prototypes * coefficients[:, None, None, :]).sum(axis=0)
        score = foreground_ssim(ref, np.clip(np.rint(generated), 0, 255).astype(np.uint8))
        print(f"shout_wave zoomPSF {n:02}: {score:.6f}", flush=True)
        data.append({"model": "zoom_psf", "layers": [{"scale": float(s), "weight": w.round(7).tolist()}
                     for s, w in zip(scales, coefficients) if w.max() > 1e-5]})
        scores.append(score)
        if n in (0, 5, 9, 12, 16, 20, 24, 29):
            analysis_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(analysis_dir / f"shout-{n:02}.png"), cv2.cvtColor(np.hstack([ref, generated]).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    result = {"schema": "subtitle-animation-motion/v1", "effect": "shout_wave", "fps": FPS,
              "frames": data, "calibration_scores": scores, "calibration_entry_mean": float(np.mean(scores)),
              "reference_sha256": sha(reference), "method": "nonnegative chromatic zoom point-spread function; text-dependent transforms"}
    write_json(parameter_dir / "shout_wave.json", result)
    return result


def fit_shout_surfaces(parameter_dir: Path, analysis_dir: Path, selected_frames=None) -> dict:
    from calibrate import fit_shader
    static = read_json(parameter_dir / "static.json")
    sprite = text_sprite(REFERENCE_TEXT, str(parameter_dir.resolve()))
    parameters = read_json(parameter_dir / "shout_wave.json")
    origin, shape = (420, 2130), (460, 1080)
    reference = LAB / "references/shout_wave.mp4"
    for n, ref in enumerate(decode(reference, (*origin, shape[1], shape[0]))):
        if n >= 16:
            break
        if selected_frames is not None and n not in selected_frames:
            continue
        item = parameters["frames"][n]
        # The bright body has a measured scale; the distant echo stays a zoom PSF.
        layers = item.get("layers", [])
        near = [layer for layer in layers if layer["scale"] < 1.2]
        if item.get("geometry"):
            scale = item["geometry"]["size"] / static["geometry"]["size"]
        elif item.get("main_scale"):
            scale = item["main_scale"]
        else:
            scale = sum(layer["scale"] * max(layer["weight"]) for layer in near) / max(1e-8, sum(max(layer["weight"]) for layer in near))
        distant = [layer for layer in layers if layer["scale"] > scale + .055]
        echo = np.zeros_like(ref, np.float32)
        for layer in distant:
            echo += transform(sprite.rgb, sprite, layer["scale"], shape, origin) * np.array(layer["weight"], np.float32)
        g = static["geometry"]
        geometry = {"x": (sprite.x + g["x"] - 960) * scale + 960 - origin[0],
                    "y": (sprite.y + g["y"] - 2358) * scale + 2358 - origin[1],
                    "size": g["size"] * scale, "sx": g["sx"], "tracking": g["tracking"] * scale}
        wanted = np.clip(ref.astype(np.float32) - echo, 0, 255).astype(np.uint8)
        shader = fit_shader(wanted, geometry, analysis_dir, prior_shader=static["shader"], iterations=140,
                            filename=f"shout-surface-{n:02}.json", horizontal_order=3)
        distance = signed_distance(glyph_alpha(REFERENCE_TEXT, geometry, shape))
        image = shade(distance, shader)
        image[distance < -19 * shader["normal_scale"]] = 0
        generated = np.clip(np.rint(image + echo), 0, 255).astype(np.uint8)
        score = foreground_ssim(ref, generated)
        print(f"shout_wave surface {n:02}: {score:.6f} scale {scale:.5f}", flush=True)
        if score > parameters["calibration_scores"][n]:
            parameters["frames"][n] = {"model": "surface", "geometry": geometry, "shader": shader, "layers": distant}
            parameters["calibration_scores"][n] = score
        parameters["calibration_entry_mean"] = float(np.mean(parameters["calibration_scores"]))
        write_json(parameter_dir / "shout_wave.json", parameters)
    return parameters


def fit_shout_horizontal(parameter_dir: Path, analysis_dir: Path) -> dict:
    parameters = read_json(parameter_dir / "shout_wave.json")
    sprite = text_sprite(REFERENCE_TEXT, str(parameter_dir.resolve()))
    animation = Animation("shout_wave", REFERENCE_TEXT, parameter_dir)
    origin, shape, radius = (420, 2130), (460, 1080), 64
    for n, ref in enumerate(decode(LAB / "references/shout_wave.mp4", (*origin, shape[1], shape[0]))):
        if n >= 28:
            break
        old = parameters["frames"][n]
        sources = []
        image, x, y = animation.shout_frame(n)
        source = np.zeros((*shape, 3), np.float32)
        place(source, image, x - origin[0], y - origin[1])
        sources.append((source, old))
        if n < 10:
            if old.get("geometry"):
                scale = old["geometry"]["size"] / read_json(parameter_dir / "static.json")["geometry"]["size"]
            else:
                scale = sum(layer["scale"] * max(layer["weight"]) for layer in old["layers"]) / sum(max(layer["weight"]) for layer in old["layers"])
            for candidate in np.arange(scale - .009, scale + .0091, .003):
                image = transform(sprite.rgb, sprite, float(candidate), shape, origin)
                sources.append((image, {"main_scale": float(candidate), "main_gain": [1., 1., 1.], "layers": []}))
        target = ref.astype(np.float32)
        tf = np.fft.rfft(target, axis=1)
        best = (parameters["calibration_scores"][n], None, old, source)
        for source, item in sources:
            sf = np.fft.rfft(source, axis=1)
            power = (np.abs(sf) ** 2).sum(axis=0)
            cross = (sf.conj() * tf).sum(axis=0)
            for regularization in (1e-6, 1e-5, 1e-4, .001):
                impulse = np.fft.irfft(cross / (power + power.max(axis=0) * regularization), n=shape[1], axis=0)
                kernel = np.concatenate([impulse[-radius:], impulse[:radius + 1]], axis=0).T
                pixels = horizontal_blur(source, kernel.tolist())
                score = foreground_ssim(ref, np.clip(np.rint(pixels), 0, 255).astype(np.uint8))
                if score > best[0]:
                    best = (score, kernel, item, pixels)
        score, kernel, item, pixels = best
        if kernel is not None:
            parameters["frames"][n] = {**item, "post_kernels": kernel.round(7).tolist()}
            parameters["calibration_scores"][n] = score
        print(f"shout horizontal {n:02}: {score:.6f}", flush=True)
        cv2.imwrite(str(analysis_dir / f"shout-horizontal-{n:02}.png"), cv2.cvtColor(np.hstack([ref, pixels]).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        parameters["calibration_entry_mean"] = float(np.mean(parameters["calibration_scores"]))
        write_json(parameter_dir / "shout_wave.json", parameters)
    return parameters


def expanded_ice_shader(shader: dict, dense=False) -> dict:
    old_dn, old_yn = np.array(shader["distance"]), np.array(shader["height"])
    dn = np.unique(np.r_[-140., -120., -100., -85., -75., -65., -55., -48., old_dn])
    yn = np.array([-.8, -.4, -.2, -.12, 0., .05, .1, .15, .2, .25, .3, .4, .5, .6, .7, .8, .9, 1., 1.1, 1.2, 1.35, 1.6, 1.9])
    if dense:
        yn = np.unique(np.round(np.r_[[-.8, -.6, -.4, -.25], np.arange(-.12, 1.2401, .02), [1.35, 1.6, 1.9]], 5))
    prior = np.array(shader["rgb"])
    tables = np.zeros((len(prior), len(yn), len(dn), 3), np.float32)
    for feature in range(len(prior)):
        for channel in range(3):
            extended = np.stack([np.interp(dn, old_dn, row) for row in prior[feature, :, :, channel]])
            tables[feature, :, :, channel] = np.stack([np.interp(yn, old_yn, extended[:, d]) for d in range(len(dn))], 1)
    return {**shader, "distance": dn.tolist(), "height": yn.tolist(), "rgb": tables.tolist()}


def fit_particles(reference: np.ndarray, generated: np.ndarray, distance: np.ndarray, geometry: dict) -> list[dict]:
    residual = np.maximum(reference.astype(np.float32) - generated, 0)
    bright = residual.max(axis=2)
    detail = bright - cv2.GaussianBlur(bright, (0, 0), 2.)
    peaks = (detail > 8.) & (bright > 16.) & (bright >= cv2.dilate(bright, np.ones((5, 5), np.uint8))) & (distance < -10)
    ys, xs = np.where(peaks)
    order = np.argsort(bright[ys, xs])[::-1]
    word_advance = dynamic_flowers.advance(REFERENCE_TEXT, geometry, str(FONT))
    particles = []
    for index in order[:280]:
        x, y = int(xs[index]), int(ys[index])
        if any((p["x"] - x) ** 2 + (p["y"] - y) ** 2 < 9 for p in particles):
            continue
        x0, x1 = max(0, x - 5), min(reference.shape[1], x + 6)
        y0, y1 = max(0, y - 5), min(reference.shape[0], y + 6)
        target = residual[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        best = None
        for sigma in (.6, .85, 1.1, 1.4, 1.8, 2.3):
            gaussian = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
            color = (target * gaussian[:, :, None]).sum(axis=(0, 1)) / max(1e-8, (gaussian * gaussian).sum())
            error = np.mean((target - gaussian[:, :, None] * color) ** 2)
            if best is None or error < best[0]:
                best = (error, sigma, color)
        _, sigma, color = best
        particles.append({"x": x, "y": y, "u": (x - geometry["x"]) / word_advance,
                          "dy": y - geometry["y"], "sigma": sigma, "rgb": color.round(5).tolist()})
    return [{key: value for key, value in p.items() if key not in ("x", "y")} for p in particles]




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--effect", choices=["bounce_up", "shout_wave"], required=True)
    parser.add_argument("--parameter-dir", type=Path, default=LAB / "parameters")
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "work/subtitle-animation-calibration/motion")
    parser.add_argument("--surface-refine", action="store_true")
    parser.add_argument("--horizontal-refine", action="store_true")
    parser.add_argument("--frames", help="Optional comma-separated calibration frame indices")
    args = parser.parse_args()
    cv2.setNumThreads(2)
    if args.effect == "bounce_up":
        fit_bounce(args.parameter_dir, args.analysis_dir)
    elif args.effect == "shout_wave":
        if args.horizontal_refine:
            fit_shout_horizontal(args.parameter_dir, args.analysis_dir)
        elif args.surface_refine:
            fit_shout_surfaces(args.parameter_dir, args.analysis_dir, set(map(int, args.frames.split(','))) if args.frames else None)
        else:
            fit_shout(args.parameter_dir, args.analysis_dir)
