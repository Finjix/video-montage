"""Independent subtitle-animation laboratory; no production workflow hooks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
from common import LAB, ROOT, EFFECTS, REFERENCE_TEXT, WIDTH, HEIGHT, write_json
from renderer import render
from comparison import compare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    renderer = commands.add_parser("render", help="Render supplied single-line text")
    renderer.add_argument("--effect", required=True, choices=list(EFFECTS))
    renderer.add_argument("--text", default=REFERENCE_TEXT)
    renderer.add_argument("--output-dir", required=True, type=Path)
    renderer.add_argument("--size", choices=["1920x3414", "1440x2560"], default="1920x3414")
    comparer = commands.add_parser("compare", help="Compare final decoded MP4s")
    comparer.add_argument("--reference", required=True, type=Path)
    comparer.add_argument("--rendered", required=True, type=Path)
    comparer.add_argument("--output-dir", required=True, type=Path)
    comparer.add_argument("--no-visuals", action="store_true")
    suite = commands.add_parser("suite", help="Render and compare all three original examples")
    suite.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    if args.command == "render":
        width, height = map(int, args.size.split("x"))
        print(render(args.effect, args.text, args.output_dir.resolve(), width, height))
        return 0
    if args.command == "compare":
        result = compare(args.reference.resolve(), args.rendered.resolve(), args.output_dir.resolve(), not args.no_visuals)
        print(f"entry={result['entry_mean']:.6f} stable={result['stable_mean']:.6f} pass={result['quantitative_pass']}")
        return 0 if result["quantitative_pass"] else 2
    output = args.output_dir or ROOT / "work" / ("字幕入场动画试验_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S"))
    output = output.resolve()
    reports = {}
    for effect, label in EFFECTS.items():
        path = render(effect, REFERENCE_TEXT, output / "演示")
        result = compare(LAB / "references" / f"{effect}.mp4", path, output / "对照" / label)
        reports[effect] = {key: result[key] for key in ("entry_mean", "stable_mean", "quantitative_pass", "minimum", "visual_review")}
        print(f"{effect}: entry={result['entry_mean']:.6f} stable={result['stable_mean']:.6f}", flush=True)
    write_json(output / "验收汇总.json", {"effects": reports, "quantitative_pass": all(r["quantitative_pass"] for r in reports.values()), "visual_review": "pending"})
    print(output)
    return 0 if all(r["quantitative_pass"] for r in reports.values()) else 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        raise SystemExit(main())
    except (ValueError, FileExistsError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
