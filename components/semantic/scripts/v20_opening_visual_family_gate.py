#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(request_path: Path) -> dict:
    request = read(request_path)
    failures: list[dict] = []
    manifest_path = Path(str(request.get("delivery_manifest_path") or ""))
    if request.get("schema") != "opening-visual-family-release-request/v260928" or not manifest_path.is_file() or sha(manifest_path) != str(request.get("delivery_manifest_sha256", "")).lower():
        return {"schema": "opening-visual-family-release-gate/v260928", "decision": "reject", "failures": [{"code": "REQUEST_OR_MANIFEST_BINDING_INVALID"}]}
    manifest = read(manifest_path)
    results = manifest.get("results", []) if isinstance(manifest.get("results"), list) else []
    expected_ids = {str(row.get("plan_id") or "") for row in results}
    items = request.get("items", []) if isinstance(request.get("items"), list) else []
    actual_ids = {str(item.get("plan_id") or "") for item in items}
    if manifest.get("output_count") != len(results) or expected_ids != actual_ids or len(items) != len(expected_ids):
        failures.append({"code": "COMPLETE_OUTPUT_SCOPE_REQUIRED", "expected": sorted(expected_ids), "actual": sorted(actual_ids)})
    max_family = int(request.get("max_opening_visual_family_uses", 0) or 0)
    max_pair = int(request.get("max_opening_second_visual_pair_uses", 0) or 0)
    if max_family <= 0 or max_pair <= 0:
        failures.append({"code": "VISUAL_LIMITS_REQUIRED"})
    family_usage: Counter[str] = Counter()
    pair_usage: Counter[tuple[str, str]] = Counter()
    first_frame_family: dict[str, str] = {}
    signature_family: dict[str, str] = {}
    result_by_id = {str(row.get("plan_id")): row for row in results}
    for item in items:
        plan_id = str(item.get("plan_id") or "")
        family = str(item.get("opening_visual_family_id") or "")
        second = str(item.get("second_visual_family_id") or "")
        evidence_path = Path(str(item.get("opening_frame_evidence_path") or ""))
        if not family or not second:
            failures.append({"code": "VISUAL_FAMILY_ID_REQUIRED", "plan_id": plan_id})
            continue
        if not evidence_path.is_file() or sha(evidence_path) != str(item.get("opening_frame_evidence_sha256", "")).lower():
            failures.append({"code": "OPENING_FRAME_EVIDENCE_INVALID", "plan_id": plan_id})
            continue
        evidence = read(evidence_path)
        output_path = Path(str(result_by_id.get(plan_id, {}).get("output_path") or ""))
        output_hash = str(result_by_id.get(plan_id, {}).get("output_sha256") or "").lower()
        frames = evidence.get("frames", []) if isinstance(evidence.get("frames"), list) else []
        if evidence.get("schema") != "encoded-opening-frame-evidence/v260928" or evidence.get("decision") != "pass" or evidence.get("plan_id") != plan_id or evidence.get("opening_visual_family_id") != family:
            failures.append({"code": "OPENING_FRAME_EVIDENCE_BINDING_MISMATCH", "plan_id": plan_id})
        if not output_path.is_file() or sha(output_path) != output_hash or str(evidence.get("output_sha256", "")).lower() != output_hash:
            failures.append({"code": "OUTPUT_HASH_BINDING_INVALID", "plan_id": plan_id})
        if int(evidence.get("reviewed_opening_frame_count", 0) or 0) < 60 or len(frames) < 60:
            failures.append({"code": "SIXTY_OPENING_FRAMES_REQUIRED", "plan_id": plan_id})
        for frame in frames:
            path = Path(str(frame.get("path") or ""))
            if not path.is_file() or sha(path) != str(frame.get("sha256", "")).lower():
                failures.append({"code": "OPENING_FRAME_HASH_INVALID", "plan_id": plan_id})
                break
        if evidence.get("independent_visual_review") != "pass" or evidence.get("celebrity_present_from_first_frame") is not True:
            failures.append({"code": "INDEPENDENT_OPENING_VISUAL_REVIEW_REQUIRED", "plan_id": plan_id})
        first_sha = str(evidence.get("first_frame_sha256") or "")
        signature = str(evidence.get("opening_perceptual_signature") or "")
        if not first_sha or not signature:
            failures.append({"code": "OPENING_VISUAL_SIGNATURE_REQUIRED", "plan_id": plan_id})
        if first_sha in first_frame_family and first_frame_family[first_sha] != family:
            failures.append({"code": "IDENTICAL_OPENING_SPLIT_ACROSS_FAMILIES", "plan_id": plan_id})
        if signature in signature_family and signature_family[signature] != family:
            failures.append({"code": "NEAR_DUPLICATE_OPENING_SPLIT_ACROSS_FAMILIES", "plan_id": plan_id})
        first_frame_family[first_sha] = family
        signature_family[signature] = family
        family_usage[family] += 1
        pair_usage[(family, second)] += 1
    for family, count in family_usage.items():
        if count > max_family:
            failures.append({"code": "OPENING_VISUAL_FAMILY_REUSE_EXCEEDED", "family": family, "count": count, "limit": max_family})
    for pair, count in pair_usage.items():
        if count > max_pair:
            failures.append({"code": "OPENING_SECOND_VISUAL_PAIR_REUSE_EXCEEDED", "pair": list(pair), "count": count, "limit": max_pair})
    minimum_families = math.ceil(len(expected_ids) / max_family) if max_family else 0
    if len(family_usage) < minimum_families:
        failures.append({"code": "OPENING_VISUAL_FAMILY_CAPACITY_SHORTAGE", "actual": len(family_usage), "required": minimum_families})
    return {
        "schema": "opening-visual-family-release-gate/v260928",
        "decision": "pass" if not failures else "reject",
        "delivery_manifest_path": str(manifest_path.resolve()),
        "delivery_manifest_sha256": sha(manifest_path),
        "checked_outputs": len(items),
        "opening_visual_family_usage": dict(family_usage),
        "opening_second_visual_pair_usage": {f"{left}->{right}": count for (left, right), count in pair_usage.items()},
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.request.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
