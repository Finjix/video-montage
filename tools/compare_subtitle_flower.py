"""Measure procedural flower text against references and actual burned video."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/packaging/scripts/package_video.py"
sys.path.insert(0, str(SCRIPT.parent))
import flower_effects


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


def video_roundtrip(packaging, reference: np.ndarray, directory: Path, style_id: str) -> tuple[float, dict]:
    """Exercise the complete 1440x2560 H.264 burn-in, then restore reference size."""
    source = directory / "source.mp4"
    packaging.run([str(packaging.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                   "color=black:s=1440x2560:r=60:d=1", "-f", "lavfi", "-i",
                   "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264", "-preset", "ultrafast",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
    subtitles = directory / "subtitle.txt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,900\n无尽冬日\n", encoding="utf-8")
    config = directory / "config.json"
    from PIL import ImageFont, Image
    size = packaging.subtitle_fonts.pixels("w8", 9)
    packaging.atomic(config, {"schema": packaging.SCHEMA, "outputs": [{"plan_id": "comparison",
        "input_path": str(source), "subtitle_txt": str(subtitles),
        "subtitle_design": {"font": "w8", "subtitle_sha256": packaging.sha(subtitles), "game_names": ["无尽冬日"],
            "game_flower": style_id, "reason": "Static reference comparison",
            "cues": [{"index": 1, "text": "无尽冬日", "font_size": 9}]}}]})
    result = packaging.render_one(packaging.prepared_rows(config)[0], directory / "video")
    x, y, right, bottom = result["design"]["events"][0]["bounds"]
    width, height = right - x, bottom - y
    frame = directory / "decoded.png"
    packaging.run([str(packaging.FFMPEG), "-v", "error", "-y", "-i", result["output_path"],
                   "-vf", f"select=eq(n\\,20),format=rgb24,crop={width}:{height}:{x}:{y}",
                   "-frames:v", "1", str(frame)])
    decoded = cv2.imread(str(frame))
    # Undo the exact production glyph transform, including Lanczos bearings and
    # pixel centers, rather than stretching the tight alpha crop to the canvas.
    dynamic = packaging.flower_effects.dynamic_flowers
    spec = packaging.flower_effects.effect_styles()["styles"][style_id]
    font_path = packaging.subtitle_fonts.FONTS["w8"]["path"]
    sprite, face = dynamic.render_text("无尽冬日", spec, font_path)
    box = ImageFont.truetype(font_path, size).getbbox("无尽冬日", anchor="ls")
    ratio = (box[2]-box[0])/(face[2]-face[0])
    enlarged = sprite.resize((round(sprite.width*ratio), round(sprite.height*ratio)), Image.Resampling.LANCZOS)
    left, top, _, _ = enlarged.getbbox()
    rx, ry = enlarged.width/sprite.width, enlarged.height/sprite.height
    parameters = dynamic.parameters(spec)
    gx, gy, _, _ = dynamic.text_extents("无尽冬日", parameters["shader"]["geometry"], font_path)
    dx, dy = max(0, 18-gx), max(0, 18-gy)
    restored = cv2.warpAffine(decoded, np.array([[1/rx, 0., (left+.5)/rx-.5-dx],
        [0., 1/ry, (top+.5)/ry-.5-dy]]), (reference.shape[1], reference.shape[0]), flags=cv2.INTER_LINEAR)
    cv2.imwrite(str(directory / "video-roundtrip.png"), restored)
    return foreground_ssim(reference, restored), result["video_encoding"]


def compare(reference_path: Path, output_dir: Path, style_id: str) -> dict:
    spec = importlib.util.spec_from_file_location("packaging_comparison", SCRIPT)
    packaging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaging)
    reference = cv2.imread(str(reference_path))
    if reference is None:
        raise ValueError(f"cannot read reference: {reference_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    style = packaging.subtitle_style({"subtitle_font": "w8"}, ROOT)
    spec = style["emphasis"]["effects"][style_id]
    height, width = reference.shape[:2]
    if spec["canvas"] != [width, height]:
        raise ValueError("reference dimensions differ from this style's calibration")
    with TemporaryDirectory(prefix="flower-comparison-") as temp:
        work = Path(temp)
        from PIL import Image
        sprite, _ = flower_effects.dynamic_flowers.render_text("无尽冬日", spec, style["font"]["path"], reference_canvas=True)
        sprite.save(work / "sprite.png")
        command = [str(packaging.FFMPEG), "-hide_banner", "-loglevel", "verbose", "-y", "-f", "lavfi",
                   "-i", f"color=black:s={width}x{height}:d=0.04,format=rgb24", "-i", "sprite.png",
                   "-filter_complex", "[0:v][1:v]overlay=format=rgb,format=rgb24",
                   "-frames:v", "1", "sample.png"]
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        rendered = cv2.imread(str(work / "sample.png"))
        (output_dir / "rendered.png").write_bytes((work / "sample.png").read_bytes())
        video_score, encoding = video_roundtrip(packaging, reference, work, style_id)
        (output_dir / "video-roundtrip.png").write_bytes((work / "video-roundtrip.png").read_bytes())
    (output_dir / "reference.png").write_bytes(reference_path.read_bytes())
    score = foreground_ssim(reference, rendered)
    report = {
        "metric": "RGB SSIM; 11x11 Gaussian window, sigma 1.5; mean over union foreground dilated 2px",
        "alignment": "reference font geometry; production font renderer and FFmpeg RGB overlay",
        "method": "font contours with distance-height-normal shader; reference pixels are never read by renderer",
        "style_id": style_id, "score": score, "video_roundtrip_score": video_score,
        "video_roundtrip": "production 1440x2560 H.264 burn-in; sprite crop uniformly restored to reference size",
        "video_encoding": encoding,
        "threshold": .95, "pass": min(score, video_score) >= .95, "video_roundtrip_pass": video_score >= .95,
        "pass_scope": "both procedural text at reference size and actual production video burn-in",
        "reference_sha256": packaging.sha(reference_path), "renderer_sha256": packaging.sha(SCRIPT),
        "effect_sha256": spec["sha256"], "font_renderer_sha256": packaging.sha(Path(flower_effects.dynamic_flowers.__file__)),
        "subtitle_style": style,
    }
    (output_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    restored = cv2.imread(str(output_dir / "video-roundtrip.png"))
    side_by_side = cv2.resize(np.hstack([reference, rendered, restored]), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(output_dir / "comparison.png"), side_by_side)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--style", choices=("fire1", "ice1", "ice2"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.reference and not args.style:
        parser.error("--reference requires --style")
    reports = []
    for style_id in ([args.style] if args.style else ["fire1", "ice1", "ice2"]):
        reference = args.reference or flower_effects.ASSET_ROOT / "references" / f"{style_id}.png"
        report = compare(reference.resolve(), args.output_dir.resolve() / style_id, style_id)
        reports.append({key: report[key] for key in ("style_id", "metric", "score", "video_roundtrip_score",
                                                    "video_roundtrip_pass", "video_encoding", "threshold", "pass", "pass_scope")})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(reports))
    raise SystemExit(0 if all(report["pass"] for report in reports) else 1)


if __name__ == "__main__":
    main()
