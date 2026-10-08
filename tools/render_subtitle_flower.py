"""Render arbitrary single-line text with a reusable flower style."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
import package_video
import dynamic_flowers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True)
    parser.add_argument("--style", choices=("fire1", "ice1", "ice2"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=1.)
    parser.add_argument("--font", choices=("random", "w8", "smiley", "fangtang"), default="random")
    args = parser.parse_args()
    if not 0 < args.scale <= 10:
        parser.error("--scale must be between 0 and 10")
    style = package_video.subtitle_style({"subtitle_font": args.font}, ROOT)
    image, _ = dynamic_flowers.render_text(args.text, style["emphasis"]["effects"][args.style], style["font"]["path"])
    if args.scale != 1:
        from PIL import Image
        image = image.resize((max(1, round(image.width * args.scale)), max(1, round(image.height * args.scale))), Image.Resampling.LANCZOS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(str(args.output.resolve()))
    print("Font: " + style["font_label"])


if __name__ == "__main__":
    main()
