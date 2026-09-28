from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from fractions import Fraction
from pathlib import Path

BASE_EVIDENCE_FIELDS = (
    "actual_asr_path", "identity_evidence_path", "semantic_cluster_review_path",
    "opening_cast_evidence_path", "boundary_window_evidence_path", "v20_evidence_bundle_path",
)
INDEPENDENT_FIELDS = (
    "v20_independent_review_path", "v20_alignment_path", "v20_visual_signal_scan_path",
)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def failure(code: str, scope: str, detail: str) -> dict:
    return {"code": code, "scope": scope, "detail": detail}


def first_expected_token(candidate: dict) -> str:
    text = str(candidate.get("candidate_text") or candidate.get("text") or "")
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    return normalized[:1]


def fingerprint_lineage(candidate: dict) -> dict | None:
    fingerprint = str(candidate.get("source_content_fingerprint") or "")
    match = re.search(r"([0-9a-fA-F]{64}).*?\[(\d+)\s*,\s*(\d+)\](?:.*?selected:(\d+)-(\d+))?", fingerprint)
    if not match:
        lineage = candidate.get("source_lineage")
        return lineage if isinstance(lineage, dict) else None
    original_start, original_end = int(match.group(2)), int(match.group(3))
    selected_start = int(match.group(4) or 0)
    selected_end = int(match.group(5) or (original_end - original_start))
    return {
        "original_source_path": candidate.get("original_source_path") or candidate.get("source_path"),
        "original_source_sha256": match.group(1).lower(),
        "original_source_in_frame": original_start + selected_start,
        "original_source_out_frame_exclusive": min(original_end, original_start + selected_end),
    }


def interval_hits(candidate: dict, entry: dict) -> bool:
    lineage = fingerprint_lineage(candidate) or {}
    identities = [
        {
            "path": str(candidate.get("source_path", "")),
            "hash": str(candidate.get("source_sha256", "")).lower(),
            "in": candidate.get("source_in_frame"),
            "out": candidate.get("source_out_frame_exclusive"),
        },
        {
            "path": str(lineage.get("original_source_path", "")),
            "hash": str(lineage.get("original_source_sha256", "")).lower(),
            "in": lineage.get("original_source_in_frame"),
            "out": lineage.get("original_source_out_frame_exclusive"),
        },
    ]
    entry_hashes = {str(value).lower() for field in ("source_sha256", "derived_source_sha256") for value in entry.get(field, [])}
    name_token = str(entry.get("source_name_contains", "")).casefold()
    matched = [item for item in identities if item["hash"] in entry_hashes or (name_token and name_token in Path(item["path"]).stem.casefold())]
    content_match = candidate.get("source_content_fingerprint") in entry.get("source_content_fingerprints", [])
    if not matched and not content_match:
        return False
    frame_ranges = entry.get("source_frame_ranges_exclusive", [])
    if frame_ranges:
        comparable = False
        for item in matched or identities:
            if item["in"] is None or item["out"] is None:
                continue
            comparable = True
            if any(int(item["in"]) < int(right) and int(item["out"]) > int(left) for left, right in frame_ranges):
                return True
        if comparable:
            return False
    guard = float(entry.get("guard", 0.35))
    left = float(candidate.get("source_in", 0.0))
    right = float(candidate.get("source_out", 0.0))
    return left < float(entry["end"]) + guard and right > float(entry["start"]) - guard


def audit_source_lineage(candidate: dict, task_root: Path, scope: str) -> tuple[list[dict], dict | None]:
    source_path = Path(str(candidate.get("source_path", "")))
    derived = source_path.suffix.casefold() in {".nut", ".mkv"} or any(token in str(candidate.get("processing_stage", "")).casefold() for token in ("clean", "derived", "repair"))
    if not derived:
        return [], fingerprint_lineage(candidate)
    path = Path(str(candidate.get("source_lineage_evidence_path", "")))
    expected_hash = str(candidate.get("source_lineage_evidence_sha256", "")).lower()
    if not path.is_file() or not inside(path, task_root) or sha(path) != expected_hash:
        return [failure("V20_DERIVED_SOURCE_LINEAGE_REQUIRED", scope, "current-task hash-bound source lineage evidence")], None
    evidence = read(path)
    required = ("original_source_path", "original_source_sha256", "original_source_in_frame", "original_source_out_frame_exclusive", "derived_source_sha256", "derived_in_frame", "derived_out_frame_exclusive")
    if evidence.get("schema") != "source-lineage-evidence/v260928" or evidence.get("candidate_id") != candidate.get("candidate_id") or any(evidence.get(name) is None for name in required):
        return [failure("V20_DERIVED_SOURCE_LINEAGE_BINDING_INVALID", scope, "schema/candidate/required fields")], None
    if str(evidence.get("derived_source_sha256", "")).lower() != str(candidate.get("source_sha256", "")).lower() or int(evidence.get("derived_in_frame")) != int(candidate.get("source_in_frame")) or int(evidence.get("derived_out_frame_exclusive")) != int(candidate.get("source_out_frame_exclusive")):
        return [failure("V20_DERIVED_SOURCE_LINEAGE_BINDING_INVALID", scope, "derived identity/frame range")], None
    if evidence.get("frame_mapping") not in {"one_to_one_monotonic", "declared_exact_frame_map"} or evidence.get("seconds_only_mapping") is not False:
        return [failure("V20_DERIVED_SOURCE_LINEAGE_MAPPING_INVALID", scope, "frame-native mapping required")], None
    return [], evidence


def validate_file_field(candidate: dict, field: str, task_root: Path, scope: str) -> list[dict]:
    value = candidate.get(field)
    path = Path(str(value or ""))
    failures = []
    if not value or not path.is_file() or not inside(path, task_root):
        return [failure("V20_CURRENT_TASK_EVIDENCE_REQUIRED", scope, field)]
    hash_field = field.replace("_path", "_sha256")
    if candidate.get(hash_field) and sha(path) != str(candidate.get(hash_field)).lower():
        failures.append(failure("V20_EVIDENCE_HASH_MISMATCH", scope, field))
    return failures


def audit_segment_start_action_context(candidate: dict, evidence: dict, task_id: str, scope: str) -> list[dict]:
    failures = []
    if evidence.get("schema") != "segment-start-action-context-evidence/v260928" or evidence.get("task_id") != task_id or evidence.get("candidate_id") != candidate.get("candidate_id"):
        return [failure("V20_SEGMENT_START_CONTEXT_BINDING_MISMATCH", scope, "schema/task/candidate")]
    original_hash = str(evidence.get("original_source_sha256") or "").lower()
    if len(original_hash) != 64 or any(char not in "0123456789abcdef" for char in original_hash):
        failures.append(failure("V20_SEGMENT_START_ORIGINAL_SOURCE_IDENTITY_REQUIRED", scope, "original_source_sha256"))
    if float(evidence.get("pre_context_seconds", 0) or 0) < 1.0 or float(evidence.get("opening_review_seconds", 0) or 0) < 1.5:
        failures.append(failure("V20_SEGMENT_START_CONTEXT_WINDOW_TOO_SHORT", scope, "requires >=1.0s before and >=1.5s after in-point"))
    frame_set_hash = str(evidence.get("reviewed_original_frame_set_sha256") or "").lower()
    if len(frame_set_hash) != 64 or any(char not in "0123456789abcdef" for char in frame_set_hash):
        failures.append(failure("V20_SEGMENT_START_ORIGINAL_FRAME_SET_REQUIRED", scope, "reviewed_original_frame_set_sha256"))
    rate = int(evidence.get("sample_rate", 0) or 0)
    first_sample = int(evidence.get("first_aligned_token_start_sample", -1))
    candidate_in_sample = int(evidence.get("candidate_in_sample", -1))
    fps_num = int(candidate.get("source_fps_num", 0) or 0)
    fps_den = int(candidate.get("source_fps_den", 0) or 0)
    if evidence.get("alignment_basis") != "expected_transcript_forced_alignment" or evidence.get("raw_waveform_energy_used_as_speech_start") is not False or not evidence.get("first_aligned_token"):
        failures.append(failure("V20_SEGMENT_START_TEXT_ALIGNMENT_REQUIRED", scope, "raw waveform energy is not speech-start evidence"))
    expected_token = first_expected_token(candidate)
    aligned_token = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(evidence.get("first_aligned_token") or ""))[:1]
    if not expected_token or aligned_token != expected_token:
        failures.append(failure("V20_SEGMENT_START_FIRST_TOKEN_MISMATCH", scope, f"expected={expected_token!r} aligned={aligned_token!r}"))
    if rate <= 0 or fps_num <= 0 or fps_den <= 0 or candidate_in_sample < 0 or first_sample < candidate_in_sample:
        failures.append(failure("V20_SEGMENT_START_SAMPLE_BINDING_INVALID", scope, "sample/fps values"))
    else:
        expected_in_sample = round(Fraction(int(candidate["source_in_frame"]) * fps_den * rate, fps_num))
        max_preroll = round(Fraction(3 * fps_den * rate, fps_num))
        if candidate_in_sample != expected_in_sample or first_sample - candidate_in_sample > max_preroll:
            failures.append(failure("V20_SEGMENT_START_EXCESS_SILENT_ACTION_PREROLL", scope, f"preroll_samples={first_sample-candidate_in_sample} max={max_preroll}"))
    if evidence.get("opening_action_complete") is not True or evidence.get("opening_semantic_complete") is not True:
        failures.append(failure("V20_SEGMENT_START_ACTION_OR_SEMANTIC_INCOMPLETE", scope, "segment-start action/semantic unit"))
    begins_mid = evidence.get("candidate_begins_inside_source_shot") is True
    complete_head = evidence.get("head_is_complete_independent_action") is True
    if begins_mid and not complete_head:
        failures.append(failure("V20_SEGMENT_START_ORPHAN_ACTION_TAIL", scope, "candidate starts mid-shot and head is not an independent complete action"))
    if evidence.get("orphan_head_shot") is True or evidence.get("user_rejected_action_match") is True:
        failures.append(failure("V20_SEGMENT_START_ORPHAN_ACTION_TAIL", scope, "orphan/user-rejected leading action"))
    return failures


def audit_pending_bundle(candidate: dict, bundle_path: Path, task_root: Path, task_id: str) -> list[dict]:
    bundle = read(bundle_path)
    cid = str(candidate.get("candidate_id"))
    scope = f"candidate:{cid}:pending_bundle"
    failures = []
    expected = {
        "schema": "candidate-boundary-evidence/v260928",
        "task_id": task_id,
        "candidate_id": cid,
        "source_sha256": str(candidate.get("source_sha256", "")).lower(),
        "source_content_fingerprint": candidate.get("source_content_fingerprint"),
        "source_in_frame": candidate.get("source_in_frame"),
        "source_out_frame_exclusive": candidate.get("source_out_frame_exclusive"),
        "source_fps_num": candidate.get("source_fps_num"),
        "source_fps_den": candidate.get("source_fps_den"),
    }
    for key, value in expected.items():
        actual = bundle.get(key)
        if key == "source_sha256":
            actual = str(actual or "").lower()
        if actual != value:
            failures.append(failure("V20_EVIDENCE_BINDING_MISMATCH", scope, key))
    if bundle.get("generator_decision") != "pending_review" or bundle.get("decision") != "pending_review" or bundle.get("reviewer") is not None or (bundle.get("audio_alignment") or {}).get("decision") != "pending_review":
        failures.append(failure("V20_MUTATED_GENERATOR_BUNDLE", scope, "generator evidence must remain immutable pending_review"))
    generator = bundle.get("generator") if isinstance(bundle.get("generator"), dict) else {}
    if generator.get("authority") != "evidence_only" or not generator.get("id"):
        failures.append(failure("V20_GENERATOR_PROVENANCE_REQUIRED", scope, "generator"))
    for side in ("in", "out"):
        window = (bundle.get("dense_windows") or {}).get(side, {})
        frames = window.get("frames") if isinstance(window.get("frames"), list) else []
        expected_count = int(window.get("expected_frame_count", 0) or 0)
        actual_count = int(window.get("actual_frame_count", 0) or 0)
        start = int(window.get("window_start_frame", -1))
        numbers = [int(item.get("source_frame_number", -2)) for item in frames]
        if expected_count < 61 or actual_count != expected_count or len(frames) != expected_count or numbers != list(range(start, start + expected_count)):
            failures.append(failure("V20_EXACT_DENSE_FRAME_SEQUENCE_REQUIRED", scope, side))
        if int(window.get("stable_run_required_frames", 0) or 0) < 60:
            failures.append(failure("V20_60_FRAME_MICROSHOT_REVIEW_REQUIRED", scope, side))
        for item in frames:
            path = Path(str(item.get("path", "")))
            if not path.is_file() or not inside(path, task_root) or sha(path) != str(item.get("sha256", "")).lower():
                failures.append(failure("V20_DENSE_FRAME_BINDING_INVALID", scope, side))
                break
        boundary = int(window.get("boundary_frame", -1))
        expected_boundary = int(candidate.get("source_in_frame" if side == "in" else "source_out_frame_exclusive", -2))
        if boundary != expected_boundary or int(window.get("boundary_index", -1)) != boundary - start:
            failures.append(failure("V20_BOUNDARY_FRAME_INDEX_MISMATCH", scope, side))
        pcm = Path(str(window.get("pcm_path", "")))
        if not pcm.is_file() or not inside(pcm, task_root) or sha(pcm) != str(window.get("pcm_sha256", "")).lower():
            failures.append(failure("V20_PCM_WINDOW_INVALID", scope, side))
    return failures


def audit_alignment(candidate: dict, alignment: dict, task_id: str, scope: str) -> list[dict]:
    failures = []
    cid = str(candidate.get("candidate_id"))
    source_sha = str(candidate.get("source_sha256", "")).lower()
    if alignment.get("schema") != "forced-boundary-alignment/v260928" or alignment.get("task_id") != task_id or alignment.get("candidate_id") != cid or str(alignment.get("source_sha256", "")).lower() != source_sha:
        return [failure("V20_ALIGNMENT_BINDING_MISMATCH", scope, "identity")]
    rate = int(alignment.get("sample_rate", 0) or 0)
    fps_num = int(candidate.get("source_fps_num", 0) or 0)
    fps_den = int(candidate.get("source_fps_den", 0) or 0)
    if rate < 8000 or fps_num <= 0 or fps_den <= 0:
        return [failure("V20_ALIGNMENT_SAMPLE_RATE_INVALID", scope, str(rate))]
    expected_start = round(Fraction(int(candidate["source_in_frame"]) * fps_den * rate, fps_num))
    expected_end = round(Fraction(int(candidate["source_out_frame_exclusive"]) * fps_den * rate, fps_num))
    selected_start = int(alignment.get("selected_start_sample", -2))
    selected_end = int(alignment.get("selected_end_sample_exclusive", -2))
    first_voiced = int(alignment.get("first_voiced_sample", -2))
    last_voiced = int(alignment.get("last_voiced_sample_exclusive", -2))
    if selected_start != expected_start or selected_end != expected_end or not selected_start <= first_voiced < last_voiced <= selected_end:
        failures.append(failure("V20_ALIGNMENT_SAMPLE_RANGE_MISMATCH", scope, "selected/voiced samples"))
        return failures
    leading = first_voiced - selected_start
    trailing = selected_end - last_voiced
    if alignment.get("leading_clean_gap_samples") != leading or alignment.get("trailing_clean_gap_samples") != trailing:
        failures.append(failure("V20_CLAIMED_GAP_NOT_DERIVED", scope, f"{leading}/{trailing}"))
    minimum = round(rate * 0.04)
    if leading < minimum or trailing < minimum:
        failures.append(failure("V20_MEASURED_CLEAN_GAP_TOO_SHORT", scope, f"{leading}/{trailing}<{minimum}"))
    maximum_preroll = round(Fraction(3 * fps_den * rate, fps_num))
    if alignment.get("alignment_basis") != "expected_transcript_forced_alignment" or alignment.get("raw_waveform_energy_used_as_speech_start") is not False or not alignment.get("first_aligned_token") or int(alignment.get("first_aligned_token_start_sample", -1)) != first_voiced:
        failures.append(failure("V20_EXPECTED_TEXT_FORCED_ALIGNMENT_REQUIRED", scope, "speech start must come from expected text, never raw waveform energy"))
    expected_token = first_expected_token(candidate)
    aligned_token = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(alignment.get("first_aligned_token") or ""))[:1]
    if not expected_token or aligned_token != expected_token:
        failures.append(failure("V20_FIRST_ALIGNED_TOKEN_MISMATCH", scope, f"expected={expected_token!r} aligned={aligned_token!r}"))
    if leading > maximum_preroll:
        failures.append(failure("V20_EXCESS_SILENT_ACTION_PREROLL", scope, f"{leading}>{maximum_preroll}"))
    if alignment.get("leading_residual_tokens") or alignment.get("trailing_residual_tokens") or alignment.get("non_speech_transients"):
        failures.append(failure("V20_RESIDUAL_OR_TRANSIENT_DETECTED", scope, "token/transient"))
    if alignment.get("final_syllable_complete") is not True:
        failures.append(failure("V20_FINAL_SYLLABLE_INCOMPLETE", scope, "final syllable"))
    return failures


def audit_independent_review(candidate: dict, bundle_path: Path, task_root: Path, task_id: str) -> list[dict]:
    cid = str(candidate.get("candidate_id"))
    scope = f"candidate:{cid}:independent_review"
    failures = []
    review_path = Path(str(candidate.get("v20_independent_review_path", "")))
    alignment_path = Path(str(candidate.get("v20_alignment_path", "")))
    visual_path = Path(str(candidate.get("v20_visual_signal_scan_path", "")))
    for path, field in ((review_path, "v20_independent_review_path"), (alignment_path, "v20_alignment_path"), (visual_path, "v20_visual_signal_scan_path")):
        if not path.is_file() or not inside(path, task_root) or sha(path) != str(candidate.get(field.replace("_path", "_sha256"), "")).lower():
            failures.append(failure("V20_INDEPENDENT_ARTIFACT_INVALID", scope, field))
    if failures:
        return failures
    review = read(review_path)
    alignment = read(alignment_path)
    visual = read(visual_path)
    if review.get("schema") != "candidate-independent-boundary-review/v260928" or review.get("decision") != "pass" or review.get("task_id") != task_id or review.get("candidate_id") != cid or review.get("pending_bundle_sha256") != sha(bundle_path):
        failures.append(failure("V20_INDEPENDENT_REVIEW_REQUIRED", scope, "review binding/decision"))
    reviewer = review.get("reviewer") if isinstance(review.get("reviewer"), dict) else {}
    if reviewer.get("role") != "independent_boundary_reviewer" or not reviewer.get("review_id"):
        failures.append(failure("V20_REVIEWER_PROVENANCE_REQUIRED", scope, "reviewer"))
    authority_path = Path(str(review.get("review_authority_path", "")))
    if not authority_path.is_file() or not inside(authority_path, task_root) or sha(authority_path) != str(review.get("review_authority_sha256", "")).lower():
        failures.append(failure("V20_REVIEW_AUTHORITY_INVALID", scope, "authority hash/path"))
    else:
        authority = read(authority_path)
        auth_reviewer = authority.get("reviewer") if isinstance(authority.get("reviewer"), dict) else {}
        if authority.get("schema") != "video-montage-independent-review-authority/v260928" or authority.get("decision") != "authorized" or authority.get("task_id") != task_id or authority.get("candidate_id") != cid or auth_reviewer.get("review_id") != reviewer.get("review_id") or auth_reviewer.get("role") != "independent_boundary_reviewer":
            failures.append(failure("V20_REVIEW_AUTHORITY_INVALID", scope, "authority content"))
    if visual.get("schema") != "video-montage-boundary-signal-scan/v260928" or visual.get("decision") != "pass" or visual.get("bundle_sha256") != sha(bundle_path):
        failures.append(failure("V20_VISUAL_SIGNAL_SCAN_FAILED", scope, "short head/tail shot"))
    failures.extend(audit_alignment(candidate, alignment, task_id, scope))
    if review.get("alignment_sha256") != sha(alignment_path) or review.get("visual_signal_scan_sha256") != sha(visual_path):
        failures.append(failure("V20_REVIEW_DEPENDENCY_HASH_MISMATCH", scope, "alignment/visual"))
    return failures


def audit_request_value(request: dict, registry: dict) -> list[dict]:
    failures = []
    work_order_path = Path(str(request.get("work_order_path", "")))
    inventory_path = Path(str(request.get("candidate_inventory_path", "")))
    if not work_order_path.is_file() or not inventory_path.is_file():
        return [failure("V20_REQUEST_REFERENCE_MISSING", "request", "work order or inventory")]
    task_root = work_order_path.parent.resolve()
    work_order = read(work_order_path)
    inventory = read(inventory_path)
    task_id = str(work_order.get("task_id") or request.get("batch_id") or "")
    candidates = {str(item.get("candidate_id")): item for item in inventory.get("candidates", []) if isinstance(item, dict)}
    plans = request.get("plans", []) if isinstance(request.get("plans"), list) else []
    requested_outputs = int(work_order.get("requested_outputs", 0) or 0)
    plan_ids = [str(plan.get("plan_id") or "") for plan in plans if isinstance(plan, dict)]
    if requested_outputs <= 0 or len(plans) != requested_outputs or len(set(plan_ids)) != requested_outputs or any(not plan_id for plan_id in plan_ids):
        failures.append(failure("V20_COMPLETE_BATCH_SCOPE_REQUIRED", "request", f"plans={len(plans)} requested={requested_outputs} unique={len(set(plan_ids))}"))
    referenced_ids = {
        str(segment.get("candidate_id") or "")
        for plan in plans if isinstance(plan, dict)
        for segment in plan.get("segments", []) if isinstance(segment, dict)
    }
    referenced_ids.discard("")
    for cid in sorted(referenced_ids - set(candidates)):
        failures.append(failure("V20_REFERENCED_CANDIDATE_MISSING_FROM_CURRENT_INVENTORY", "request", cid))
    invalid_ids = set()
    for cid, candidate in candidates.items():
        scope = f"candidate:{cid}"
        candidate_failures = []
        candidate_for_registry = dict(candidate)
        if not candidate.get("source_content_fingerprint"):
            candidate_failures.append(failure("V20_CONTENT_FINGERPRINT_REQUIRED", scope, "source_content_fingerprint"))
        if cid in referenced_ids:
            lineage_failures, lineage = audit_source_lineage(candidate, task_root, scope)
            candidate_failures.extend(lineage_failures)
            if lineage:
                candidate_for_registry["source_lineage"] = lineage
            field = "segment_start_action_context_evidence_path"
            candidate_failures.extend(validate_file_field(candidate, field, task_root, scope))
            context_path = Path(str(candidate.get(field, "")))
            if context_path.is_file() and inside(context_path, task_root) and sha(context_path) == str(candidate.get("segment_start_action_context_evidence_sha256", "")).lower():
                candidate_failures.extend(audit_segment_start_action_context(candidate, read(context_path), task_id, scope))
        for entry in registry.get("entries", []):
            if interval_hits(candidate_for_registry, entry):
                candidate_failures.append(failure("V20_USER_INVALID_INTERVAL", scope, str(entry.get("entry_id"))))
        for field in BASE_EVIDENCE_FIELDS + INDEPENDENT_FIELDS:
            candidate_failures.extend(validate_file_field(candidate, field, task_root, scope))
        bundle_path = Path(str(candidate.get("v20_evidence_bundle_path", "")))
        if bundle_path.is_file() and inside(bundle_path, task_root) and sha(bundle_path) == str(candidate.get("v20_evidence_bundle_sha256", "")).lower():
            candidate_failures.extend(audit_pending_bundle(candidate, bundle_path, task_root, task_id))
            candidate_failures.extend(audit_independent_review(candidate, bundle_path, task_root, task_id))
        else:
            candidate_failures.append(failure("V20_EVIDENCE_HASH_MISMATCH", scope, "pending bundle"))
        if candidate_failures:
            invalid_ids.add(cid)
            failures.extend(candidate_failures)
    for plan in plans:
        plan_id = str(plan.get("plan_id"))
        used = {str(segment.get("candidate_id")) for segment in plan.get("segments", [])}
        for cid in sorted(used & invalid_ids):
            failures.append(failure("V20_DEPENDENT_PLAN_INVALIDATED", f"plan:{plan_id}", cid))
    return failures


def audit_request(request_path: Path, registry_path: Path) -> dict:
    request = read(request_path)
    registry = read(registry_path)
    failures = audit_request_value(request, registry)
    work_order_path = Path(str(request.get("work_order_path", "")))
    requested_outputs = 0
    if work_order_path.is_file():
        requested_outputs = int(read(work_order_path).get("requested_outputs", 0) or 0)
    return {
        "schema": "video-montage-fail-closed-prelock-report/v260928",
        "decision": "pass" if not failures else "reject",
        "request_path": str(request_path.resolve()),
        "request_sha256": sha(request_path),
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": sha(registry_path),
        "requested_outputs": requested_outputs,
        "declared_plan_count": len(request.get("plans", [])),
        "failures": failures,
    }


def bound_artifact(container: dict, field: str, scope: str) -> list[dict]:
    item = container.get(field) if isinstance(container.get(field), dict) else {}
    path = Path(str(item.get("path", "")))
    if not path.is_file() or sha(path) != str(item.get("sha256", "")).lower():
        return [failure("V20_FINAL_BOUND_ARTIFACT_INVALID", scope, field)]
    return []


def audit_final(manifest_path: Path, post_qc_path: Path) -> dict:
    manifest, post = read(manifest_path), read(post_qc_path)
    failures = []
    if manifest.get("output_count") != len(manifest.get("results", [])) or manifest.get("output_count") in (None, 0):
        failures.append(failure("V20_FINAL_OUTPUT_COUNT_INVALID", "final", "manifest"))
    if post.get("schema") != "ffmpeg-post-encode-qc/v260928" or post.get("decision") != "pass":
        failures.append(failure("V20_POST_ENCODE_QC_REQUIRED", "final", "v20.5 full-boundary independent review with complete-turn speech-start alignment"))
    if post.get("delivery_manifest_sha256") != sha(manifest_path):
        failures.append(failure("V20_POST_ENCODE_BINDING_MISMATCH", "final", "manifest hash"))
    reviewer = post.get("reviewer") if isinstance(post.get("reviewer"), dict) else {}
    if reviewer.get("role") != "independent_post_encode_reviewer" or not reviewer.get("review_id"):
        failures.append(failure("V20_FINAL_REVIEWER_PROVENANCE_REQUIRED", "final", "reviewer"))
    for field in ("exact_cut_evidence", "machine_signal_gate", "independent_findings"):
        failures.extend(bound_artifact(post, field, "final"))
    signal = post.get("machine_signal_gate") if isinstance(post.get("machine_signal_gate"), dict) else {}
    if signal.get("decision") != "pass":
        failures.append(failure("V20_MACHINE_SIGNAL_GATE_REQUIRED", "final", "machine decision"))
    return {"schema": "video-montage-final-release-gate/v260928", "decision": "pass" if not failures else "reject", "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("audit-request")
    request.add_argument("--request", type=Path, required=True)
    request.add_argument("--registry", type=Path, required=True)
    request.add_argument("--output", type=Path, required=True)
    final = sub.add_parser("audit-final")
    final.add_argument("--manifest", type=Path, required=True)
    final.add_argument("--post-qc", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_request(args.request, args.registry) if args.command == "audit-request" else audit_final(args.manifest, args.post_qc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["decision"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
