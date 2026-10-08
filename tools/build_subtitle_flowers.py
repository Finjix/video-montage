"""Calibrate reusable font shaders. SciPy is a development-only dependency."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets/packaging/flowers"
sys.path[:0] = [str(ROOT / "scripts/packaging/scripts"), str(ROOT / "tools")]
import dynamic_flowers
from compare_subtitle_flower import foreground_ssim


def calibrate(reference: np.ndarray, text: str, spec: dict, refit: bool) -> dict:
    from scipy.optimize import minimize
    from scipy.sparse import coo_matrix, hstack, block_diag
    from scipy.sparse.linalg import spsolve
    font = str(ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf")
    initial = spec["shader"]
    dn, yn = np.asarray(initial["distance"]), np.asarray(initial["height"])
    nd, ny = len(dn), len(yn)
    count = 5 if initial["normals"] and initial.get("order", 1) == 2 else 3 if initial["normals"] else 1
    grid = np.arange(nd * ny).reshape(ny, nd)
    rows, cols, values = [], [], []
    row = 0
    for y in range(1, ny - 1):
        for d in range(nd):
            for dy, value in [(-1, 1), (0, -2), (1, 1)]:
                rows.append(row); cols.append(grid[y + dy, d]); values.append(value)
            row += 1
    regularizer = coo_matrix((values, (rows, cols)), shape=(row, nd * ny)).tocsr()
    smooth = block_diag([regularizer.T @ regularizer] * count)
    ridge = np.r_[np.full(nd * ny, .0001), np.full(nd * ny * (count - 1), .005)]
    ridge = coo_matrix((ridge, (np.arange(ridge.size), np.arange(ridge.size))))
    shape = reference.shape[:2]

    def fit(geometry):
        alpha = dynamic_flowers.font_mask(text, geometry, shape, font)
        distance = cv2.resize(dynamic_flowers.signed_distance(alpha), (shape[1], shape[0]), interpolation=cv2.INTER_AREA)
        height = np.broadcast_to((np.arange(shape[0])[:, None] - geometry["y"]) / geometry["size"], shape)
        x = np.minimum(np.interp(distance.ravel(), dn, np.arange(nd)), nd - 1.0001)
        y = np.minimum(np.interp(height.ravel(), yn, np.arange(ny)), ny - 1.0001)
        xi, yi = np.floor(x).astype(int), np.floor(y).astype(int)
        dx, dy = x - xi, y - yi
        indices = np.stack([yi * nd + xi, yi * nd + xi + 1, (yi + 1) * nd + xi, (yi + 1) * nd + xi + 1], 1).ravel()
        weights = np.stack([(1 - dx) * (1 - dy), dx * (1 - dy), (1 - dx) * dy, dx * dy], 1).ravel()
        matrix = coo_matrix((weights, (np.repeat(np.arange(x.size), 4), indices)), shape=(x.size, nd * ny)).tocsr()
        if initial["normals"]:
            nx = cv2.Sobel(distance, cv2.CV_64F, 1, 0, ksize=3) / 8
            normal_y = cv2.Sobel(distance, cv2.CV_64F, 0, 1, ksize=3) / 8
            length = np.maximum(np.hypot(nx, normal_y), .01)
            nx, normal_y = nx / length, normal_y / length
            gate = np.clip((3 - distance) / 2, 0, 1) * np.clip((distance + 18) / 4, 0, 1)
            features = [nx * gate, normal_y * gate]
            if initial.get("order", 1) == 2:
                features += [(nx * nx - normal_y * normal_y) * gate, 2 * nx * normal_y * gate]
            matrix = hstack([matrix] + [matrix.multiply(feature.ravel()[:, None]) for feature in features]).tocsr()
        coefficients = spsolve((matrix.T @ matrix + .3 * smooth + ridge).tocsc(), matrix.T @ reference.reshape(-1, 3))
        rendered = np.clip(matrix @ coefficients, 0, 255).reshape(*shape, 3)
        shader = {"geometry": geometry, "distance": dn.tolist(), "height": yn.tolist(), "normals": initial["normals"],
                  "order": initial.get("order", 1), "rgb": coefficients.reshape(count, ny, nd, 3).round(4).tolist()}
        return foreground_ssim(reference, rendered), shader

    geometry = {key: initial["geometry"][key] for key in ("x", "y", "size", "tracking", "sx")}
    best_score, best_shader = fit(geometry)
    if refit:
        names, start = list(geometry), list(geometry.values())
        bounds = [(start[0] - 2, start[0] + 2), (start[1] - 2, start[1] + 2), (start[2] - 2, start[2] + 2), (-1, 1), (.98, 1.02)]
        def objective(values):
            nonlocal best_score, best_shader
            score, shader = fit(dict(zip(names, map(float, values))))
            if score > best_score:
                best_score, best_shader = score, shader
            return 1 - score
        minimize(objective, start, method="Powell", bounds=bounds, options={"maxiter": 5, "xtol": .02, "ftol": .0001})
    if best_score < .95:
        raise ValueError(f"procedural shader similarity {best_score:.6f} is below .95")
    return best_shader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for style_id in ("fire1", "ice1", "ice2"):
        parser.add_argument(f"--{style_id}", type=Path)
    parser.add_argument("--text", default="无尽冬日", help="text shown in calibration references")
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    parser.add_argument("--optimizer-path", type=Path, help="optional local SciPy directory")
    parser.add_argument("--refit-geometry", action="store_true")
    args = parser.parse_args()
    if args.optimizer_path:
        sys.path.insert(0, str(args.optimizer_path.resolve()))
    try:
        import scipy
    except ImportError:
        parser.error("calibration requires SciPy; production rendering does not. Supply --optimizer-path.")
    cv2.setNumThreads(1)
    manifest = json.loads((ASSETS / "styles.json").read_text(encoding="utf-8"))
    staged = []
    for style_id, recorded in manifest["styles"].items():
        source = getattr(args, style_id) or ASSETS / recorded["reference"]
        reference = np.asarray(Image.open(source).convert("RGB"))
        spec = json.loads((ASSETS / recorded["path"]).read_text(encoding="utf-8"))
        if list(reversed(reference.shape[:2])) != spec["canvas"]:
            parser.error(f"{style_id} reference dimensions differ from calibration")
        spec["shader"] = calibrate(reference, args.text, spec, args.refit_geometry)
        geometry = spec["shader"]["geometry"]
        font = str(ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf")
        spec["right_padding"] = reference.shape[1] - geometry["x"] - dynamic_flowers.advance(args.text, geometry, font)
        spec["face_bbox"] = list(dynamic_flowers.ink_bounds(dynamic_flowers.font_mask(args.text, geometry, reference.shape[:2], font)))
        payload = (json.dumps(spec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        recorded["sha256"] = hashlib.sha256(payload).hexdigest()
        recorded["face_bbox"] = spec["face_bbox"]
        recorded["reference_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        staged.append((recorded, payload, source))
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "references").mkdir(exist_ok=True)
    for recorded, payload, source in staged:
        (destination / recorded["path"]).write_bytes(payload)
        copy = destination / recorded["reference"]
        if source.resolve() != copy.resolve():
            shutil.copyfile(source, copy)
    (destination / "styles.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(destination))


if __name__ == "__main__":
    main()
