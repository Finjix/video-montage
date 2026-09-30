#!/usr/bin/env python3
"""Build hash-bound pre-render source-boundary frame/audio windows for every locked cut."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FFMPEG = str(ROOT.parents[2] / "assets" / "dependencies" / "ffmpeg" / "bin" / "ffmpeg.exe")
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "v9_gate_runtime.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gate)


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])


def frame(source: str, when: float, output: Path) -> dict:
    if not output.is_file():
        output.parent.mkdir(parents=True, exist_ok=True)
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{when:.6f}", "-i", source, "-frames:v", "1", str(output)])
    return {"path": str(output.resolve()), "sha256": gate.sha_file(output), "source_time": round(when, 6)}


def audio(source: str, start: float, duration: float, output: Path) -> dict:
    if not output.is_file():
        output.parent.mkdir(parents=True, exist_ok=True)
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{start:.6f}", "-i", source, "-t", f"{duration:.6f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)])
    return {"path": str(output.resolve()), "sha256": gate.sha_file(output), "source_start": round(start, 6), "duration": round(duration, 6)}


def prior_source_segment_index(segments: list[dict], right_index: int) -> int | None:
    """Find an earlier source use separated from the current cut by another clip."""
    returned = segments[right_index]
    source_hash = str(returned.get("source_sha256") or "").lower()
    source_path = str(Path(returned.get("source_path") or "").resolve()).casefold()
    for index in range(right_index - 2, -1, -1):
        earlier = segments[index]
        earlier_hash = str(earlier.get("source_sha256") or "").lower()
        same_source = (source_hash == earlier_hash if source_hash and earlier_hash else
                       source_path == str(Path(earlier.get("source_path") or "").resolve()).casefold())
        if same_source:
            return index + 1
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked-index", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    index = gate.load_json(args.locked_index)
    rows = []
    for entry in index.get("plans", []):
        plan_path = Path(entry["path"])
        allowed, errors = gate.verify_render_authorization(args.locked_index, args.gate_report, plan_path)
        if not allowed:
            raise ValueError(f"unauthorized plan {entry['plan_id']}: {errors}")
        plan = gate.load_json(plan_path)
        segments = plan["segments"]
        for cut_index, (left, right) in enumerate(zip(segments, segments[1:]), 1):
            return_index = prior_source_segment_index(segments, cut_index)
            cut_dir = args.evidence_dir / entry["plan_id"] / f"cut_{cut_index:02d}"
            left_out = float(left["source_out"])
            right_in = float(right["source_in"])
            frames = []
            for label, offset in (("left_020", -0.20), ("left_012", -0.12), ("left_004", -0.04)):
                when = max(float(left["source_in"]), left_out + offset)
                frames.append({"label": label, **frame(left["source_path"], when, cut_dir / f"{label}.jpg")})
            for label, offset in (("right_004", 0.04), ("right_012", 0.12), ("right_020", 0.20)):
                when = min(float(right["source_out"]) - 0.001, right_in + offset)
                frames.append({"label": label, **frame(right["source_path"], when, cut_dir / f"{label}.jpg")})
            left_audio_start = max(float(left["source_in"]), left_out - 0.30)
            audio_rows = [
                {"side": "left", **audio(left["source_path"], left_audio_start, left_out - left_audio_start, cut_dir / "left_tail.wav")},
                {"side": "right", **audio(right["source_path"], right_in, min(0.30, float(right["source_out"]) - right_in), cut_dir / "right_head.wav")},
            ]
            rows.append({"plan_id": entry["plan_id"], "plan_sha256": entry["sha256"], "cut_index": cut_index, "left_candidate_id": left["candidate_id"], "right_candidate_id": right["candidate_id"], "left_boundary_person_id": left["boundary_close_person_id"], "right_boundary_person_id": right["boundary_open_person_id"], "source_return_visual_risk": return_index is not None, "prior_source_segment_index": return_index, "frames": frames, "audio": audio_rows})
    manifest = {"schema": "semantic-cut-smoke-manifest/v260928", "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "locked_index_path": str(args.locked_index.resolve()), "locked_index_sha256": gate.sha_file(args.locked_index), "gate_report_sha256": gate.sha_file(args.gate_report), "decision": "pending_independent_review", "cuts": rows}
    gate.atomic_json(args.output, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
