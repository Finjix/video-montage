#!/usr/bin/env python3
"""Fail closed unless every rendered segment is bound to decoded source frames."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_INTEGER_FIELDS = (
    "source_in_frame",
    "speech_end_frame",
    "source_out_frame_exclusive",
    "source_fps_num",
    "source_fps_den",
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_segment(segment: dict, index: int) -> list[str]:
    errors: list[str] = []
    prefix = f"segment[{index}]"
    for field in REQUIRED_INTEGER_FIELDS:
        value = segment.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{prefix}:{field}:required_integer")
    if errors:
        return errors
    start = segment["source_in_frame"]
    speech_end = segment["speech_end_frame"]
    end = segment["source_out_frame_exclusive"]
    fps_num = segment["source_fps_num"]
    fps_den = segment["source_fps_den"]
    if not (0 <= start <= speech_end < end):
        errors.append(f"{prefix}:invalid_frame_order")
    if fps_num <= 0 or fps_den <= 0:
        errors.append(f"{prefix}:invalid_source_fps")
    if not str(segment.get("source_path") or "").strip():
        errors.append(f"{prefix}:source_path:required")
    if float(segment.get("speed", 1.0)) <= 0:
        errors.append(f"{prefix}:speed:invalid")
    if float(segment.get("video_hold", 0.0) or 0.0) > 0:
        errors.append(f"{prefix}:video_hold:forbidden")
    return errors


def _overlaps(left: int, right: int, ranges: list) -> bool:
    return any(left <= int(item[1]) and right >= int(item[0]) for item in ranges if isinstance(item, list) and len(item) == 2)


def validate_internal_silence_compactions(plan: dict, plan_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    segments = {str(item.get("segment_id") or ""): item for item in plan.get("segments", []) if isinstance(item, dict)}
    records = plan.get("internal_silence_compactions", [])
    if records is None:
        records = []
    if not isinstance(records, list):
        return ["internal_silence_compactions:must_be_list"]
    matched_pairs: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        prefix = f"internal_silence_compactions[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix}:must_be_object")
            continue
        left_id, right_id = str(record.get("left_segment_id") or ""), str(record.get("right_segment_id") or "")
        left, right = segments.get(left_id), segments.get(right_id)
        if not left or not right:
            errors.append(f"{prefix}:segment_binding_invalid")
            continue
        matched_pairs.add((left_id, right_id))
        frame_range = record.get("deleted_source_frame_range_inclusive")
        if not isinstance(frame_range, list) or len(frame_range) != 2 or not all(isinstance(value, int) and not isinstance(value, bool) for value in frame_range):
            errors.append(f"{prefix}:deleted_source_frame_range_inclusive:required_integer_pair")
            continue
        delete_start, delete_end = frame_range
        same_source = str(left.get("source_path")) == str(right.get("source_path")) and str(left.get("source_sha256", "")).lower() == str(right.get("source_sha256", "")).lower()
        same_timing = left.get("source_fps_num") == right.get("source_fps_num") and left.get("source_fps_den") == right.get("source_fps_den") and float(left.get("speed", 1.0)) == float(right.get("speed", 1.0))
        if not same_source or not same_timing:
            errors.append(f"{prefix}:same_source_timing_required")
        if int(left.get("source_out_frame_exclusive", -1)) != delete_start or int(right.get("source_in_frame", -1)) != delete_end + 1 or delete_start > delete_end:
            errors.append(f"{prefix}:deleted_range_does_not_match_split_segments")
        if record.get("user_authorized") is not True or not str(record.get("authorization_reference") or "").strip():
            errors.append(f"{prefix}:current_user_authorization_required")
        evidence_path = Path(str(record.get("alignment_evidence_path") or ""))
        if not evidence_path.is_absolute() and plan_root is not None:
            evidence_path = plan_root / evidence_path
        if not evidence_path.is_file() or sha(evidence_path) != str(record.get("alignment_evidence_sha256", "")).lower():
            errors.append(f"{prefix}:alignment_evidence_invalid")
            continue
        evidence = load_json(evidence_path)
        if evidence.get("schema") != "internal-silence-alignment-evidence/v260928" or evidence.get("decision") != "pass":
            errors.append(f"{prefix}:alignment_evidence_decision_invalid")
        if str(evidence.get("source_sha256", "")).lower() != str(left.get("source_sha256", "")).lower() or evidence.get("deleted_source_frame_range_inclusive") != frame_range:
            errors.append(f"{prefix}:alignment_source_or_range_mismatch")
        previous_end = int(evidence.get("previous_token_end_frame", -1))
        next_start = int(evidence.get("next_token_start_frame", -1))
        if delete_start - previous_end < 3:
            errors.append(f"{prefix}:previous_final_syllable_tail_under_3_frames")
        preroll = next_start - int(right.get("source_in_frame", -1))
        if not 0 <= preroll <= 3:
            errors.append(f"{prefix}:next_first_token_preroll_out_of_range")
        if evidence.get("alignment_basis") != "expected_transcript_forced_alignment" or evidence.get("raw_waveform_energy_used_as_speech_start") is not False:
            errors.append(f"{prefix}:forced_expected_text_alignment_required")
        if evidence.get("tokens_intersecting_deleted_range") or evidence.get("non_speech_transients_in_deleted_range"):
            errors.append(f"{prefix}:deleted_range_contains_token_or_transient")
        protected = evidence.get("protected_spoken_frame_ranges_inclusive", [])
        if _overlaps(delete_start, delete_end, protected):
            errors.append(f"{prefix}:deleted_range_overlaps_protected_speech")
        if evidence.get("previous_final_syllable_complete") is not True or evidence.get("next_first_token_complete") is not True:
            errors.append(f"{prefix}:adjacent_speech_incomplete")
        right_text = str(right.get("text") or "").lstrip()
        if right_text.startswith(("但", "但是", "不过", "所以", "然后", "接着")) and evidence.get("connector_antecedent_preserved") is not True:
            errors.append(f"{prefix}:connector_antecedent_not_preserved")
    transitions = plan.get("transitions", []) if isinstance(plan.get("transitions"), list) else []
    ordered = [item for item in plan.get("segments", []) if isinstance(item, dict)]
    for index, transition in enumerate(transitions):
        if not isinstance(transition, dict) or "silence_removal" not in str(transition.get("relation") or ""):
            continue
        if index + 1 >= len(ordered):
            errors.append(f"transition[{index}]:silence_removal_segment_binding_invalid")
            continue
        pair = (str(ordered[index].get("segment_id") or ""), str(ordered[index + 1].get("segment_id") or ""))
        if pair not in matched_pairs:
            errors.append(f"transition[{index}]:undeclared_internal_silence_compaction")
    return errors


def validate_plan_value(plan: dict, plan_root: Path | None = None) -> list[str]:
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["segments:required_nonempty_list"]
    errors: list[str] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segment[{index}]:must_be_object")
            continue
        errors.extend(validate_segment(segment, index))
    errors.extend(validate_internal_silence_compactions(plan, plan_root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    errors = validate_plan_value(load_json(args.plan), args.plan.parent.resolve())
    report = {
        "schema": "semantic-frame-plan-gate/v260928",
        "decision": "pass" if not errors else "reject",
        "render_authority": "decoded_source_frame_indexes_only",
        "seconds_only_fallback": False,
        "plan_path": str(args.plan.resolve()),
        "errors": errors,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
