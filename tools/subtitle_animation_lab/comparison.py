"""Measure decoded video foreground, and export synchronized visual evidence."""
from __future__ import annotations

from pathlib import Path
import csv
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import LAB, FONT, FFMPEG, WIDTH, HEIGHT, FPS, FRAMES, decode, probe, sha, write_json, foreground_ssim, run


def compare(reference: Path, rendered: Path, output_dir: Path, visuals=True) -> dict:
    ref_spec, spec = probe(reference), probe(rendered)
    if ref_spec["fps"] != "60/1" or spec["fps"] != "60/1" or ref_spec["frames"] != FRAMES or spec["frames"] != FRAMES:
        raise ValueError("Comparison requires exactly 60 decoded frames at 60 fps in both clips")
    if (ref_spec["width"], ref_spec["height"]) != (WIDTH, HEIGHT):
        raise ValueError("Reference must be 1920x3414")
    if (spec["width"], spec["height"]) not in ((WIDTH, HEIGHT), (1440, 2560)):
        raise ValueError("Rendered clip must be 1920x3414 or 1440x2560")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    chosen = set([0, 1, 4, 5, 7, 10, 13, 16, 20, 24, 29, 40, 59])
    panels = []
    samples = {}
    for n, (ref, got) in enumerate(zip(decode(reference), decode(rendered))):
        if ref.shape != got.shape:
            ref = cv2.resize(ref, (spec["width"], spec["height"]), interpolation=cv2.INTER_AREA)
        score = foreground_ssim(ref, got)
        foreground = (ref.max(axis=2) > 25) | (got.max(axis=2) > 25)
        ys, xs = np.nonzero(foreground)
        box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
        rows.append({"frame": n, "time_seconds": n / FPS, "phase": "entry" if n < 30 else "stable",
                     "ssim": score, "foreground_bbox": box})
        if visuals:
            left = cv2.resize(ref, (540, 960), interpolation=cv2.INTER_AREA)
            right = cv2.resize(got, (540, 960), interpolation=cv2.INTER_AREA)
            panel = np.hstack([left, right])
            label = Image.fromarray(panel)
            draw = ImageDraw.Draw(label)
            face = ImageFont.truetype(str(FONT), 23)
            draw.text((12, 16), "参考", font=face, fill="white")
            draw.text((552, 16), "复刻", font=face, fill="white")
            draw.text((12, 925), f"{n:02d}/59  {n / FPS:.3f}s    SSIM {score:.4f}", font=face, fill="white")
            panels.append(np.asarray(label))
            if n in chosen:
                samples[n] = detail_panel(ref, got, box, n, score)
    if len(rows) != FRAMES:
        raise ValueError(f"Decoder returned {len(rows)} frames, expected 60")
    entry = float(np.mean([row["ssim"] for row in rows[:30]]))
    stable = float(np.mean([row["ssim"] for row in rows[30:]]))
    worst = sorted(rows, key=lambda row: row["ssim"])[:5]
    report = {"schema": "subtitle-animation-comparison/v1", "metric": "RGB foreground SSIM; Gaussian 11x11 sigma=1.5; foreground RGB max>25; union dilated 2px",
              "alignment": "same frame index and canvas coordinates; no framewise spatial, luminance or temporal alignment",
              "reference_resampling": "none" if spec["width"] == WIDTH else "one fixed resize of every reference frame to 1440x2560 using INTER_AREA",
              "empty_foreground_policy": "score 1 when neither clip has any RGB channel >25; no background pixels are averaged",
              "entry_mean": entry, "stable_mean": stable, "threshold": .95,
              "quantitative_pass": entry >= .95 and stable >= .95, "minimum": worst[0], "worst_frames": worst,
              "visual_review": "pending", "reference": {"path": str(reference.resolve()), "sha256": sha(reference), "spec": ref_spec},
              "rendered": {"path": str(rendered.resolve()), "sha256": sha(rendered), "spec": spec}, "frames": rows}
    write_json(output_dir / "comparison.json", report)
    with (output_dir / "frames.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["frame", "time_seconds", "phase", "ssim"])
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in rows)
    if visuals:
        # Regenerate the five worst frames with identical shared crops.
        numbers = {row["frame"] for row in worst}
        for n, (ref, got) in enumerate(zip(decode(reference), decode(rendered))):
            if n not in numbers:
                continue
            if ref.shape != got.shape:
                ref = cv2.resize(ref, (spec["width"], spec["height"]), interpolation=cv2.INTER_AREA)
            samples[n] = detail_panel(ref, got, rows[n]["foreground_bbox"], n, rows[n]["ssim"])
        key_dir = output_dir / "关键帧"
        key_dir.mkdir(exist_ok=True)
        for n, panel in sorted(samples.items()):
            panel.save(key_dir / f"frame-{n:02}.png")
        thumbs = []
        for n, panel in sorted(samples.items()):
            panel = panel.copy()
            panel.thumbnail((1080, 310))
            tile = Image.new("RGB", (1080, 330), "#151515")
            tile.paste(panel, ((1080 - panel.width) // 2, 0))
            thumbs.append(tile)
        sheet = Image.new("RGB", (2160, ((len(thumbs) + 1) // 2) * 330), "#151515")
        for i, tile in enumerate(thumbs):
            sheet.paste(tile, (i % 2 * 1080, i // 2 * 330))
        sheet.save(output_dir / "关键帧对照.jpg", quality=94)
        encode_panels(panels, output_dir / "同步对照.mp4")
        run([str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(output_dir / "同步对照.mp4"),
             "-vf", "setpts=4*PTS", "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(output_dir / "同步对照_四倍慢放.mp4")])
    return report


def detail_panel(ref, got, box, n, score) -> Image.Image:
    if box is None:
        x0, y0, x1, y1 = round(ref.shape[1] * .3), round(ref.shape[0] * .65), round(ref.shape[1] * .7), round(ref.shape[0] * .75)
    else:
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0 - 24), max(0, y0 - 24)
        x1, y1 = min(ref.shape[1], x1 + 24), min(ref.shape[0], y1 + 24)
    a, b = ref[y0:y1, x0:x1], got[y0:y1, x0:x1]
    diff = np.clip(np.abs(a.astype(float) - b.astype(float)) * 4, 0, 255).astype(np.uint8)
    image = Image.fromarray(np.hstack([a, b, diff]))
    panel = Image.new("RGB", (image.width, image.height + 50), "#161616")
    panel.paste(image, (0, 50))
    draw = ImageDraw.Draw(panel)
    face = ImageFont.truetype(str(FONT), 20)
    draw.text((8, 8), f"帧{n:02} / {n / FPS:.3f}s / SSIM {score:.4f}    参考 | 复刻 | 差异×4", font=face, fill="white")
    return panel


def encode_panels(panels, target: Path) -> None:
    height, width = panels[0].shape[:2]
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", "60", "-i", "pipe:0", "-an", "-c:v", "libx264",
               "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for panel in panels:
            process.stdin.write(panel.tobytes())
        process.stdin.close()
        error = process.stderr.read()
        if process.wait() != 0:
            raise RuntimeError(error.decode("utf-8", errors="replace"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stderr.close()
