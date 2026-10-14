"""Shared paths and analysis helpers for the isolated subtitle experiment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import os
import tempfile
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "assets/packaging/animations"
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
import dynamic_flowers

PYTHON = ROOT / "assets/dependencies/python/python.exe"
FFMPEG = ROOT / "assets/dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "assets/dependencies/ffmpeg/bin/ffprobe.exe"
FONT = ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf"
EFFECTS = {"bounce_up": "向上弹入", "shout_wave": "呐喊声波", "ice_drift": "冰雪飘动"}
REFERENCE_TEXT = "无尽冬日"
WIDTH, HEIGHT, FPS, FRAMES = 1920, 3414, 60, 60


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as target:
        temporary = Path(target.name)
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")
    try:
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-4000:])
    return result


def probe(path: Path) -> dict:
    data = json.loads(run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout)
    video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    return {"width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
            "frames": int(video.get("nb_frames", 0)), "codec": video["codec_name"], "pixel_format": video["pix_fmt"],
            "video_duration_seconds": float(video.get("duration", 0.)),
            "audio_streams": sum(s["codec_type"] == "audio" for s in data["streams"])}


def decode(path: Path, crop: tuple[int, int, int, int] | None = None):
    """Stream decoded RGB frames; do not retain full-resolution videos in RAM."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Cannot open video: {path}")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if crop:
                x, y, w, h = crop
                frame = frame[y:y + h, x:x + w]
            yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        capture.release()


def foreground_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Same RGB SSIM as compare_subtitle_flower, with a lossless ROI optimization.

    The crop includes the 2px mask dilation AND the 5px Gaussian radius. No
    geometric alignment, resampling or brightness normalization is performed.
    """
    if a.shape != b.shape:
        raise ValueError("SSIM inputs must have identical dimensions")
    mask = (a.max(axis=2) > 25) | (b.max(axis=2) > 25)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        # The original static-flower metric is undefined for an empty mask.
        # In video, two absent subtitles match. Sub-threshold codec noise stays
        # outside the foreground metric just as it does in nonempty frames.
        return 1.0
    x0, x1 = max(0, int(xs.min()) - 7), min(a.shape[1], int(xs.max()) + 8)
    y0, y1 = max(0, int(ys.min()) - 7), min(a.shape[0], int(ys.max()) + 8)
    a, b = a[y0:y1, x0:x1].astype(np.float64), b[y0:y1, x0:x1].astype(np.float64)
    mask = cv2.dilate(mask[y0:y1, x0:x1].astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    blur = lambda value: cv2.GaussianBlur(value, (11, 11), 1.5)
    ma, mb = blur(a), blur(b)
    va, vb = blur(a * a) - ma * ma, blur(b * b) - mb * mb
    covariance = blur(a * b) - ma * mb
    score = ((2 * ma * mb + 6.5025) * (2 * covariance + 58.5225) /
             ((ma * ma + mb * mb + 6.5025) * (va + vb + 58.5225)))
    return float(score[mask].mean())


def base_spec() -> dict:
    return read_json(ROOT / "assets/packaging/flowers/ice2.json")


def glyph_alpha(text: str, geometry: dict, shape: tuple[int, int], supersample: int = 4) -> np.ndarray:
    """Render supplied text directly at the requested resolution from font outlines."""
    height, width = shape
    image = np.zeros((height * supersample, width * supersample), np.float32)
    scale, shift = dynamic_flowers.font_profile(str(FONT))
    sx = geometry["size"] / 500 * scale * geometry["sx"]
    sy = geometry["size"] / 500 * scale
    x = geometry["x"]
    for char in text:
        pixels, step, left, top = dynamic_flowers.glyph(char, str(FONT))
        transform = np.array([[sx * supersample, 0, (x + left * sx) * supersample],
                              [0, sy * supersample, (geometry["y"] + shift * geometry["size"] / 500 + top * sy) * supersample]], np.float32)
        painted = cv2.warpAffine(pixels.astype(np.float32) / 255, transform,
                                 (width * supersample, height * supersample), flags=cv2.INTER_LINEAR)
        np.maximum(image, painted, out=image)
        x += step * sx + geometry["tracking"]
    return image


def signed_distance(alpha: np.ndarray, supersample: int = 4) -> np.ndarray:
    solid = (alpha >= .5).astype(np.uint8)
    inside = cv2.distanceTransform(solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    outside = cv2.distanceTransform(1 - solid, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distance = (inside - outside + np.where(solid > 0, -.5, .5)) / supersample
    return cv2.resize(distance.astype(np.float32), (alpha.shape[1] // supersample, alpha.shape[0] // supersample), interpolation=cv2.INTER_AREA)


def initial_geometry() -> dict:
    original = base_spec()["shader"]["geometry"]
    ratio = 2.368
    return {"x": original["x"] * ratio - 28, "y": original["y"] * ratio + 14,
            "size": original["size"] * ratio, "sx": original["sx"], "tracking": original["tracking"] * ratio}


def shade(distance: np.ndarray, shader: dict) -> np.ndarray:
    if shader.get("lighting_grid"):
        from animation_shader import shade_light
        return shade_light(distance, shader)
    geometry = shader["geometry"]
    dn, yn = np.asarray(shader["distance"]), np.asarray(shader["height"])
    d = np.minimum(np.interp(distance, dn, np.arange(len(dn))), len(dn) - 1.0001)
    yy = (np.arange(distance.shape[0]) - geometry["y"]) / geometry["size"]
    y = np.broadcast_to(np.minimum(np.interp(yy, yn, np.arange(len(yn))), len(yn) - 1.0001)[:, None], distance.shape)
    if "rgb" in shader:
        tables = np.asarray(shader["rgb"], np.float32)
    else:
        from animation_shader import load_table
        tables = load_table(shader["table_file"], shader["table_sha256"], shader.get("_parameter_dir", str(LAB / "parameters")))
    result = dynamic_flowers.sample_table(tables[0], d, y)
    nx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3) / 8
    ny = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3) / 8
    norm = np.maximum(np.hypot(nx, ny), .01)
    nx, ny = nx / norm, ny / norm
    ratio = shader.get("normal_scale", 1.)
    outer = shader.get("outer_normal_radius", 18 * ratio)
    gate = np.clip((3 * ratio - distance) / (2 * ratio), 0, 1) * np.clip((distance + outer) / (4 * ratio), 0, 1)
    fields = [np.ones(distance.shape, np.float32), nx * gate, ny * gate, (nx * nx - ny * ny) * gate, 2 * nx * ny * gate]
    features = fields[1:]
    if shader.get("horizontal_order", 0):
        xx = np.broadcast_to((np.arange(distance.shape[1])[None, :] - geometry["x"]) / shader["horizontal_width"] * 2 - 1, distance.shape)
        xx = np.clip(xx, -1.5, 1.5)
        for power in range(1, shader["horizontal_order"] + 1):
            features.extend([field * xx ** power for field in fields])
    for table, feature in zip(tables[1:], features):
        result += dynamic_flowers.sample_table(table, d, y) * feature[:, :, None]
    return np.clip(result, 0, 255).astype(np.float32)


def initial_rgb(text: str, geometry: dict, shape=(320, 730), supersample=4) -> np.ndarray:
    spec = base_spec()
    shader = spec["shader"]
    ratio = geometry["size"] / shader["geometry"]["size"]
    shader = {**shader, "geometry": geometry, "distance": (np.array(shader["distance"]) * ratio).tolist()}
    distance = signed_distance(glyph_alpha(text, geometry, shape, supersample), supersample)
    return dynamic_flowers.shade(distance, shader).astype(np.uint8)
