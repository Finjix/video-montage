#!/usr/bin/env python3
"""Create hash-bound, accurately decoded source frames for candidate proposals."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_SCHEMA = "semantic-source-frame-evidence/v260928"
INDEX_SCHEMA = "semantic-source-frame-evidence-index/v260928"
PROPOSAL_SCHEMA = "semantic-candidate-proposal-index/v260928"
PTS_PATTERN = re.compile(r"pts_time:([-+]?\d+(?:\.\d+)?)")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def safe_component(value: object) -> str:
    raw = str(value)
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in raw)[:96] or "candidate"


def proposal_fingerprint(proposal: dict) -> str:
    fields = {key: proposal.get(key) for key in ("candidate_id", "source_id", "source_path", "source_sha256", "source_in", "source_out")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def frame_times(proposal: dict) -> dict[str, float]:
    start = float(proposal["source_in"])
    end = float(proposal["source_out"])
    if end <= start:
        raise ValueError("proposal source_out must be after source_in")
    inset = min(0.04, (end - start) / 4)
    return {"in": start + inset, "mid": (start + end) / 2, "out": end - inset}


def decode_frame(ffmpeg: str, source: str, requested_time: float, output: Path, tolerance: float) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Input is decoded through the target timestamp. The select filter emits the
    # first decoded frame at or after the requested point, then showinfo exposes its source PTS.
    filter_value = f"select='gte(t\\,{requested_time:.6f})',showinfo"
    command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-i", source, "-an", "-vf", filter_value, "-frames:v", "1", "-fps_mode", "passthrough", "-q:v", "2", "-y", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"FFMPEG_FRAME_EXTRACTION_FAILED: exit={result.returncode} stderr={result.stderr[-500:]}")
    matches = PTS_PATTERN.findall((result.stderr or "") + "\n" + (result.stdout or ""))
    if not matches:
        raise RuntimeError("FFMPEG_FRAME_PTS_MISSING")
    decoded_time = float(matches[-1])
    if abs(decoded_time - requested_time) > tolerance:
        raise RuntimeError(f"FFMPEG_FRAME_PTS_OUT_OF_TOLERANCE: requested={requested_time:.6f} decoded={decoded_time:.6f}")
    return {"requested_time": round(requested_time, 6), "decoded_pts_time": decoded_time, "path": str(output.resolve()), "sha256": sha_file(output), "extractor": "ffmpeg_accurate_decode"}


def asr_rows(index: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in index.get("results", []):
        if isinstance(row, dict) and row.get("status") in {"completed", "reused_completed"} and row.get("source_id") and row.get("path") and row.get("sha256"):
            rows[str(row["source_id"])] = row
    return rows


def validate_proposal(proposal: dict, asr_index_rows: dict[str, dict]) -> tuple[dict, dict]:
    required = ("candidate_id", "source_id", "source_path", "source_sha256", "source_in", "source_out")
    missing = [key for key in required if proposal.get(key) in (None, "")]
    if missing:
        raise ValueError("PROPOSAL_FIELDS_MISSING:" + ",".join(missing))
    source_id = str(proposal["source_id"])
    index_row = asr_index_rows.get(source_id)
    if not index_row:
        raise ValueError("PROPOSAL_SOURCE_NOT_IN_COMPLETED_ASR_INDEX")
    asr_path = Path(index_row["path"])
    if not asr_path.is_file() or sha_file(asr_path).lower() != str(index_row["sha256"]).lower():
        raise ValueError("ASR_INDEX_REFERENCE_INVALID")
    asr = load_json(asr_path)
    if asr.get("source_id") != source_id or str(asr.get("source_sha256", "")).lower() != str(proposal["source_sha256"]).lower() or asr.get("source_path") != proposal["source_path"]:
        raise ValueError("PROPOSAL_ASR_SOURCE_BINDING_MISMATCH")
    frame_times(proposal)
    return asr, index_row


def valid_existing(path: Path, proposal: dict, asr_row: dict) -> bool:
    if not path.is_file():
        return False
    try:
        value = load_json(path)
        if value.get("schema") != EVIDENCE_SCHEMA or value.get("status") != "completed" or value.get("proposal_sha256") != proposal_fingerprint(proposal):
            return False
        if value.get("source_asr_sha256", "").lower() != str(asr_row.get("sha256", "")).lower():
            return False
        by_position = {row.get("position"): row for row in value.get("frames", []) if isinstance(row, dict)}
        return all(position in by_position and Path(by_position[position].get("path", "")).is_file() and sha_file(Path(by_position[position]["path"])).lower() == str(by_position[position].get("sha256", "")).lower() for position in ("in", "mid", "out"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def process_one(proposal: dict, asr_row: dict, output_dir: Path, ffmpeg: str, tolerance: float) -> dict:
    candidate_id = str(proposal["candidate_id"])
    evidence_path = output_dir / safe_component(candidate_id) / "frame_evidence.json"
    if valid_existing(evidence_path, proposal, asr_row):
        return {"candidate_id": candidate_id, "status": "reused_completed", "path": str(evidence_path.resolve()), "sha256": sha_file(evidence_path)}
    frames = []
    for position, requested_time in frame_times(proposal).items():
        frame = decode_frame(ffmpeg, str(proposal["source_path"]), requested_time, evidence_path.parent / f"{position}.jpg", tolerance)
        frame["position"] = position
        frames.append(frame)
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "candidate_id": candidate_id,
        "proposal_sha256": proposal_fingerprint(proposal),
        "source_id": proposal["source_id"],
        "source_path": proposal["source_path"],
        "source_sha256": proposal["source_sha256"],
        "source_asr_path": asr_row["path"],
        "source_asr_sha256": asr_row["sha256"],
        "source_in": float(proposal["source_in"]),
        "source_out": float(proposal["source_out"]),
        "frames": frames,
        "created_at": now_iso(),
        "extractor": "ffmpeg_accurate_decode",
        "wpf_evidence_accepted": False,
    }
    atomic_json(evidence_path, evidence)
    return {"candidate_id": candidate_id, "status": "completed", "path": str(evidence_path.resolve()), "sha256": sha_file(evidence_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-asr-index", type=Path, required=True)
    parser.add_argument("--proposal-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=str(Path(__file__).resolve().parents[3] / "dependencies" / "ffmpeg" / "bin" / "ffmpeg.exe"))
    parser.add_argument("--max-items-per-run", type=int, default=24)
    parser.add_argument("--pts-tolerance-seconds", type=float, default=0.25)
    args = parser.parse_args(argv)
    if args.max_items_per_run < 1 or args.pts_tolerance_seconds <= 0:
        raise ValueError("positive item limit and PTS tolerance required")
    source_asr_index = load_json(args.source_asr_index)
    proposal_index = load_json(args.proposal_index)
    if source_asr_index.get("schema") != "semantic-source-asr-index/v260928" or proposal_index.get("schema") != PROPOSAL_SCHEMA:
        raise ValueError("SOURCE_ASR_OR_PROPOSAL_SCHEMA_MISMATCH")
    rows = asr_rows(source_asr_index)
    proposals = proposal_index.get("proposals")
    if not isinstance(proposals, list) or not proposals or len({str(row.get("candidate_id")) for row in proposals if isinstance(row, dict)}) != len(proposals):
        raise ValueError("UNIQUE_NONEMPTY_PROPOSALS_REQUIRED")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    pending: list[tuple[dict, dict]] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise ValueError("PROPOSAL_OBJECT_REQUIRED")
        _, asr_row = validate_proposal(proposal, rows)
        evidence_path = args.output_dir / safe_component(proposal["candidate_id"]) / "frame_evidence.json"
        if valid_existing(evidence_path, proposal, asr_row):
            results.append({"candidate_id": proposal["candidate_id"], "status": "reused_completed", "path": str(evidence_path.resolve()), "sha256": sha_file(evidence_path)})
        else:
            pending.append((proposal, asr_row))
    failures = 0
    for proposal, asr_row in pending[:args.max_items_per_run]:
        try:
            row = process_one(proposal, asr_row, args.output_dir, args.ffmpeg, args.pts_tolerance_seconds)
        except Exception as exc:
            row = {"candidate_id": proposal.get("candidate_id"), "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            failures += 1
        results.append(row)
        print(json.dumps({**row, "total": len(proposals)}, ensure_ascii=False), flush=True)
    completed = sum(1 for row in results if row.get("status") in {"completed", "reused_completed"})
    atomic_json(args.progress, {"schema": "semantic-source-frame-evidence-progress/v260928", "total": len(proposals), "completed": completed, "remaining": len(proposals) - completed, "failed": failures, "updated_at": now_iso(), "extractor": "ffmpeg_accurate_decode", "wpf_evidence_accepted": False})
    if failures:
        return 2
    if completed < len(proposals):
        return 75
    ordered = {str(row["candidate_id"]): position for position, row in enumerate(proposals)}
    results.sort(key=lambda row: ordered[str(row["candidate_id"])])
    atomic_json(args.index, {"schema": INDEX_SCHEMA, "decision": "pass", "source_asr_index_path": str(args.source_asr_index.resolve()), "source_asr_index_sha256": sha_file(args.source_asr_index), "proposal_index_path": str(args.proposal_index.resolve()), "proposal_index_sha256": sha_file(args.proposal_index), "completed_at": now_iso(), "extractor": "ffmpeg_accurate_decode", "wpf_evidence_accepted": False, "results": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
