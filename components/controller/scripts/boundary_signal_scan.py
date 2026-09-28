from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import wave
from pathlib import Path

import numpy as np
from PIL import Image

EVIDENCE_SPEC = importlib.util.spec_from_file_location("post_encode_evidence", Path(__file__).resolve().with_name("post_encode_evidence.py"))
evidence_module = importlib.util.module_from_spec(EVIDENCE_SPEC)
assert EVIDENCE_SPEC.loader
EVIDENCE_SPEC.loader.exec_module(evidence_module)
verify_boundary_files = evidence_module.verify_boundary_files


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_array(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L").resize((96, 96)), dtype=np.float32) / 255.0


def visual_scan(boundary: dict, stable_frames: int) -> dict:
    frames = boundary.get("frames", [])
    numbers = [int(item.get("output_frame_number", -1)) for item in frames]
    if not frames or numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return {"decision": "reject", "failures": ["FRAME_NUMBER_SEQUENCE_INVALID"]}
    arrays = [image_array(Path(item["path"])) for item in frames]
    diffs = [float(np.mean(np.abs(arrays[index + 1] - arrays[index]))) for index in range(len(arrays) - 1)]
    median = float(np.median(diffs)) if diffs else 0.0
    mad = float(np.median(np.abs(np.asarray(diffs) - median))) if diffs else 0.0
    threshold = max(0.08, median + max(0.03, 8.0 * mad))
    boundary_index = int(boundary.get("frames_before_boundary", boundary.get("frames_before_cut", -1)))
    kind = boundary.get("boundary_kind", "concat_cut")
    transitions, failures = [], []
    for index, value in enumerate(diffs):
        if value < threshold:
            continue
        right_index = index + 1
        offset = right_index - boundary_index
        transitions.append({"left_output_frame": numbers[index], "right_output_frame": numbers[right_index], "right_offset_from_boundary": offset, "right_offset_from_cut": offset, "mean_abs_diff": round(value, 6)})
        if kind == "output_start" and 0 < offset < stable_frames:
            failures.append("SHORT_OUTPUT_HEAD_SHOT")
        elif kind == "output_end" and -stable_frames < offset < 0:
            failures.append("SHORT_OUTPUT_TAIL_SHOT")
        elif kind == "concat_cut" and offset != 0 and abs(offset) < stable_frames:
            failures.append("EXTRA_VISUAL_TRANSITION_NEAR_CUT")
    return {"decision": "pass" if not failures else "reject", "boundary_kind": kind, "stable_run_required_frames": stable_frames, "threshold": round(threshold, 6), "transitions": transitions, "failures": sorted(set(failures))}


def audio_scan(boundary: dict) -> dict:
    path = Path(boundary["pcm_path"])
    with wave.open(str(path), "rb") as handle:
        rate, width, channels = handle.getframerate(), handle.getsampwidth(), handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    if width != 2 or channels != 1:
        return {"decision": "reject", "failures": ["PCM_FORMAT_INVALID"]}
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.int64)
    center = int(boundary.get("cut_sample_index_in_pcm", len(samples) // 2))
    delta = np.abs(np.diff(samples, prepend=samples[0]))
    radius = max(1, round(rate * 0.015))
    lo, hi = max(1, center - radius), min(len(delta), center + radius + 1)
    local_max = int(delta[lo:hi].max()) if hi > lo else 0
    baseline = float(np.percentile(delta, 95)) if len(delta) else 0.0
    ratio = local_max / max(1.0, baseline)
    clipped = int(np.sum(np.abs(samples) >= 32760))
    failures = []
    if local_max >= 1200 and ratio >= 6.0:
        failures.append("IMPULSE_WITHIN_15MS_OF_BOUNDARY")
    if clipped > 3:
        failures.append("PCM_CLIPPING_NEAR_BOUNDARY")
    return {"decision": "pass" if not failures else "reject", "sample_rate": rate, "boundary_sample_index_in_pcm": center, "max_delta_within_15ms": local_max, "p95_delta_full_window": round(baseline, 3), "impulse_ratio": round(ratio, 3), "clipped_samples": clipped, "failures": failures}


def scan(evidence_path: Path, stable_frames: int = 60) -> dict:
    evidence = read(evidence_path)
    failures, results = [], []
    if evidence.get("schema") != "ffmpeg-post-encode-evidence/v260928" or evidence.get("complete_plan_scope") is not True:
        failures.append("EVIDENCE_SCHEMA_OR_SCOPE")
    for boundary in evidence.get("cuts", []):
        integrity = verify_boundary_files(boundary)
        if integrity:
            row = {"plan_id": boundary.get("plan_id"), "cut_index": boundary.get("cut_index"), "boundary_kind": boundary.get("boundary_kind"), "decision": "reject", "integrity_failures": integrity}
            results.append(row)
            failures.append(row)
            continue
        visual, audio = visual_scan(boundary, stable_frames), audio_scan(boundary)
        decision = "pass" if visual["decision"] == audio["decision"] == "pass" else "reject"
        row = {"plan_id": boundary.get("plan_id"), "cut_index": boundary.get("cut_index"), "boundary_kind": boundary.get("boundary_kind"), "decision": decision, "visual": visual, "audio": audio}
        results.append(row)
        if decision != "pass":
            failures.append({"plan_id": boundary.get("plan_id"), "cut_index": boundary.get("cut_index"), "boundary_kind": boundary.get("boundary_kind"), "visual": visual.get("failures"), "audio": audio.get("failures")})
    return {"schema": "ffmpeg-boundary-signal-scan/v260928", "decision": "pass" if not failures else "reject", "evidence_path": str(evidence_path.resolve()), "evidence_sha256": sha(evidence_path), "stable_run_required_frames": stable_frames, "results": results, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-stable-shot-frames", "--min-short-shot-frames", dest="stable_frames", type=int, default=60)
    args = parser.parse_args()
    report = scan(args.evidence, args.stable_frames)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": len(report["failures"])}, ensure_ascii=False))
    return 0 if report["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
