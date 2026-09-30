from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame_array(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L").resize((96, 96)), dtype=np.float32) / 255.0


def scan_window(window: dict, side: str, min_stable_frames: int) -> dict:
    frames = window.get("frames", [])
    numbers = [int(item.get("source_frame_number", -1)) for item in frames]
    failures = []
    if not frames or numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return {"side": side, "decision": "reject", "failures": ["FRAME_NUMBER_SEQUENCE_INVALID"]}
    arrays = [frame_array(Path(item["path"])) for item in frames]
    diffs = [float(np.mean(np.abs(arrays[i + 1] - arrays[i]))) for i in range(len(arrays) - 1)]
    median = float(np.median(diffs)) if diffs else 0.0
    mad = float(np.median(np.abs(np.asarray(diffs) - median))) if diffs else 0.0
    threshold = max(0.08, median + max(0.03, 8.0 * mad))
    transitions = []
    boundary_index = int(window.get("boundary_index", -1))
    for index, value in enumerate(diffs):
        if value < threshold:
            continue
        right_index = index + 1
        offset = right_index - boundary_index
        transitions.append({
            "left_source_frame": numbers[index],
            "right_source_frame": numbers[right_index],
            "right_offset_from_boundary": offset,
            "mean_abs_diff": round(value, 6),
        })
        if side == "in" and 0 < offset < min_stable_frames:
            failures.append("SHORT_HEAD_SHOT_AFTER_IN")
        if side == "out" and -min_stable_frames < offset < 0:
            failures.append("SHORT_TAIL_SHOT_BEFORE_OUT")
    return {
        "side": side,
        "decision": "pass" if not failures else "reject",
        "boundary_frame": window.get("boundary_frame"),
        "boundary_index": boundary_index,
        "stable_run_required_frames": min_stable_frames,
        "transition_threshold": round(threshold, 6),
        "transitions": transitions,
        "failures": sorted(set(failures)),
    }


def scan_bundle(bundle_path: Path, min_stable_frames: int = 60) -> dict:
    bundle = read(bundle_path)
    failures = []
    if bundle.get("schema") != "candidate-boundary-evidence/v260928":
        failures.append("BUNDLE_SCHEMA")
    results = []
    for side in ("in", "out"):
        result = scan_window(bundle.get("dense_windows", {}).get(side, {}), side, min_stable_frames)
        results.append(result)
        failures.extend(f"{side}:{item}" for item in result.get("failures", []))
    return {
        "schema": "video-montage-boundary-signal-scan/v260928",
        "decision": "pass" if not failures else "reject",
        "bundle_path": str(bundle_path.resolve()),
        "bundle_sha256": sha(bundle_path),
        "candidate_id": bundle.get("candidate_id"),
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-stable-shot-frames", "--min-short-shot-frames", dest="min_stable_shot_frames", type=int, default=60)
    args = parser.parse_args()
    report = scan_bundle(args.bundle, args.min_stable_shot_frames)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
