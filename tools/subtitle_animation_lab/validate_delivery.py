"""Export replacement-text/specification evidence for a completed lab suite."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import EFFECTS, FONT, LAB, decode, probe, write_json, foreground_ssim
from comparison import compare
from renderer import Animation, render

SAMPLES = {"短句": "冰雪", "长句": "全新挑战即将开启", "中英文混排": "ABC冰雪2026"}


def specification(path: Path, width: int, height: int) -> dict:
    spec = probe(path)
    assert (spec["width"], spec["height"]) == (width, height), spec
    assert spec["frames"] == 60 and spec["fps"] == "60/1", spec
    assert spec["audio_streams"] == 0 and spec["codec"] == "h264", spec
    assert abs(spec["video_duration_seconds"] - 1.) < 1e-4, spec
    return spec


def validate(output: Path, group: str = "all") -> dict:
    results = {}
    if group in ("all", "resolution"):
        for effect, label in EFFECTS.items():
            path = render(effect, "无尽冬日", output / "1440版", 1440, 2560)
            comparison = compare(LAB / "references" / f"{effect}.mp4", path,
                                 output / "1440版" / "对照" / label, visuals=False)
            assert comparison["quantitative_pass"], comparison["entry_mean"]
            results[effect] = {"path": str(path), "spec": specification(path, 1440, 2560),
                               "entry_mean": comparison["entry_mean"], "stable_mean": comparison["stable_mean"]}
            print(f"1440 {effect}: {comparison['entry_mean']:.6f} / {comparison['stable_mean']:.6f}", flush=True)
        write_json(output / "1440版" / "规格验证.json", results)
    if group in ("all", "text"):
        results = {}
        sheet = Image.new("RGB", (1800, 1080), "#111111")
        draw, face = ImageDraw.Draw(sheet), ImageFont.truetype(str(FONT), 22)
        for row, (name, text) in enumerate(SAMPLES.items()):
            for col, (effect, label) in enumerate(EFFECTS.items()):
                path = render(effect, text, output / "替换文字" / name, 1440, 2560)
                animation = Animation(effect, text)
                sample = animation.frame(22, 1440, 2560)
                np.testing.assert_array_equal(sample, Animation(effect, text).frame(22, 1440, 2560))
                settled = animation.frame(30, 1440, 2560)
                np.testing.assert_array_equal(settled, animation.frame(59, 1440, 2560))
                ys, xs = np.where(settled.max(2) > 25)
                assert len(xs) and xs.min() > 0 and xs.max() < 1439 and ys.min() > 0 and ys.max() < 2559
                key = f"{effect}/{name}"
                results[key] = {"text": text, "path": str(path), "spec": specification(path, 1440, 2560),
                                "deterministic_frame_22": True, "stable_frames_30_59_identical": True,
                                "stable_bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]}
                decoded_stable, stability = None, []
                for n, frame in enumerate(decode(path)):
                    if n == 30:
                        decoded_stable = frame
                    if n >= 30:
                        stability.append(foreground_ssim(decoded_stable, frame))
                    if n in (12, 24, 40):
                        image = Image.fromarray(frame[1560:1950, 45:1395])
                        image.thumbnail((580, 100))
                        k = (12, 24, 40).index(n)
                        sheet.paste(image, (col * 600 + 10, row * 360 + k * 110 + 40))
                results[key]["decoded_stable_ssim_vs_frame_30_min"] = min(stability)
                assert min(stability) >= .999, results[key]
                draw.text((col * 600 + 10, row * 360 + 8), f"{label}  {text}", font=face, fill="white")
                print(f"replacement {effect}/{name}: spec, layout, determinism, stable frames passed", flush=True)
        write_json(output / "替换文字" / "验证报告.json", results)
        sheet.save(output / "替换文字" / "排版对照.jpg", quality=94)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--group", choices=["all", "resolution", "text"], default="all")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cv2.setNumThreads(2)
    validate(args.output_dir.resolve(), args.group)
