"""Semantic-first editing contract; visual conclusions belong to Codex reviews."""
from __future__ import annotations

import importlib.util
import re
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = "semantic-continuity/v1"
CLEAN_FRAME_RANGE = (1296, 2160)
FINAL_FRAME_RANGE = (1080, 1800)


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


renderer = load("planning_frame_renderer", "scripts/semantic/scripts/portable_frame_renderer.py")
semantic = load("planning_semantics", "scripts/semantic/scripts/v15_content_gate.py")


def frame_errors(count: int, *, final: bool = False) -> list[str]:
    lower, upper = FINAL_FRAME_RANGE if final else CLEAN_FRAME_RANGE
    if type(count) is not int or not lower <= count <= upper:
        return [f"{'FINAL' if final else 'CLEAN'}_DURATION_VIOLATION:{count} frames not in {lower}..{upper} at 60 fps"]
    return []


def person_id(value) -> bool:
    return (isinstance(value, str) and re.fullmatch(r"person:[a-z0-9][a-z0-9_-]*", value) is not None
            and value.split(":", 1)[1] not in {"unknown", "unidentified", "uncertain", "anonymous"})


def shot_timeline(output: dict) -> tuple[list[dict], list[str]]:
    """Map native shot spans onto the renderer's coalesced 60 fps timeline."""
    groups = renderer.planned_timeline(output["segments"])
    shots = output.get("visual_shots")
    if not isinstance(shots, list) or not shots:
        return [], ["VISUAL_SHOT_TABLE_REQUIRED"]
    errors = []
    mapped = []
    group_index = 0
    cursor = groups[0]["source_in_frame"]
    previous = None
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict) or any(type(shot.get(key)) is not int for key in (
                "render_segment_index", "source_in_frame", "source_out_frame_exclusive")):
            errors.append(f"shot:{index + 1}:INTEGER_NATIVE_SHOT_BOUNDS_REQUIRED")
            continue
        if group_index >= len(groups):
            errors.append("VISUAL_SHOT_TABLE_EXTRA_RANGE")
            break
        group = groups[group_index]
        start, end = shot["source_in_frame"], shot["source_out_frame_exclusive"]
        if (shot["render_segment_index"] != group_index + 1 or shot.get("source_sha256") != group["source_sha256"]
                or start != cursor or not start < end <= group["source_out_frame_exclusive"]):
            errors.append(f"shot:{index + 1}:VISUAL_SHOT_COVERAGE_OR_LINEAGE_INVALID")
            continue
        shot_id = shot.get("source_visual_shot_id")
        if not isinstance(shot_id, str) or not shot_id.strip():
            errors.append(f"shot:{index + 1}:SOURCE_VISUAL_SHOT_ID_REQUIRED")
        expected_kind = "opening" if index == 0 else ("edit_cut" if start == group["source_in_frame"] else "source_camera_change")
        if shot.get("boundary_kind") != expected_kind:
            errors.append(f"shot:{index + 1}:REAL_SHOT_BOUNDARY_REQUIRED:{expected_kind}")
        if previous and (previous.get("source_sha256"), previous.get("source_visual_shot_id")) == (shot.get("source_sha256"), shot_id):
            errors.append(f"shot:{index + 1}:CONTINUOUS_SHOT_SPLIT")
        if shot.get("visible_person_ids") != [output.get("protagonist_id")]:
            errors.append(f"shot:{index + 1}:OTHER_OR_UNKNOWN_VISIBLE_PERSON")
        scale = Fraction(60 * group["source_fps_den"], group["source_fps_num"])
        left = group["output_in_frame"] + round(float((start - group["source_in_frame"]) * scale))
        right = (group["output_out_frame_exclusive"] if end == group["source_out_frame_exclusive"]
                 else group["output_in_frame"] + round(float((end - group["source_in_frame"]) * scale)))
        if right - left < 48:
            errors.append(f"shot:{index + 1}:FLASH_OR_MICROSHOT")
        mapped.append({**shot, "shot_index": index + 1, "output_in_frame": left,
                       "output_out_frame_exclusive": right, "output_frames": right - left})
        previous = shot
        cursor = end
        if end == group["source_out_frame_exclusive"]:
            group_index += 1
            if group_index < len(groups):
                cursor = groups[group_index]["source_in_frame"]
    if group_index != len(groups):
        errors.append("VISUAL_SHOT_TABLE_INCOMPLETE")
    if len(mapped) < 4:
        errors.append("AT_LEAST_FOUR_REAL_VISUAL_SHOTS_REQUIRED")
    if mapped and max(shot["output_frames"] for shot in mapped) - min(shot["output_frames"] for shot in mapped) > 180:
        errors.append("VISUAL_SHOT_DURATION_IMBALANCE:spread exceeds 3 seconds")
    return mapped, errors


def audit_output(output: dict) -> list[str]:
    errors = []
    protagonist = output.get("protagonist_id")
    if not person_id(protagonist):
        errors.append("CANONICAL_PROTAGONIST_REQUIRED")
    segments = output["segments"]
    for index, segment in enumerate(segments, 1):
        if float(segment.get("speed", 1.0)) != 1.0:
            errors.append(f"segment:{index}:NATURAL_SOURCE_SPEED_REQUIRED")
        if segment.get("visible_person_ids") != [protagonist]:
            errors.append(f"segment:{index}:OTHER_OR_UNKNOWN_VISIBLE_PERSON")
    timeline = renderer.planned_timeline(segments)
    errors.extend(frame_errors(timeline[-1]["output_out_frame_exclusive"]))
    _, shot_errors = shot_timeline(output)
    errors.extend(shot_errors)
    spoken = [{**segment, "duration": (segment["source_out_frame_exclusive"] - segment["source_in_frame"])
               * segment["source_fps_den"] / segment["source_fps_num"]} for segment in segments]
    errors.extend(f"{row['code']}:{row['scope']}:{row['detail']}" for row in semantic.audit_semantic_sequence(
        output["plan_id"], spoken, output.get("transitions")))
    return errors


def summary(output: dict) -> dict:
    timeline = renderer.planned_timeline(output["segments"])
    shots, errors = shot_timeline(output)
    if errors:
        raise ValueError("; ".join(errors))
    return {"plan_id": output["plan_id"], "protagonist_id": output["protagonist_id"],
            "clean_frames": timeline[-1]["output_out_frame_exclusive"],
            "visual_shot_count": len(shots), "visual_shots": shots}


def render_errors(output: dict, rendered: dict, actual_frames: int) -> list[str]:
    timeline = renderer.planned_timeline(output["segments"])
    errors = frame_errors(actual_frames)
    if (actual_frames != timeline[-1]["output_out_frame_exclusive"]
            or rendered.get("actual_output_frames") != actual_frames
            or rendered.get("expected_output_frames") != actual_frames):
        errors.append("CLEAN_RENDER_FRAME_COUNT_CHANGED")
    recorded = rendered.get("segments", [])
    fields = ("source_sha256", "source_in_frame", "source_out_frame_exclusive", "source_fps_num",
              "source_fps_den", "expected_output_frames")
    if (len(recorded) != len(timeline)
            or any(any(observed.get(field) != expected[field] for field in fields)
                   for observed, expected in zip(recorded, timeline))):
        errors.append("CLEAN_RENDER_LINEAGE_CHANGED")
    return errors


def indexed_findings(rows, expected: set, key) -> tuple[dict, list[str]]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {}, ["REVIEW_SCOPE_MISMATCH"]
    actual = [key(row) for row in rows]
    if any(isinstance(item, tuple) and (len(item) != 2 or type(item[1]) is not int) for item in actual):
        return {}, ["REVIEW_SCOPE_MISMATCH"]
    # Wrong/unhashable IDs are an invalid review rather than a scope bypass.
    try:
        if len(actual) != len(expected) or set(actual) != expected:
            return {}, ["REVIEW_SCOPE_MISMATCH"]
        return dict(zip(actual, rows)), []
    except TypeError:
        return {}, ["REVIEW_SCOPE_MISMATCH"]


def output_review_errors(output: dict, finding: dict) -> list[str]:
    required = ("semantic_pass", "protagonist_only_pass", "visual_shots_pass")
    reasons = ("semantic_reason", "protagonist_reason", "shot_reason")
    if (any(finding.get(key) is not True for key in required)
            or any(not isinstance(finding.get(key), str) or not finding[key].strip() for key in reasons)
            or finding.get("protagonist_id") != output["protagonist_id"]
            or type(finding.get("visual_shot_count")) is not int
            or finding["visual_shot_count"] != len(output["visual_shots"])):
        return [f"{output['plan_id']}:WHOLE_EDIT_SEMANTIC_CAST_SHOT_REVIEW_REQUIRED"]
    return []


def plan_review_errors(plan: dict, evidence: dict, review: dict) -> list[str]:
    outputs = {output["plan_id"]: output for output in plan["outputs"]}
    findings, errors = indexed_findings(review.get("outputs"), set(outputs), lambda row: row.get("plan_id"))
    for pid, finding in findings.items():
        errors.extend(output_review_errors(outputs[pid], finding))
    transitions = {(pid, index): transition for pid, output in outputs.items()
                   for index, transition in enumerate(output["transitions"], 1)}
    findings, scope_errors = indexed_findings(review.get("transitions"), set(transitions),
                                             lambda row: (row.get("plan_id"), row.get("transition_index")))
    errors.extend(scope_errors)
    for key, finding in findings.items():
        if finding.get("continuity_pass") is not True or not isinstance(finding.get("reason"), str) or not finding["reason"].strip():
            errors.append(f"{key}:GROUNDED_TRANSITION_REVIEW_REQUIRED")
    shots = {(pid, index): shot for pid, output in outputs.items()
             for index, shot in enumerate(output["visual_shots"], 1)}
    findings, scope_errors = indexed_findings(review.get("visual_shots"), set(shots),
                                             lambda row: (row.get("plan_id"), row.get("shot_index")))
    errors.extend(scope_errors)
    for (pid, index), finding in findings.items():
        if (finding.get("shot_pass") is not True or finding.get("protagonist_only_pass") is not True
                or finding.get("visible_person_ids") != [outputs[pid]["protagonist_id"]]
                or finding.get("source_visual_shot_id") != shots[(pid, index)]["source_visual_shot_id"]
                or any(not isinstance(finding.get(key), str) or not finding[key].strip() for key in ("reason", "boundary_reason"))):
            errors.append(f"{pid}:shot:{index}:REAL_SHOT_AND_CAST_REVIEW_REQUIRED")
    expected_candidates = {(pid, index) for pid, output in outputs.items()
                           for index in range(1, len(output["segments"]) + 1)}
    candidates, candidate_errors = indexed_findings(evidence.get("results"), expected_candidates,
                                                   lambda row: (row.get("plan_id"), row.get("segment_index")))
    errors.extend(candidate_errors)
    if candidate_errors:
        return errors
    findings, scope_errors = indexed_findings(review.get("segments"), expected_candidates,
                                             lambda row: (row.get("plan_id"), row.get("segment_index")))
    errors.extend(scope_errors)
    for (pid, index), finding in findings.items():
        candidate = candidates[(pid, index)]
        if (finding.get("protagonist_only_pass") is not True
                or finding.get("visible_person_ids") != [outputs[pid]["protagonist_id"]]
                or not candidate.get("continuous_visual")
                or finding.get("continuous_visual_sha256") != candidate.get("continuous_visual", {}).get("sha256")
                or not isinstance(finding.get("protagonist_reason"), str) or not finding["protagonist_reason"].strip()):
            errors.append(f"{pid}:segment:{index}:FULL_INTERVAL_CAST_REVIEW_REQUIRED")
    return errors
