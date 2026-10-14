#!/usr/bin/env python3
"""Copy only release-authorized outputs into a new or matching delivery directory."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "v9_gate_runtime.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gate)
AUDIT_SPEC = importlib.util.spec_from_file_location("v20_fail_closed", ROOT / "v20_fail_closed.py")
audit = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC.loader
AUDIT_SPEC.loader.exec_module(audit)


def safe_name(value: str) -> str:
    result = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    if not result:
        raise ValueError("empty plan id")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-authorization", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    authorization = gate.load_json(args.release_authorization)
    if authorization.get("schema") != "semantic-release-authorization/v260928" or authorization.get("decision") != "pass" or authorization.get("failures"):
        raise ValueError("release authorization is not passing")
    rows = authorization.get("authorized_outputs", [])
    if not rows:
        raise ValueError("no authorized outputs")
    index_path = Path(str(authorization.get("locked_index_path", "")))
    if not index_path.is_file() or gate.sha_file(index_path) != authorization.get("locked_index_sha256"):
        raise ValueError("locked index changed")
    index = gate.load_json(index_path)
    request_path = Path(str(index.get("request_path", "")))
    if not request_path.is_file() or gate.sha_file(request_path) != index.get("request_sha256"):
        raise ValueError("batch lock request changed")
    request = gate.load_json(request_path)
    evidence_paths = {
        "batch_lock_request": request_path,
        "candidate_inventory": Path(str(request.get("candidate_inventory_path", ""))),
        "work_order": Path(str(request.get("work_order_path", ""))),
    }
    for name in ("candidate_inventory", "work_order"):
        path = evidence_paths[name]
        if not path.is_file() or gate.sha_file(path) != request.get(f"{name}_sha256"):
            raise ValueError(f"{name} changed")
    work_order = gate.load_json(evidence_paths["work_order"])
    plan_ids = [str(item.get("plan_id") or "") for item in index.get("plans", [])]
    authorized_ids = [str(item.get("plan_id") or "") for item in rows]
    count = int(work_order.get("requested_outputs", 0) or 0)
    if (request.get("package_id") != gate.PACKAGE_ID or count != len(rows)
            or len({pid.casefold() for pid in authorized_ids}) != count
            or len({pid.casefold() for pid in plan_ids}) != count or set(authorized_ids) != set(plan_ids)):
        raise ValueError("authorization does not cover the complete locked batch")
    registry = ROOT.parents[2] / "references/semantic/wuzimu-v20-invalid-intervals.json"
    prelock = audit.audit_request(request_path, registry)
    if prelock.get("decision") != "pass":
        raise ValueError("current complete-batch prelock audit rejected")
    prelock_path = args.manifest.parent / "prelock_audit.json"
    gate.atomic_json(prelock_path, prelock)
    evidence_paths["prelock_audit"] = prelock_path
    args.delivery_dir.mkdir(parents=True, exist_ok=True)
    delivered = []
    for row in rows:
        source = Path(row["export_path"])
        if not source.is_file() or gate.sha_file(source) != row["export_sha256"]:
            raise ValueError(f"authorized export hash mismatch: {row.get('plan_id')}")
        target = args.delivery_dir / f"{safe_name(row['plan_id'])}{source.suffix.lower()}"
        if target.exists():
            if gate.sha_file(target) != row["export_sha256"]:
                raise FileExistsError(f"delivery collision: {target}")
        else:
            partial = target.with_name(target.name + ".partial")
            if partial.exists():
                partial.unlink()
            shutil.copy2(source, partial)
            if gate.sha_file(partial) != row["export_sha256"]:
                partial.unlink(missing_ok=True)
                raise ValueError(f"delivery copy hash mismatch: {target}")
            partial.replace(target)
        delivered.append({"plan_id": row["plan_id"], "path": str(target.resolve()), "sha256": gate.sha_file(target)})
    manifest = {"schema": "semantic-delivery-manifest/v260928", "package_id": gate.PACKAGE_ID, "package_version": "v260928", "decision": "pass", "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "requested_outputs": count, "authorized_outputs": authorized_ids, "evidence": {name: {"path": str(path.resolve()), "sha256": gate.sha_file(path)} for name, path in evidence_paths.items()}, "release_authorization_path": str(args.release_authorization.resolve()), "release_authorization_sha256": gate.sha_file(args.release_authorization), "delivery_dir": str(args.delivery_dir.resolve()), "count": len(delivered), "outputs": delivered}
    gate.atomic_json(args.manifest, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
