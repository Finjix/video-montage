#!/usr/bin/env python3
"""Deterministic V11 semantic gate with V9 artifact compatibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from v20_fail_closed import audit_request_value

try:
    from v15_content_gate import audit_plan as audit_v15_plan, audit_sol_review
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v15_content_gate import audit_plan as audit_v15_plan, audit_sol_review

PACKAGE_ID = "video-montage"
REPORT_SCHEMA = "semantic-batch-gate-report/v260928"
LOCK_SCHEMA = "semantic-locked-plan-index/v260928"
STATE_SCHEMA = "semantic-production-gate-state/v260928"
PHASES = [
    "INIT", "INVENTORY", "CANDIDATE_AUDITED", "GLOBAL_SOLVED",
    "PRELOCK_PASSED", "CUT_SMOKE_PASSED", "RENDERED",
    "OUTPUT_QC_PASSED", "RELEASED",
]


def opening_visual_reuse_failures(family_usage: Counter, pair_usage: Counter, family_limit: int | None, pair_limit: int | None) -> list[tuple[str, str, str]]:
    failures: list[tuple[str, str, str]] = []
    if family_limit is not None:
        for family, count in family_usage.items():
            if count > int(family_limit):
                failures.append(("OPENING_VISUAL_FAMILY_REUSE_EXCEEDED", f"opening_visual_family:{family}", f"{count}>{int(family_limit)}"))
    if pair_limit is not None:
        for (left, right), count in pair_usage.items():
            if count > int(pair_limit):
                failures.append(("OPENING_SECOND_VISUAL_PAIR_REUSE_EXCEEDED", f"opening_second_pair:{left}->{right}", f"{count}>{int(pair_limit)}"))
    return failures
ALLOWED_TRANSITIONS = {
    "INIT": {"INVENTORY"},
    "INVENTORY": {"CANDIDATE_AUDITED"},
    "CANDIDATE_AUDITED": {"GLOBAL_SOLVED"},
    "GLOBAL_SOLVED": {"PRELOCK_PASSED", "REPAIR_REQUIRED"},
    "PRELOCK_PASSED": {"CUT_SMOKE_PASSED", "REPAIR_REQUIRED"},
    "CUT_SMOKE_PASSED": {"RENDERED", "REPAIR_REQUIRED"},
    "RENDERED": {"OUTPUT_QC_PASSED", "REPAIR_REQUIRED"},
    "OUTPUT_QC_PASSED": {"RELEASED", "REPAIR_REQUIRED"},
    "REPAIR_REQUIRED": {"CANDIDATE_AUDITED"},
    "RELEASED": set(),
}
REQUIRED_PHASE_ARTIFACTS = {
    "INVENTORY": {"work_order"},
    "CANDIDATE_AUDITED": {"candidate_inventory"},
    "GLOBAL_SOLVED": {"unique_plan_witness"},
    "PRELOCK_PASSED": {"gate_report", "locked_index"},
    "CUT_SMOKE_PASSED": {"cut_smoke_report"},
    "RENDERED": {"batch_execution_report"},
    "OUTPUT_QC_PASSED": {"output_qc_report"},
    "RELEASED": {"release_authorization"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(temp, path)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum())


def normalized_text_sha(text: str) -> str:
    return sha_bytes(normalize_text(text).encode("utf-8"))


def trigrams(text: str) -> set[str]:
    text = normalize_text(text)
    if len(text) < 3:
        return {text} if text else set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


def trigram_similarity(left: str, right: str) -> float:
    a, b = trigrams(left), trigrams(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def candidate_fingerprint(candidate: dict) -> str:
    payload = {
        "source_sha256": candidate["source_sha256"].lower(),
        "processing_stage": candidate["processing_stage"],
        "source_in": f"{float(candidate['source_in']):.6f}",
        "source_out": f"{float(candidate['source_out']):.6f}",
        "source_in_frame": int(candidate["source_in_frame"]),
        "speech_end_frame": int(candidate["speech_end_frame"]),
        "source_out_frame_exclusive": int(candidate["source_out_frame_exclusive"]),
        "source_fps_num": int(candidate["source_fps_num"]),
        "source_fps_den": int(candidate["source_fps_den"]),
        "candidate_text_sha256": candidate["candidate_text_sha256"].lower(),
    }
    return sha_bytes(canonical_bytes(payload))


def valid_person_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"person:[a-z0-9][a-z0-9_-]{1,63}", value))


def verified_json(gate: "Gate", path_value: object, hash_value: object, scope: str) -> dict | None:
    if not gate.verify_ref(path_value, hash_value, scope):
        return None
    try:
        return load_json(Path(str(path_value)))
    except Exception as exc:
        gate.fail("INVALID_EVIDENCE_JSON", scope, str(exc))
        return None


def validate_live_action_evidence(gate: "Gate", candidate: dict, policy: dict) -> None:
    cid = str(candidate.get("candidate_id", "<missing>"))
    scope = f"candidate:{cid}"
    required = [
        "live_action_scope_evidence_path", "live_action_scope_evidence_sha256",
        "cta_evidence_path", "cta_evidence_sha256",
    ]
    if not gate.require(candidate, required, scope):
        return
    evidence = verified_json(
        gate,
        candidate["live_action_scope_evidence_path"],
        candidate["live_action_scope_evidence_sha256"],
        scope + ":live_action_scope",
    )
    if evidence:
        if evidence.get("schema") != "celebrity-live-action-scope-evidence/v260928" or evidence.get("decision") != "pass":
            gate.fail("LIVE_ACTION_SCOPE_EVIDENCE_INVALID", scope, "schema/decision")
        bindings = {
            "candidate_id": cid,
            "source_sha256": str(candidate.get("source_sha256", "")).lower(),
            "source_in": float(candidate.get("source_in", 0.0)),
            "source_out": float(candidate.get("source_out", 0.0)),
        }
        if evidence.get("candidate_id") != bindings["candidate_id"] or str(evidence.get("source_sha256", "")).lower() != bindings["source_sha256"]:
            gate.fail("LIVE_ACTION_SCOPE_BINDING_MISMATCH", scope, "candidate/source")
        try:
            if abs(float(evidence.get("source_in")) - bindings["source_in"]) > 0.001 or abs(float(evidence.get("source_out")) - bindings["source_out"]) > 0.001:
                gate.fail("LIVE_ACTION_SCOPE_BINDING_MISMATCH", scope, "source interval")
        except (TypeError, ValueError):
            gate.fail("LIVE_ACTION_SCOPE_BINDING_MISMATCH", scope, "invalid source interval")
        allowed_domains = {"live_action_primary", "live_action_primary_with_game_inset"}
        frame_rows = evidence.get("frames") if isinstance(evidence.get("frames"), list) else []
        by_position = {row.get("position"): row for row in frame_rows if isinstance(row, dict)}
        for position in ("in", "mid", "out"):
            row = by_position.get(position)
            if not row:
                gate.fail("LIVE_ACTION_SCOPE_FRAME_MISSING", scope, position)
                continue
            if row.get("dominant_domain") not in allowed_domains or row.get("fullscreen_game") is not False or row.get("end_card") is not False or row.get("visual_cta") is not False:
                gate.fail("NON_LIVE_ACTION_CONTENT", scope, position)
            if row.get("dominant_person_id") != candidate.get("primary_person_id"):
                gate.fail("LIVE_ACTION_PRIMARY_PERSON_MISMATCH", scope, position)
            candidate_frame = candidate.get("frames", {}).get(position, {})
            if row.get("frame_path") != candidate_frame.get("path") or str(row.get("frame_sha256", "")).lower() != str(candidate_frame.get("sha256", "")).lower():
                gate.fail("LIVE_ACTION_FRAME_BINDING_MISMATCH", scope, position)
        first_game = evidence.get("first_fullscreen_game_time")
        if first_game is not None:
            try:
                if float(candidate.get("source_out", 0.0)) > float(first_game) + 0.001:
                    gate.fail("FULLSCREEN_GAME_BOUNDARY_CROSSED", scope, f"source_out={candidate.get('source_out')} first_game={first_game}")
            except (TypeError, ValueError):
                gate.fail("LIVE_ACTION_SCOPE_EVIDENCE_INVALID", scope, "first_fullscreen_game_time")
    cta = verified_json(
        gate,
        candidate["cta_evidence_path"],
        candidate["cta_evidence_sha256"],
        scope + ":cta",
    )
    if cta:
        if cta.get("schema") != "candidate-cta-evidence/v260928" or cta.get("candidate_id") != cid or cta.get("decision") != "pass":
            gate.fail("CTA_EVIDENCE_INVALID", scope, "schema/binding/decision")
        allow_spoken_cta = bool((policy.get("content_grounding_contract") or {}).get("allow_spoken_cta"))
        if (cta.get("spoken_cta") is True and not allow_spoken_cta) or cta.get("visual_cta") is not False or cta.get("end_card") is not False:
            gate.fail("CTA_OR_HARD_TEXT_CONFLICT", scope, "structured CTA evidence")


def validate_structured_identity(gate: "Gate", candidate: dict) -> None:
    cid = str(candidate.get("candidate_id", "<missing>"))
    scope = f"candidate:{cid}:identity"
    evidence = verified_json(gate, candidate.get("identity_evidence_path"), candidate.get("identity_evidence_sha256"), scope)
    if not evidence:
        return
    expected = {
        "candidate_id": cid,
        "primary_person_id": candidate.get("primary_person_id"),
        "boundary_open_person_id": candidate.get("boundary_open_person_id"),
        "boundary_close_person_id": candidate.get("boundary_close_person_id"),
    }
    if evidence.get("schema") != "canonical-person-identity-evidence/v260928" or evidence.get("decision") != "pass":
        gate.fail("IDENTITY_EVIDENCE_INVALID", scope, "schema/decision")
    for key, value in expected.items():
        if evidence.get(key) != value:
            gate.fail("IDENTITY_EVIDENCE_BINDING_MISMATCH", scope, key)


class Gate:
    def __init__(self) -> None:
        self.failures: list[dict] = []

    def fail(self, code: str, scope: str, detail: str) -> None:
        self.failures.append({"code": code, "scope": scope, "detail": detail})

    def require(self, obj: dict, fields: list[str], scope: str) -> bool:
        missing = [field for field in fields if field not in obj or obj[field] in (None, "")]
        if missing:
            self.fail("MISSING_REQUIRED_TRACEABILITY", scope, "missing=" + ",".join(missing))
            return False
        return True

    def verify_ref(self, path_value: object, hash_value: object, scope: str, code: str = "EVIDENCE_HASH_MISMATCH") -> bool:
        if not isinstance(path_value, str) or not isinstance(hash_value, str):
            self.fail("MISSING_REQUIRED_TRACEABILITY", scope, "evidence path/hash missing")
            return False
        path = Path(path_value)
        if not path.is_file():
            self.fail("MISSING_EVIDENCE_FILE", scope, str(path))
            return False
        actual = sha_file(path)
        if actual.lower() != hash_value.lower():
            self.fail(code, scope, f"expected={hash_value} actual={actual}")
            return False
        return True


def validate_candidate(gate: Gate, candidate: dict, sources: dict[str, dict], policy: dict, style: dict) -> str | None:
    cid = str(candidate.get("candidate_id", "<missing>"))
    scope = f"candidate:{cid}"
    required = [
        "candidate_id", "candidate_status", "capacity_input_eligible", "source_id",
        "source_sha256", "processing_stage", "source_in", "source_out", "speech_end",
        "source_in_frame", "speech_end_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den",
        "candidate_text", "candidate_text_sha256", "actual_asr_text", "actual_asr_path",
        "actual_asr_sha256", "first_voiced_source_time", "last_voiced_source_time",
        "opening_completion_status", "closing_completion_status", "boundary_status",
        "primary_person_id", "visible_person_ids", "boundary_open_person_id",
        "boundary_close_person_id", "identity_evidence_path", "identity_evidence_sha256",
        "semantic_cluster_id", "semantic_cluster_review_path", "semantic_cluster_review_sha256",
        "candidate_type", "allowed_editorial_roles", "frames", "cta_status", "hard_text_status",
    ]
    if not gate.require(candidate, required, scope):
        return None
    if candidate["candidate_status"] != "approved" or candidate["capacity_input_eligible"] is not True:
        gate.fail("UNAPPROVED_CANDIDATE", scope, "candidate is not approved capacity input")
    source = sources.get(candidate["source_id"])
    if not source:
        gate.fail("UNKNOWN_SOURCE", scope, candidate["source_id"])
    elif (candidate["source_sha256"].lower() != source["source_sha256"].lower()
          or candidate["processing_stage"] != source["processing_stage"]):
        gate.fail("SOURCE_BINDING_MISMATCH", scope, candidate["source_id"])
    if normalized_text_sha(candidate["candidate_text"]) != candidate["candidate_text_sha256"].lower():
        gate.fail("CANDIDATE_TEXT_HASH_MISMATCH", scope, "normalized text hash does not match")
    similarity = SequenceMatcher(None, normalize_text(candidate["candidate_text"]), normalize_text(candidate["actual_asr_text"])).ratio()
    if similarity < float(policy.get("min_actual_asr_text_similarity", 0.9)):
        gate.fail("ACTUAL_ASR_MISMATCH", scope, f"similarity={similarity:.4f}")
    gate.verify_ref(candidate["actual_asr_path"], candidate["actual_asr_sha256"], scope + ":actual_asr")
    gate.verify_ref(candidate["identity_evidence_path"], candidate["identity_evidence_sha256"], scope + ":identity")
    gate.verify_ref(candidate["semantic_cluster_review_path"], candidate["semantic_cluster_review_sha256"], scope + ":semantic_cluster")
    for name, item in candidate["frames"].items() if isinstance(candidate["frames"], dict) else []:
        if not isinstance(item, dict) or not gate.verify_ref(item.get("path"), item.get("sha256"), scope + f":frame:{name}"):
            continue
    if not isinstance(candidate["frames"], dict) or not {"in", "mid", "out"}.issubset(candidate["frames"]):
        gate.fail("MISSING_NATIVE_FRAME_EVIDENCE", scope, "in/mid/out frames required")
    person_values = [candidate["primary_person_id"], candidate["boundary_open_person_id"], candidate["boundary_close_person_id"]]
    if not isinstance(candidate["visible_person_ids"], list) or not candidate["visible_person_ids"]:
        gate.fail("UNKNOWN_CANONICAL_PERSON", scope, "visible_person_ids must be non-empty")
    else:
        person_values += candidate["visible_person_ids"]
    for person in person_values:
        if not valid_person_id(person):
            gate.fail("UNKNOWN_CANONICAL_PERSON", scope, str(person))
    if candidate["primary_person_id"] not in candidate["visible_person_ids"]:
        gate.fail("IDENTITY_CONTRACT_MISMATCH", scope, "primary person not in visible_person_ids")
    if candidate["boundary_open_person_id"] not in candidate["visible_person_ids"] or candidate["boundary_close_person_id"] not in candidate["visible_person_ids"]:
        gate.fail("IDENTITY_CONTRACT_MISMATCH", scope, "boundary person not in visible_person_ids")
    source_in, source_out = float(candidate["source_in"]), float(candidate["source_out"])
    first_voice, last_voice = float(candidate["first_voiced_source_time"]), float(candidate["last_voiced_source_time"])
    frame_values = [candidate[name] for name in ("source_in_frame", "speech_end_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den")]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in frame_values):
        gate.fail("FRAME_NATIVE_CONTRACT_FAILED", scope, "frame fields must be integers")
    else:
        frame_in, frame_speech_end, frame_out, fps_num, fps_den = frame_values
        if not (0 <= frame_in <= frame_speech_end < frame_out) or fps_num <= 0 or fps_den <= 0:
            gate.fail("FRAME_NATIVE_CONTRACT_FAILED", scope, "invalid frame order or fps")
        else:
            frame_seconds = fps_den / fps_num
            tolerance = frame_seconds + 0.001
            if abs(source_in - frame_in * frame_seconds) > tolerance or abs(float(candidate["speech_end"]) - frame_speech_end * frame_seconds) > tolerance or abs(source_out - frame_out * frame_seconds) > tolerance:
                gate.fail("FRAME_TIME_BINDING_MISMATCH", scope, "seconds metadata does not match decoded frame bounds")
    if not (source_in <= first_voice <= last_voice <= source_out):
        gate.fail("BOUNDARY_CROSSING_SPEECH", scope, f"source={source_in}-{source_out} voice={first_voice}-{last_voice}")
    if candidate["opening_completion_status"] != "complete":
        gate.fail("DEPENDENT_OPENING", scope, str(candidate["opening_completion_status"]))
    if candidate["closing_completion_status"] != "complete":
        gate.fail("OPEN_TAIL", scope, str(candidate["closing_completion_status"]))
    if candidate["boundary_status"] != "pass":
        gate.fail("BOUNDARY_CONTRACT_FAILED", scope, str(candidate["boundary_status"]))
    tail_bounds = style.get("natural_tail_seconds", {"min": 0.08, "max": 0.12})
    natural_tail = source_out - float(candidate["speech_end"])
    if not (float(tail_bounds.get("min", 0.08)) <= natural_tail <= float(tail_bounds.get("max", 0.12))):
        gate.fail("NATURAL_TAIL_VIOLATION", scope, f"tail={natural_tail:.6f}")
    if candidate["cta_status"] != "pass" or candidate["hard_text_status"] != "pass":
        gate.fail("CTA_OR_HARD_TEXT_CONFLICT", scope, f"cta={candidate['cta_status']} text={candidate['hard_text_status']}")
    if policy.get("enforce_structured_identity_evidence") is True:
        validate_structured_identity(gate, candidate)
    if policy.get("content_scope") == "celebrity_live_action_opening" or policy.get("enforce_live_action_scope_evidence") is True:
        validate_live_action_evidence(gate, candidate, policy)
    if policy.get("require_boundary_window_attestation") is True:
        if gate.require(candidate, ["boundary_window_evidence_path", "boundary_window_evidence_sha256"], scope + ":boundary_window"):
            window = verified_json(
                gate,
                candidate["boundary_window_evidence_path"],
                candidate["boundary_window_evidence_sha256"],
                scope + ":boundary_window",
            )
            if window and (
                window.get("schema") != "candidate-boundary-window-evidence/v260928"
                or window.get("candidate_id") != cid
                or window.get("decision") != "pass"
                or window.get("leading_foreign_frame") is not False
                or window.get("trailing_foreign_frame") is not False
                or window.get("leading_extra_audio") is not False
                or window.get("trailing_extra_audio") is not False
                or window.get("final_syllable_complete") is not True
            ):
                gate.fail("BOUNDARY_WINDOW_CONTRACT_FAILED", scope, "frame/audio window evidence")
    actual = normalize_text(candidate["actual_asr_text"])
    for phrase in policy.get("hard_excluded_connectors", []) + policy.get("hard_excluded_phrases", []):
        if normalize_text(str(phrase)) and normalize_text(str(phrase)) in actual:
            gate.fail("HARD_EXCLUDED_SPEECH", scope, str(phrase))
    if candidate["candidate_type"] in {"source_contained_dialogue_anchor", "source_contained_micro_dialogue"}:
        gate.require(candidate, ["internal_sequence_evidence_path", "internal_sequence_evidence_sha256"], scope)
        if candidate.get("internal_sequence_evidence_path"):
            gate.verify_ref(candidate["internal_sequence_evidence_path"], candidate["internal_sequence_evidence_sha256"], scope + ":internal_sequence")
    try:
        return candidate_fingerprint(candidate)
    except Exception as exc:
        gate.fail("CANDIDATE_FINGERPRINT_FAILED", scope, str(exc))
        return None


def validate_and_lock(request_path: Path, output_dir: Path) -> tuple[bool, dict, Path | None]:
    gate = Gate()
    request = load_json(request_path)
    package_root = Path(__file__).resolve().parent.parent
    package_skill_hash = sha_file(package_root.parents[1] / "skill/video-montage/SKILL.md")
    gate.require(request, ["schema", "package_id", "batch_id", "analysis_snapshot_id", "analysis_record_path", "analysis_record_sha256", "work_order_path", "work_order_sha256", "candidate_inventory_path", "candidate_inventory_sha256", "unique_plan_witness_path", "unique_plan_witness_sha256", "plans"], "request")
    if request.get("schema") != "semantic-batch-lock-request/v260928":
        gate.fail("SCHEMA_MISMATCH", "request", str(request.get("schema")))
    if request.get("package_id") != PACKAGE_ID:
        gate.fail("PACKAGE_BINDING_MISMATCH", "request", str(request.get("package_id")))
    refs = {}
    for key in ("analysis_record", "work_order", "candidate_inventory", "unique_plan_witness"):
        path = Path(str(request.get(key + "_path", "")))
        expected = str(request.get(key + "_sha256", ""))
        if gate.verify_ref(str(path), expected, key, "WORK_ORDER_HASH_MISMATCH" if key == "work_order" else "EVIDENCE_HASH_MISMATCH"):
            refs[key] = load_json(path)
    if len(refs) != 4:
        report = {
            "schema": REPORT_SCHEMA,
            "package_id": PACKAGE_ID,
            "created_at": now_iso(),
            "batch_id": request.get("batch_id"),
            "decision": "reject",
            "bindings": {"package_skill_sha256": package_skill_hash},
            "metrics": {"requested_plans": None, "supplied_plans": len(request.get("plans", [])) if isinstance(request.get("plans"), list) else 0, "validated_plans": 0, "total_segments": 0},
            "failures": gate.failures,
            "plans": [],
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(output_dir / "semantic_batch_gate_report_v7.json", report)
        return False, report, None
    work_order = refs.get("work_order", {})
    v20_registry = load_json(package_root / "references" / "wuzimu-v20-invalid-intervals.json")
    for item in audit_request_value(request, v20_registry):
        gate.fail(item["code"], item["scope"], item["detail"])
    inventory = refs.get("candidate_inventory", {})
    witness = refs.get("unique_plan_witness", {})
    policy = work_order.get("policy", {}) if isinstance(work_order.get("policy"), dict) else {}
    style = work_order.get("style_contract", {}) if isinstance(work_order.get("style_contract"), dict) else {}
    if work_order.get("schema") != "semantic-video-work-order/v260928" or work_order.get("package_id") != PACKAGE_ID:
        gate.fail("WORK_ORDER_CONTRACT_MISMATCH", "work_order", "schema/package mismatch")
    if work_order.get("package_skill_sha256", "").lower() != package_skill_hash:
        gate.fail("PACKAGE_SKILL_HASH_MISMATCH", "work_order", f"expected={package_skill_hash}")
    if work_order.get("batch_id") != request.get("batch_id"):
        gate.fail("BATCH_BINDING_MISMATCH", "work_order", "batch_id")
    requested_count = int(work_order.get("requested_outputs", -1))
    plans = request.get("plans", []) if isinstance(request.get("plans"), list) else []
    if requested_count < 1 or len(plans) != requested_count:
        gate.fail("PLAN_COUNT_MISMATCH", "batch", f"requested={requested_count} supplied={len(plans)}")
    if inventory.get("schema") != "semantic-candidate-inventory/v260928" or inventory.get("batch_id") != request.get("batch_id"):
        gate.fail("CANDIDATE_INVENTORY_CONTRACT_MISMATCH", "inventory", "schema/batch mismatch")
    if inventory.get("work_order_sha256", "").lower() != str(request.get("work_order_sha256", "")).lower():
        gate.fail("WORK_ORDER_HASH_MISMATCH", "inventory", "inventory bound to another work order")
    inventory_mode = policy.get("inventory_mode", "exhaustive")
    allowed_inventory_status = {"exhausted"} if inventory_mode == "exhaustive" else {"witness_sufficient", "exhausted"}
    if inventory.get("status") not in allowed_inventory_status:
        gate.fail("CANDIDATE_INVENTORY_INCOMPLETE", "inventory", f"mode={inventory_mode} status={inventory.get('status')}")
    sources = {}
    for source in inventory.get("sources", []) if isinstance(inventory.get("sources"), list) else []:
        if not gate.require(source, ["source_id", "source_path", "source_sha256", "processing_stage", "extraction_status", "extraction_evidence_path", "extraction_evidence_sha256"], "source"):
            continue
        allowed_source_status = {"exhausted"} if inventory_mode == "exhaustive" else {"partial", "witness_sufficient", "exhausted"}
        if source["extraction_status"] not in allowed_source_status:
            gate.fail("CANDIDATE_INVENTORY_INCOMPLETE", f"source:{source['source_id']}", source["extraction_status"])
        gate.verify_ref(source["extraction_evidence_path"], source["extraction_evidence_sha256"], f"source:{source['source_id']}:extraction")
        sources[source["source_id"]] = source
    candidates, fingerprints = {}, {}
    for candidate in inventory.get("candidates", []) if isinstance(inventory.get("candidates"), list) else []:
        cid = candidate.get("candidate_id")
        if not isinstance(cid, str) or cid in candidates:
            gate.fail("DUPLICATE_OR_MISSING_CANDIDATE_ID", "inventory", str(cid))
            continue
        candidates[cid] = candidate
        fp = validate_candidate(gate, candidate, sources, policy, style)
        if fp:
            fingerprints[cid] = fp
    style_required = ["profile", "target_duration_seconds", "shot_count", "performance_anchor_seconds", "response_or_detail_seconds", "min_performance_anchors"]
    gate.require(style, style_required, "style_contract")
    candidate_usage, cluster_usage, person_usage, exact_text_usage = Counter(), Counter(), Counter(), Counter()
    opening_visual_family_usage, opening_second_visual_pair_usage = Counter(), Counter()
    derived_plans = []
    for plan in plans:
        pid = str(plan.get("plan_id", "<missing>"))
        scope = f"plan:{pid}"
        if not gate.require(plan, ["plan_id", "plan_revision", "segments", "transitions"], scope):
            continue
        if not isinstance(plan.get("plan_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
            gate.fail("INVALID_PLAN_ID", scope, "plan_id must be a portable filename")
            continue
        segments = plan["segments"] if isinstance(plan["segments"], list) else []
        shot_range = style.get("shot_count", {})
        if not (int(shot_range.get("min", -1)) <= len(segments) <= int(shot_range.get("max", -1))):
            gate.fail("SHOT_COUNT_VIOLATION", scope, str(len(segments)))
        resolved, roles, text_hashes = [], [], []
        for expected_order, segment in enumerate(segments, 1):
            if not gate.require(segment, ["segment_order", "candidate_id", "editorial_role"], scope):
                continue
            if int(segment["segment_order"]) != expected_order:
                gate.fail("SEGMENT_ORDER_INVALID", scope, str(segment["segment_order"]))
            cid = segment["candidate_id"]
            candidate = candidates.get(cid)
            if not candidate or cid not in fingerprints:
                gate.fail("UNKNOWN_OR_INVALID_CANDIDATE", scope, str(cid))
                continue
            role = segment["editorial_role"]
            if role not in candidate.get("allowed_editorial_roles", []):
                gate.fail("EDITORIAL_ROLE_NOT_ALLOWED", scope, f"{cid}:{role}")
            duration = float(candidate["source_out"]) - float(candidate["source_in"])
            if role == "performance_anchor":
                bounds = style["performance_anchor_seconds"]
            elif role == "short_reaction" and isinstance(style.get("short_complete_reaction_seconds"), dict):
                bounds = style["short_complete_reaction_seconds"]
                if candidate.get("candidate_type") != "complete_short_reaction":
                    gate.fail("SHORT_REACTION_TYPE_REQUIRED", scope, cid)
            else:
                bounds = style["response_or_detail_seconds"]
            if not (float(bounds["min"]) <= duration <= float(bounds["max"])):
                gate.fail("SEGMENT_DURATION_VIOLATION", scope, f"{cid}:{duration:.3f}")
            candidate_usage[cid] += 1
            cluster_usage[candidate["semantic_cluster_id"]] += 1
            person_usage[candidate["primary_person_id"]] += 1
            exact_text_usage[candidate["candidate_text_sha256"]] += 1
            roles.append(role)
            text_hashes.append(candidate["candidate_text_sha256"])
            resolved.append({"segment_order": expected_order, "segment_id": segment.get("segment_id", f"{pid}-s{expected_order}"), "candidate_id": cid, "editorial_role": role, "candidate_fingerprint": fingerprints[cid], "semantic_cluster_id": candidate["semantic_cluster_id"], "source_path": candidate["source_path"], "source_sha256": candidate["source_sha256"], "processing_stage": candidate["processing_stage"], "source_in": candidate["source_in"], "speech_end": candidate["speech_end"], "source_out": candidate["source_out"], "speed": float(candidate.get("speed", 1.0)), "speaker_id": candidate["primary_person_id"], "primary_person_id": candidate["primary_person_id"], "boundary_open_person_id": candidate["boundary_open_person_id"], "boundary_close_person_id": candidate["boundary_close_person_id"], "opening_frame_visible_person_ids": candidate.get("opening_frame_visible_person_ids"), "opening_cast_evidence_path": candidate.get("opening_cast_evidence_path"), "opening_cast_evidence_sha256": candidate.get("opening_cast_evidence_sha256"), "spoken_product_mentions": candidate.get("spoken_product_mentions"), "boundary_cleanliness": candidate.get("boundary_cleanliness"), "purpose_contract": segment.get("purpose_contract"), "opening_question_exception": segment.get("opening_question_exception", candidate.get("opening_question_exception")), "text": candidate["candidate_text"], "duration": duration, "actual_asr_text": candidate["actual_asr_text"], "actual_asr_path": candidate["actual_asr_path"], "actual_asr_sha256": candidate["actual_asr_sha256"], "first_voiced_source_time": candidate["first_voiced_source_time"], "last_voiced_source_time": candidate["last_voiced_source_time"], "candidate_text_sha256": candidate["candidate_text_sha256"]})
            resolved[-1].update({name: candidate[name] for name in ("source_in_frame", "speech_end_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den")})
        for resolved_segment in resolved:
            resolved_segment["visible_person_ids"] = candidates[resolved_segment["candidate_id"]].get("visible_person_ids")
        if len(resolved) != len(segments):
            continue
        if policy.get("max_opening_visual_family_uses") is not None:
            opening_candidate = candidates[resolved[0]["candidate_id"]]
            opening_family = str(opening_candidate.get("opening_visual_family_id") or "")
            if not opening_family:
                gate.fail("OPENING_VISUAL_FAMILY_REQUIRED", scope, resolved[0]["candidate_id"])
            else:
                gate.verify_ref(opening_candidate.get("opening_visual_family_evidence_path"), opening_candidate.get("opening_visual_family_evidence_sha256"), scope + ":opening_visual_family")
                opening_visual_family_usage[opening_family] += 1
            if len(resolved) >= 2:
                second_candidate = candidates[resolved[1]["candidate_id"]]
                second_family = str(second_candidate.get("visual_family_id") or second_candidate.get("opening_visual_family_id") or "")
                if not second_family:
                    gate.fail("SECOND_VISUAL_FAMILY_REQUIRED", scope, resolved[1]["candidate_id"])
                elif opening_family:
                    opening_second_visual_pair_usage[(opening_family, second_family)] += 1
        if sum(1 for role in roles if role == "performance_anchor") < int(style["min_performance_anchors"]):
            gate.fail("MISSING_PERFORMANCE_ANCHOR", scope, str(roles))
        max_short_reactions = style.get("max_short_complete_reactions")
        if max_short_reactions is not None and sum(1 for role in roles if role == "short_reaction") > int(max_short_reactions):
            gate.fail("SHORT_REACTION_LIMIT_EXCEEDED", scope, str(roles))
        total_duration = sum(item["duration"] for item in resolved)
        duration_bounds = style["target_duration_seconds"]
        if not (float(duration_bounds["min"]) <= total_duration <= float(duration_bounds["max"])):
            gate.fail("PLAN_DURATION_VIOLATION", scope, f"{total_duration:.3f}")
        same_person_mode = policy.get("same_person_adjacency", "reject")
        for left, right in zip(resolved, resolved[1:]):
            if left["boundary_close_person_id"] == right["boundary_open_person_id"] and same_person_mode != "allow_with_visual_change_and_information_gain":
                gate.fail("ADJACENT_SAME_VISIBLE_PERSON", scope, f"{left['candidate_id']}->{right['candidate_id']}")
        transitions = plan["transitions"] if isinstance(plan["transitions"], list) else []
        if len(transitions) != max(0, len(resolved) - 1):
            gate.fail("TRANSITION_COUNT_MISMATCH", scope, str(len(transitions)))
        for index, transition in enumerate(transitions):
            if index + 1 >= len(resolved):
                break
            left, right = resolved[index], resolved[index + 1]
            required_transition = ["from_candidate_id", "to_candidate_id", "from_candidate_fingerprint", "to_candidate_fingerprint", "plan_revision", "decision", "reviewer_role", "relation", "information_gain", "review_artifact_path", "review_artifact_sha256", "risk_codes"]
            if not gate.require(transition, required_transition, scope + f":transition:{index + 1}"):
                continue
            if transition["from_candidate_id"] != left["candidate_id"] or transition["to_candidate_id"] != right["candidate_id"] or transition["from_candidate_fingerprint"] != left["candidate_fingerprint"] or transition["to_candidate_fingerprint"] != right["candidate_fingerprint"]:
                gate.fail("STALE_TRANSITION_BINDING", scope, str(index + 1))
            if transition["plan_revision"] != plan["plan_revision"]:
                gate.fail("STALE_TRANSITION_REVIEW", scope, str(index + 1))
            if transition["decision"] != "pass" or transition["reviewer_role"] != "independent_pre_render" or transition["risk_codes"]:
                gate.fail("SEMANTIC_TRANSITION_REJECTED", scope, str(index + 1))
            gate.verify_ref(transition["review_artifact_path"], transition["review_artifact_sha256"], scope + f":transition:{index + 1}:review")
            allowed_relations = policy.get("allowed_transition_relations")
            if isinstance(allowed_relations, list) and transition.get("relation") not in allowed_relations:
                gate.fail("INVALID_SEMANTIC_RELATION", scope, f"{index + 1}:{transition.get('relation')}")
            if policy.get("require_information_gain") is True:
                information_gain = normalize_text(str(transition.get("information_gain", "")))
                if len(information_gain) < int(policy.get("min_information_gain_chars", 4)):
                    gate.fail("MISSING_INFORMATION_GAIN", scope, str(index + 1))
            if policy.get("require_proposition_progress") is True:
                if not gate.require(transition, ["from_proposition_id", "to_proposition_id"], scope + f":transition:{index + 1}"):
                    continue
                if transition["from_proposition_id"] == transition["to_proposition_id"]:
                    gate.fail("REPEATED_PROPOSITION_WITHOUT_PROGRESS", scope, str(index + 1))
            same_person = left["boundary_close_person_id"] == right["boundary_open_person_id"]
            if same_person and same_person_mode == "allow_with_visual_change_and_information_gain":
                visual_change = transition.get("visual_change")
                if not isinstance(visual_change, dict):
                    gate.fail("SAME_PERSON_VISUAL_CHANGE_MISSING", scope, str(index + 1))
                else:
                    allowed_change_types = set(policy.get("allowed_same_person_visual_changes", ["scene", "shot_scale", "camera_angle", "costume", "action", "performance_state"]))
                    change_types = set(visual_change.get("change_types", [])) if isinstance(visual_change.get("change_types"), list) else set()
                    if visual_change.get("decision") != "pass" or not (allowed_change_types & change_types):
                        gate.fail("SAME_PERSON_VISUAL_CHANGE_REJECTED", scope, str(index + 1))
                    gate.verify_ref(visual_change.get("evidence_path"), visual_change.get("evidence_sha256"), scope + f":transition:{index + 1}:visual_change")
        allowed_return_reasons = set(policy.get("allowed_speaker_return_reasons", []))
        if allowed_return_reasons:
            for index in range(2, len(resolved)):
                first, middle, returned = resolved[index - 2], resolved[index - 1], resolved[index]
                if first["primary_person_id"] == returned["primary_person_id"] and first["primary_person_id"] != middle["primary_person_id"]:
                    transition = transitions[index - 1] if index - 1 < len(transitions) else {}
                    if transition.get("speaker_return_reason") not in allowed_return_reasons:
                        gate.fail("UNJUSTIFIED_SPEAKER_RETURN", scope, f"{first['primary_person_id']}->{middle['primary_person_id']}->{returned['primary_person_id']}")
        plan_policy = dict(policy)
        plan_policy["visual_shots"] = plan.get("visual_shots")
        for item in audit_v15_plan(pid, resolved, transitions, plan_policy):
            gate.fail(item["code"], item["scope"], item["detail"])
        ordered_signature = sha_bytes(canonical_bytes([[item["candidate_fingerprint"], item["editorial_role"]] for item in resolved]))
        route_signature = sha_bytes(canonical_bytes([[item["semantic_cluster_id"], item["editorial_role"]] for item in resolved]))
        derived_plans.append({"plan_id": pid, "plan_revision": plan["plan_revision"], "duration": round(total_duration, 6), "ordered_plan_signature": ordered_signature, "semantic_route_signature": route_signature, "segments": resolved, "transitions": transitions, "exact_text_hashes": text_hashes})
    total_segments = sum(len(plan["segments"]) for plan in derived_plans)
    for cid, count in candidate_usage.items():
        if count > int(policy.get("max_candidate_uses", 0)):
            gate.fail("CANDIDATE_REUSE_EXCEEDED", f"candidate:{cid}", str(count))
    for cluster, count in cluster_usage.items():
        if count > int(policy.get("max_semantic_cluster_uses", 0)):
            gate.fail("SEMANTIC_CLUSTER_REUSE_EXCEEDED", f"cluster:{cluster}", str(count))
    if policy.get("max_exact_text_uses") is not None:
        for text_hash, count in exact_text_usage.items():
            if count > int(policy["max_exact_text_uses"]):
                gate.fail("EXACT_TEXT_REUSE_EXCEEDED", f"text:{text_hash}", str(count))
    for code, scope, detail in opening_visual_reuse_failures(opening_visual_family_usage, opening_second_visual_pair_usage, policy.get("max_opening_visual_family_uses"), policy.get("max_opening_second_visual_pair_uses")):
        gate.fail(code, scope, detail)
    speaker_share_limit = policy.get("max_single_speaker_share", 0)
    if speaker_share_limit is not None:
        for person, count in person_usage.items():
            share = count / total_segments if total_segments else 1.0
            if share > float(speaker_share_limit):
                gate.fail("SPEAKER_SHARE_EXCEEDED", f"person:{person}", f"{share:.6f}")
    ordered_counts = Counter(plan["ordered_plan_signature"] for plan in derived_plans)
    route_counts = Counter(plan["semantic_route_signature"] for plan in derived_plans)
    for signature, count in ordered_counts.items():
        if count > int(policy.get("max_ordered_plan_signature_uses", 1)):
            gate.fail("DUPLICATE_ORDERED_PLAN_SEQUENCE", f"signature:{signature}", str(count))
    route_limit = policy.get("max_semantic_route_signature_uses", 1)
    if route_limit is not None:
        for signature, count in route_counts.items():
            if count > int(route_limit):
                gate.fail("DUPLICATE_SEMANTIC_ROUTE", f"route:{signature}", str(count))
    for i, left in enumerate(derived_plans):
        left_ids = {s["candidate_id"] for s in left["segments"]}
        left_text = "".join(s["actual_asr_text"] for s in left["segments"])
        for right in derived_plans[i + 1:]:
            overlap = len(left_ids & {s["candidate_id"] for s in right["segments"]})
            exact_overlap = len(set(left["exact_text_hashes"]) & set(right["exact_text_hashes"]))
            similarity = trigram_similarity(left_text, "".join(s["actual_asr_text"] for s in right["segments"]))
            pair = f"{left['plan_id']}|{right['plan_id']}"
            if overlap > int(policy.get("max_pairwise_candidate_overlap", 0)):
                gate.fail("PAIRWISE_CANDIDATE_OVERLAP_EXCEEDED", pair, str(overlap))
            if exact_overlap > int(policy.get("max_pairwise_exact_text_overlap", 0)):
                gate.fail("PAIRWISE_EXACT_TEXT_OVERLAP_EXCEEDED", pair, str(exact_overlap))
            if similarity > float(policy.get("max_pairwise_trigram_similarity", 1.0)):
                gate.fail("PAIRWISE_TEXT_SIMILARITY_EXCEEDED", pair, f"{similarity:.6f}")
    if witness:
        if witness.get("schema") != "video-montage-unique-plan-search-witness/v260928" or witness.get("complete") is not True:
            gate.fail("INVALID_GLOBAL_COMPLETION_WITNESS", "witness", "schema/complete")
        expected_ids = sorted(plan["plan_id"] for plan in derived_plans)
        if sorted(witness.get("plan_ids", [])) != expected_ids or int(witness.get("unique_plan_capacity", -1)) < requested_count:
            gate.fail("UNIQUE_PLAN_CAPACITY_SHORTAGE", "witness", "plan IDs or capacity do not attest request")
        if witness.get("work_order_sha256", "").lower() != str(request.get("work_order_sha256", "")).lower() or witness.get("candidate_inventory_sha256", "").lower() != str(request.get("candidate_inventory_sha256", "")).lower():
            gate.fail("GLOBAL_WITNESS_BINDING_MISMATCH", "witness", "work order/inventory")
        if inventory_mode == "witness_sufficient":
            required_reserve = math.ceil(requested_count * float(policy.get("min_replacement_plan_margin", 0.2)))
            if int(witness.get("replacement_plan_capacity", -1)) < required_reserve:
                gate.fail("REPLACEMENT_MARGIN_SHORTAGE", "witness", f"required={required_reserve} actual={witness.get('replacement_plan_capacity')}")
    report = {
        "schema": REPORT_SCHEMA,
        "package_id": PACKAGE_ID,
        "created_at": now_iso(),
        "batch_id": request.get("batch_id"),
        "decision": "pass" if not gate.failures else "reject",
        "bindings": {"package_skill_sha256": package_skill_hash, "analysis_snapshot_id": request.get("analysis_snapshot_id"), "analysis_record_sha256": request.get("analysis_record_sha256"), "work_order_sha256": request.get("work_order_sha256"), "candidate_inventory_sha256": request.get("candidate_inventory_sha256"), "unique_plan_witness_sha256": request.get("unique_plan_witness_sha256")},
        "metrics": {"requested_plans": requested_count, "supplied_plans": len(plans), "validated_plans": len(derived_plans), "total_segments": total_segments, "candidate_usage": dict(candidate_usage), "semantic_cluster_usage": dict(cluster_usage), "person_usage": dict(person_usage), "opening_visual_family_usage": dict(opening_visual_family_usage), "opening_second_visual_pair_usage": {f"{left}->{right}": count for (left, right), count in opening_second_visual_pair_usage.items()}, "ordered_plan_signature_usage": dict(ordered_counts), "semantic_route_signature_usage": dict(route_counts)},
        "failures": gate.failures,
        "plans": [{k: v for k, v in plan.items() if k != "exact_text_hashes"} for plan in derived_plans],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "semantic_batch_gate_report_v9.json"
    atomic_json(report_path, report)
    if gate.failures:
        return False, report, None
    report_hash = sha_file(report_path)
    locked_entries = []
    locked_dir = output_dir / "locked_plans"
    locked_dir.mkdir(parents=True, exist_ok=True)
    for plan in report["plans"]:
        output_spec = work_order.get("output_spec", {"width": 1080, "height": 1920, "fps": 30, "video_codec": "h264", "audio_codec": "aac", "audio_rate": 48000})
        render_work_order = {"schema": "portable-video-work-order/v260928", "task_id": request["batch_id"], "target_duration": style["target_duration_seconds"], "shot_count": style["shot_count"], "output_spec": output_spec, "edit_mode": "mixed_edit", "style_profile": style["profile"]}
        locked = {"schema": "semantic-locked-plan/v260928", "package_id": PACKAGE_ID, "batch_id": request["batch_id"], "gate_report_path": str(report_path.resolve()), "gate_report_sha256": report_hash, "work_order_path": str(Path(request["work_order_path"]).resolve()), "work_order_sha256": request["work_order_sha256"], "candidate_inventory_path": str(Path(request["candidate_inventory_path"]).resolve()), "candidate_inventory_sha256": request["candidate_inventory_sha256"], "work_order": render_work_order, **plan}
        path = locked_dir / f"{plan['plan_id']}.json"
        atomic_json(path, locked)
        locked_entries.append({"plan_id": plan["plan_id"], "path": str(path.resolve()), "sha256": sha_file(path), "ordered_plan_signature": plan["ordered_plan_signature"], "semantic_route_signature": plan["semantic_route_signature"]})
    index = {"schema": LOCK_SCHEMA, "package_id": PACKAGE_ID, "batch_id": request["batch_id"], "created_at": now_iso(), "request_path": str(request_path.resolve()), "request_sha256": sha_file(request_path), "gate_report_path": str(report_path.resolve()), "gate_report_sha256": report_hash, "work_order_sha256": request["work_order_sha256"], "candidate_inventory_sha256": request["candidate_inventory_sha256"], "plan_count": len(locked_entries), "plans": locked_entries}
    index_path = output_dir / "locked_plan_index_v9.json"
    atomic_json(index_path, index)
    return True, report, index_path


def verify_render_authorization(index_path: Path, gate_report_path: Path, plan_path: Path) -> tuple[bool, list[str]]:
    errors = []
    index, report, plan = load_json(index_path), load_json(gate_report_path), load_json(plan_path)
    if index.get("schema") != LOCK_SCHEMA or report.get("schema") != REPORT_SCHEMA or report.get("decision") != "pass":
        errors.append("gate/index schema or decision invalid")
    if index.get("gate_report_sha256") != sha_file(gate_report_path):
        errors.append("gate report hash mismatch")
    entry = next((item for item in index.get("plans", []) if item.get("plan_id") == plan.get("plan_id")), None)
    if not entry or Path(entry.get("path", "")).resolve() != plan_path.resolve() or entry.get("sha256") != sha_file(plan_path):
        errors.append("plan is not hash-bound in locked index")
    if plan.get("gate_report_sha256") != sha_file(gate_report_path):
        errors.append("plan gate binding mismatch")
    return not errors, errors


def verify_output(qc_path: Path, index_path: Path, output_path: Path) -> tuple[bool, dict]:
    gate = Gate()
    qc, index = load_json(qc_path), load_json(index_path)
    if qc.get("schema") != "semantic-output-qc-report/v260928" or qc.get("decision") != "pass":
        gate.fail("OUTPUT_QC_REJECTED", "output_qc", "schema/decision")
    if qc.get("locked_index_sha256", "").lower() != sha_file(index_path):
        gate.fail("LOCKED_INDEX_HASH_MISMATCH", "output_qc", "locked index")
    expected = {item["plan_id"]: item for item in index.get("plans", [])}
    outputs = qc.get("outputs", []) if isinstance(qc.get("outputs"), list) else []
    if len(outputs) != len(expected):
        gate.fail("OUTPUT_COUNT_MISMATCH", "output_qc", f"expected={len(expected)} actual={len(outputs)}")
    authorized = []
    review_intervals: list[tuple[datetime, datetime, float, str]] = []
    for item in outputs:
        pid = item.get("plan_id")
        scope = f"output:{pid}"
        required = ["plan_id", "plan_sha256", "export_path", "export_sha256", "actual_asr_path", "actual_asr_sha256", "cut_evidence_path", "cut_evidence_sha256", "sol_review_path", "sol_review_sha256", "decision", "checks"]
        if not gate.require(item, required, scope):
            continue
        entry = expected.get(pid)
        if not entry or item["plan_sha256"].lower() != entry["sha256"].lower():
            gate.fail("OUTPUT_PLAN_BINDING_MISMATCH", scope, str(pid))
        for prefix in ("export", "actual_asr", "cut_evidence", "sol_review"):
            gate.verify_ref(item[prefix + "_path"], item[prefix + "_sha256"], scope + ":" + prefix)
        try:
            sol_review = load_json(Path(item["sol_review_path"]))
        except Exception as exc:
            gate.fail("V18_SOL_REVIEW_UNREADABLE", scope, str(exc))
        else:
            for failure_item in audit_sol_review(sol_review, scope):
                gate.fail(failure_item["code"], failure_item["scope"], failure_item["detail"])
            try:
                review_start = datetime.fromisoformat(str(sol_review["review_started_at"]))
                review_end = datetime.fromisoformat(str(sol_review["review_completed_at"]))
                review_audio = float(sol_review["audio_duration_seconds"])
                review_intervals.append((review_start, review_end, review_audio, str(pid)))
            except Exception as exc:
                gate.fail("V18_SOL_REVIEW_INTERVAL_INVALID", scope, str(exc))
        if item["decision"] != "pass" or not isinstance(item["checks"], dict) or not item["checks"] or not all(value is True for value in item["checks"].values()):
            gate.fail("OUTPUT_QC_REJECTED", scope, "decision/checks")
        authorized.append({"plan_id": pid, "export_path": item.get("export_path"), "export_sha256": item.get("export_sha256")})
    if review_intervals:
        ordered = sorted(review_intervals, key=lambda row: row[0])
        total_audio = sum(row[2] for row in ordered)
        session_elapsed = (max(row[1] for row in ordered) - min(row[0] for row in ordered)).total_seconds()
        if session_elapsed + 0.05 < total_audio:
            gate.fail("V18_BATCH_REVIEW_TIME_TOO_SHORT", "output_qc", f"audio={total_audio:.3f},elapsed={session_elapsed:.3f}")
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                gate.fail("V18_OVERLAPPING_NORMAL_SPEED_REVIEWS", "output_qc", f"{previous[3]}->{current[3]}")
    manifest = {"schema": "semantic-release-authorization/v260928", "package_id": PACKAGE_ID, "created_at": now_iso(), "decision": "pass" if not gate.failures else "reject", "locked_index_path": str(index_path.resolve()), "locked_index_sha256": sha_file(index_path), "output_qc_path": str(qc_path.resolve()), "output_qc_sha256": sha_file(qc_path), "authorized_outputs": authorized if not gate.failures else [], "failures": gate.failures}
    atomic_json(output_path, manifest)
    return not gate.failures, manifest


def parse_artifacts(values: list[str]) -> dict:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"artifact must be name=path: {value}")
        name, raw_path = value.split("=", 1)
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        result[name] = {"path": str(path), "sha256": sha_file(path)}
    return result


def init_state(path: Path, task_id: str, source: str, work_order: Path) -> None:
    if path.exists():
        raise FileExistsError(path)
    state = {"schema": STATE_SCHEMA, "task_id": task_id, "phase": "INIT", "revision": 0, "updated_at": now_iso(), "source": source, "artifacts": {"work_order": {"path": str(work_order.resolve()), "sha256": sha_file(work_order)}}, "history": []}
    atomic_json(path, state)


def transition_state(path: Path, target: str, source: str, artifacts: dict) -> None:
    state = load_json(path)
    current = state.get("phase")
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid transition {current}->{target}")
    merged = {**state.get("artifacts", {}), **artifacts}
    missing = REQUIRED_PHASE_ARTIFACTS.get(target, set()) - set(merged)
    if missing:
        raise ValueError("missing required artifacts: " + ",".join(sorted(missing)))
    for name, ref in merged.items():
        if sha_file(Path(ref["path"])) != ref["sha256"]:
            raise ValueError(f"artifact changed: {name}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".bak-{stamp}")
    shutil.copy2(path, backup)
    state.setdefault("history", []).append({"from": current, "to": target, "at": now_iso(), "source": source})
    state.update({"phase": target, "revision": int(state.get("revision", 0)) + 1, "updated_at": now_iso(), "source": source, "artifacts": merged})
    atomic_json(path, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    gate_p = subs.add_parser("gate")
    gate_p.add_argument("--request", required=True, type=Path)
    gate_p.add_argument("--output-dir", required=True, type=Path)
    render_p = subs.add_parser("verify-render")
    render_p.add_argument("--locked-index", required=True, type=Path)
    render_p.add_argument("--gate-report", required=True, type=Path)
    render_p.add_argument("--plan", required=True, type=Path)
    output_p = subs.add_parser("verify-output")
    output_p.add_argument("--qc-report", required=True, type=Path)
    output_p.add_argument("--locked-index", required=True, type=Path)
    output_p.add_argument("--output", required=True, type=Path)
    init_p = subs.add_parser("init-state")
    init_p.add_argument("--state", required=True, type=Path)
    init_p.add_argument("--task-id", required=True)
    init_p.add_argument("--source", required=True)
    init_p.add_argument("--work-order", required=True, type=Path)
    trans_p = subs.add_parser("transition")
    trans_p.add_argument("--state", required=True, type=Path)
    trans_p.add_argument("--to", required=True)
    trans_p.add_argument("--source", required=True)
    trans_p.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            passed, report, index = validate_and_lock(args.request, args.output_dir)
            print(json.dumps({"decision": report["decision"], "failures": report["failures"], "locked_index": str(index) if index else None}, ensure_ascii=False))
            return 0 if passed else 2
        if args.command == "verify-render":
            passed, errors = verify_render_authorization(args.locked_index, args.gate_report, args.plan)
            print(json.dumps({"decision": "pass" if passed else "reject", "errors": errors}, ensure_ascii=False))
            return 0 if passed else 3
        if args.command == "verify-output":
            passed, manifest = verify_output(args.qc_report, args.locked_index, args.output)
            print(json.dumps({"decision": manifest["decision"], "failures": manifest["failures"]}, ensure_ascii=False))
            return 0 if passed else 4
        if args.command == "init-state":
            init_state(args.state, args.task_id, args.source, args.work_order)
            return 0
        if args.command == "transition":
            transition_state(args.state, args.to, args.source, parse_artifacts(args.artifact))
            return 0
    except Exception as exc:
        print(json.dumps({"decision": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 10
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
