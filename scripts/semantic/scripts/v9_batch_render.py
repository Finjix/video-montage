#!/usr/bin/env python3
"""Authorized, resumable batch wrapper around the stable portable FFmpeg renderer."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "v9_gate_runtime.py")
gate_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gate_runtime)
FRAME_SPEC = importlib.util.spec_from_file_location("v20_frame_plan_gate", ROOT / "v20_frame_plan_gate.py")
frame_gate = importlib.util.module_from_spec(FRAME_SPEC)
assert FRAME_SPEC.loader
FRAME_SPEC.loader.exec_module(frame_gate)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    result = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    if not result:
        raise ValueError("empty plan id")
    return result


def valid_existing(output: Path, evidence: Path, plan_hash: str) -> bool:
    if not output.is_file() or not evidence.is_file():
        return False
    try:
        value = gate_runtime.load_json(evidence)
    except Exception:
        return False
    return value.get("render_mode") == "source_frame_ranges/v1" and value.get("seconds_only_fallback") is False and value.get("plan_sha256", "").lower() == plan_hash.lower() and value.get("export_sha256", "").lower() == gate_runtime.sha_file(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked-index", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--renderer", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--execution-index", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-items-per-run", type=int, default=5)
    args = parser.parse_args(argv)
    index = gate_runtime.load_json(args.locked_index)
    if index.get("schema") != gate_runtime.LOCK_SCHEMA:
        raise ValueError("locked index schema mismatch")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    plans = index.get("plans", [])
    results = []

    def run_one(entry: dict) -> dict:
        plan_path = Path(entry["path"])
        frame_errors = frame_gate.validate_plan_value(gate_runtime.load_json(plan_path))
        if frame_errors:
            return {"plan_id": entry["plan_id"], "status": "rejected_frame_contract", "errors": frame_errors}
        allowed, errors = gate_runtime.verify_render_authorization(args.locked_index, args.gate_report, plan_path)
        if not allowed:
            return {"plan_id": entry["plan_id"], "status": "rejected_authorization", "errors": errors}
        name = safe_name(entry["plan_id"])
        output = args.output_dir / f"{name}.mp4"
        evidence = args.evidence_dir / f"{name}.render_evidence.json"
        if valid_existing(output, evidence, entry["sha256"]):
            return {"plan_id": entry["plan_id"], "status": "reused_completed", "render_mode": "source_frame_ranges/v1", "plan_path": str(plan_path), "plan_sha256": entry["sha256"], "export_path": str(output.resolve()), "export_sha256": gate_runtime.sha_file(output), "render_evidence_path": str(evidence.resolve()), "render_evidence_sha256": gate_runtime.sha_file(evidence)}
        command = [sys.executable, str(args.renderer.resolve()), "--plan", str(plan_path.resolve()), "--output", str(output.resolve()), "--evidence", str(evidence.resolve())]
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if run.returncode != 0:
            return {"plan_id": entry["plan_id"], "status": "failed", "returncode": run.returncode, "stderr_tail": run.stderr[-4000:]}
        if not valid_existing(output, evidence, entry["sha256"]):
            return {"plan_id": entry["plan_id"], "status": "failed", "returncode": 0, "stderr_tail": "renderer output/evidence binding invalid"}
        return {"plan_id": entry["plan_id"], "status": "completed", "render_mode": "source_frame_ranges/v1", "plan_path": str(plan_path), "plan_sha256": entry["sha256"], "export_path": str(output.resolve()), "export_sha256": gate_runtime.sha_file(output), "render_evidence_path": str(evidence.resolve()), "render_evidence_sha256": gate_runtime.sha_file(evidence)}

    pending = []
    for entry in plans:
        name = safe_name(entry["plan_id"])
        output = args.output_dir / f"{name}.mp4"
        evidence = args.evidence_dir / f"{name}.render_evidence.json"
        if valid_existing(output, evidence, entry["sha256"]):
            results.append(run_one(entry))
        else:
            pending.append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
        for row in pool.map(run_one, pending[:max(1, args.max_items_per_run)]):
            results.append(row)
            gate_runtime.atomic_json(args.progress, {"schema": "semantic-batch-render-progress/v260928", "total": len(plans), "completed": sum(item["status"] in {"completed", "reused_completed"} for item in results), "failed": sum(item["status"] not in {"completed", "reused_completed"} for item in results), "updated_at": now_iso(), "last_plan_id": row["plan_id"]})
            print(json.dumps(row, ensure_ascii=False), flush=True)
    failures = [row for row in results if row["status"] not in {"completed", "reused_completed"}]
    if len(results) < len(plans):
        return 75
    if failures:
        return 2
    order = {entry["plan_id"]: index for index, entry in enumerate(plans)}
    results.sort(key=lambda row: order[row["plan_id"]])
    execution = {"schema": "semantic-batch-render-execution/v260928", "render_mode": "source_frame_ranges/v1", "seconds_only_fallback": False, "batch_id": index.get("batch_id"), "created_at": now_iso(), "locked_index_path": str(args.locked_index.resolve()), "locked_index_sha256": gate_runtime.sha_file(args.locked_index), "gate_report_path": str(args.gate_report.resolve()), "gate_report_sha256": gate_runtime.sha_file(args.gate_report), "decision": "pass" if not failures else "reject", "results": results}
    gate_runtime.atomic_json(args.execution_index, execution)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
