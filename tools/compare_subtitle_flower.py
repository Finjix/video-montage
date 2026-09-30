"""Reproduce the W8 flower comparison using the actual subtitle renderer."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/packaging/scripts/package_video.py"


def foreground_ssim(reference: np.ndarray, rendered: np.ndarray) -> float:
    """Standard RGB SSIM, averaged over the foreground union plus a 2px edge."""
    a, b = reference.astype(np.float64), rendered.astype(np.float64)
    blur = lambda value: cv2.GaussianBlur(value, (11, 11), 1.5)
    ma, mb = blur(a), blur(b)
    va, vb, covariance = blur(a * a) - ma * ma, blur(b * b) - mb * mb, blur(a * b) - ma * mb
    score = ((2 * ma * mb + 6.5025) * (2 * covariance + 58.5225) /
             ((ma * ma + mb * mb + 6.5025) * (va + vb + 58.5225)))
    foreground = np.any(a > 25, axis=2) | np.any(b > 25, axis=2)
    foreground = cv2.dilate(foreground.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return float(score[foreground].mean())


def compare(reference_path: Path, output_dir: Path) -> dict:
    spec = importlib.util.spec_from_file_location("packaging_comparison", SCRIPT)
    packaging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaging)
    reference = cv2.imread(str(reference_path))
    if reference is None or reference.shape != (115, 264, 3):
        raise ValueError("expected the supplied 264x115 reference image")
    output_dir.mkdir(parents=True, exist_ok=True)
    style = packaging.subtitle_style({}, ROOT)
    geometry = style["emphasis"]["reference_geometry"]
    recorded_style = json.loads(json.dumps(style))
    # Uniform size and translation align the screenshot. No image warping or reference pixels are used.
    style["ass"].update(play_res_x=264, play_res_y=115, position_x=geometry["x"], position_y=geometry["y"],
                        font_size=style["ass"]["font_size"] / style["emphasis"]["ass_font_size"] * geometry["size"])
    style["emphasis"]["ass_font_size"] = geometry["size"]
    with TemporaryDirectory(prefix="flower-comparison-") as temp:
        work = Path(temp)
        (work / "fonts").mkdir()
        shutil.copyfile(recorded_style["font"]["path"], work / "fonts/font.otf")
        packaging.write_ass(work / "sample.ass", [dict(start_ms=0, end_ms=1000, text="无尽冬日")], style,
                            render_width=1056)
        command = [str(packaging.FFMPEG), "-hide_banner", "-loglevel", "verbose", "-y", "-f", "lavfi",
                   "-i", "color=black:s=1056x460:d=0.04", "-vf",
                   "ass=sample.ass:fontsdir=fonts,scale=264:115:flags=area", "-frames:v", "1", "sample.png"]
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        packaging.require_subtitle_font(result.stderr)
        rendered = cv2.imread(str(work / "sample.png"))
        shutil.copyfile(work / "sample.png", output_dir / "rendered.png")
    shutil.copyfile(reference_path, output_dir / "reference.png")
    score = foreground_ssim(reference, rendered)
    report = {
        "metric": "RGB SSIM; 11x11 Gaussian window, sigma 1.5; mean over union foreground dilated 2px",
        "alignment": "uniform font-size scale and translation to reference; no nonuniform warp",
        "score": score, "threshold": .9, "pass": score >= .9,
        "reference_sha256": packaging.sha(reference_path), "renderer_sha256": packaging.sha(SCRIPT),
        "subtitle_style": recorded_style,
    }
    (output_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    side_by_side = cv2.resize(np.hstack([reference, rendered]), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(output_dir / "comparison.png"), side_by_side)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = compare(args.reference.resolve(), args.output_dir.resolve())
    print(json.dumps({key: report[key] for key in ("metric", "score", "threshold", "pass")}))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
