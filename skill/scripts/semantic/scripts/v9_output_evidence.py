#!/usr/bin/env python3
"""Build rendered-output technical, cut-frame, audio-window, and ASR-source manifests."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FFMPEG = str(ROOT.parents[2] / "assets" / "dependencies" / "ffmpeg" / "bin" / "ffmpeg.exe")
FFPROBE = str(ROOT.parents[2] / "assets" / "dependencies" / "ffmpeg" / "bin" / "ffprobe.exe")
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "v9_gate_runtime.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gate)


def run(command: list[str], text: bool = True):
    result = subprocess.run(command, capture_output=True, text=text, encoding="utf-8" if text else None, errors="replace" if text else None, shell=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:] if text else "subprocess failed")
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked-index", type=Path, required=True)
    parser.add_argument("--render-index", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--rendered-source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    locked = gate.load_json(args.locked_index)
    rendered = gate.load_json(args.render_index)
    by_plan = {row["plan_id"]: row for row in rendered.get("results", []) if row.get("status") in {"completed", "reused_completed"}}
    if len(by_plan) != len(locked.get("plans", [])):
        raise ValueError("render index is incomplete")
    evidence_rows, asr_sources = [], []
    for entry in locked["plans"]:
        result = by_plan[entry["plan_id"]]
        export = Path(result["export_path"])
        if gate.sha_file(export) != result["export_sha256"]:
            raise ValueError(f"export hash mismatch {entry['plan_id']}")
        plan = gate.load_json(Path(entry["path"]))
        probe = json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(export)]))
        durations = [round(float(segment["duration"]) * 30) / 30 for segment in plan["segments"]]
        cuts, cursor = [], 0.0
        for duration in durations[:-1]:
            cursor += duration
            cuts.append(cursor)
        plan_dir = args.evidence_dir / entry["plan_id"]
        cut_rows = []
        for cut_index, cut in enumerate(cuts, 1):
            cut_dir = plan_dir / f"cut_{cut_index:02d}"
            frames = []
            for label, offset in (("m020", -0.20), ("m012", -0.12), ("m004", -0.04), ("p004", 0.04), ("p012", 0.12), ("p020", 0.20)):
                path = cut_dir / f"{label}.jpg"
                if not path.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{max(0, cut + offset):.6f}", "-i", str(export), "-frames:v", "1", str(path)])
                frames.append({"label": label, "path": str(path.resolve()), "sha256": gate.sha_file(path), "output_time": round(max(0, cut + offset), 6)})
            audio_path = cut_dir / "cut_window.wav"
            audio_start = max(0, cut - 0.30)
            if not audio_path.is_file():
                run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{audio_start:.6f}", "-i", str(export), "-t", "0.600000", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path)])
            cut_rows.append({"cut_index": cut_index, "cut_time": round(cut, 6), "frames": frames, "audio_path": str(audio_path.resolve()), "audio_sha256": gate.sha_file(audio_path)})
        evidence_rows.append({"plan_id": entry["plan_id"], "plan_path": entry["path"], "plan_sha256": entry["sha256"], "export_path": str(export.resolve()), "export_sha256": result["export_sha256"], "probe": probe, "cuts": cut_rows})
        asr_sources.append({"source_id": entry["plan_id"], "source_path": str(export.resolve()), "source_sha256": result["export_sha256"], "processing_stage": "rendered_output_v9"})
    gate.atomic_json(args.rendered_source_manifest, {"schema": "semantic-source-manifest/v260928", "batch_id": locked.get("batch_id"), "sources": asr_sources})
    gate.atomic_json(args.output, {"schema": "semantic-output-evidence-manifest/v260928", "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "locked_index_path": str(args.locked_index.resolve()), "locked_index_sha256": gate.sha_file(args.locked_index), "render_index_path": str(args.render_index.resolve()), "render_index_sha256": gate.sha_file(args.render_index), "outputs": evidence_rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
