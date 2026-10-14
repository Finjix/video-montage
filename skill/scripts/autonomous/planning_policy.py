"""Semantic-first editing contract; visual conclusions belong to The agent reviews."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = "semantic-continuity/v2"
CLEAN_FRAME_RANGE = (1296, 2160)
FINAL_FRAME_RANGE = (1080, 1800)


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


renderer = load("planning_frame_renderer", "scripts/semantic/portable_frame_renderer.py")
semantic = load("planning_content_gate", "scripts/semantic/content_gate.py")
CAST = load("planning_cast_policy", "scripts/autonomous/cast_policy.py")


def frame_errors(count: int, *, final: bool = False) -> list[str]:
    lower, upper = FINAL_FRAME_RANGE if final else CLEAN_FRAME_RANGE
    if type(count) is not int or not lower <= count <= upper:
        return [f"{'FINAL' if final else 'CLEAN'}_DURATION_VIOLATION:{count} frames not in {lower}..{upper} at 60 fps"]
    return []


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
        if not CAST.valid_visible(output, shot):
            errors.append(f"shot:{index + 1}:OTHER_OR_UNKNOWN_VISIBLE_PERSON")
        covered = [segment for segment_index, segment in enumerate(output["segments"], 1)
                   if segment_index in group.get("_source_segment_indexes", [])
                   and segment["source_in_frame"] < end and start < segment["source_out_frame_exclusive"]]
        if not covered or any(not set(shot.get("visible_person_ids", [])).issubset(segment["visible_person_ids"])
                              for segment in covered):
            errors.append(f"shot:{index + 1}:SOURCE_SEGMENT_CAST_MISMATCH")
        if any(not set(CAST.primary_people(output, shot)).issubset(CAST.primary_people(output, segment))
               for segment in covered):
            errors.append(f"shot:{index + 1}:SOURCE_SEGMENT_PRIMARY_PERSON_MISMATCH")
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
    if not errors:
        try:
            CAST.nameplate_windows(output, mapped)
        except ValueError as exc:
            errors.append(f"FIRST_APPEARANCE_TIMELINE_INVALID:{exc}")
    return mapped, errors


def audit_output(output: dict) -> list[str]:
    errors = CAST.output_errors(output)
    segments = output["segments"]
    for index, segment in enumerate(segments, 1):
        if float(segment.get("speed", 1.0)) != 1.0:
            errors.append(f"segment:{index}:NATURAL_SOURCE_SPEED_REQUIRED")
    timeline = renderer.planned_timeline(segments)
    errors.extend(frame_errors(timeline[-1]["output_out_frame_exclusive"]))
    _, shot_errors = shot_timeline(output)
    errors.extend(shot_errors)
    spoken = [{**segment, "duration": (segment["source_out_frame_exclusive"] - segment["source_in_frame"])
               * segment["source_fps_den"] / segment["source_fps_num"]} for segment in segments]
    errors.extend(f"{row['code']}:{row['scope']}:{row['detail']}" for row in semantic.audit_semantic_sequence(
        output["plan_id"], spoken, output.get("transitions")))
    return errors + grounded_transition_errors(output) + CAST.speaker_change_errors(output)


def specific_reason(value) -> bool:
    """Reject empty/general assent; editorial truth still requires agent review."""
    if not isinstance(value, str):
        return False
    normalized = semantic.normalize_text(value)
    return len(normalized) >= 8 and not re.fullmatch(
        r"(?:同一(?:个)?(?:游戏|人物|话题|主角)|语义连贯|逻辑连贯|衔接自然|增加(?:新的)?信息|接着介绍|继续介绍|通过审核)+",
        normalized)


def retained_quote(output: dict, reference, allowed: range) -> bool:
    if not isinstance(reference, dict) or type(reference.get("segment_index")) is not int:
        return False
    index = reference["segment_index"]
    quote = reference.get("quote")
    return (index in allowed and isinstance(quote, str) and len(semantic.normalize_text(quote)) >= 4
            and semantic.normalize_text(quote) in semantic.normalize_text(output["segments"][index - 1]["text"]))


def gameplay_bridge(quote: str) -> bool:
    # A product name or gameplay keyword alone does not introduce a method.
    return bool(re.search(r"怎么(?:玩|上手|开始)|如何(?:玩|上手|开始)|玩法|开局|一上来|进去(?:以后|之后|一看)|首先|先(?:让|把|收集|采集|砍|升级|建)",
                          semantic.normalize_text(quote)))


def grounded_transition_errors(output: dict) -> list[str]:
    errors = []
    segments = output["segments"]
    transitions = output.get("transitions")
    if not isinstance(transitions, list) or len(transitions) != len(segments) - 1:
        return ["TRANSITION_SCOPE_MISMATCH"]
    for index, transition in enumerate(transitions, 1):
        scope = f"transition:{index}"
        evidence = transition.get("content_evidence", {}) if isinstance(transition, dict) else {}
        if not isinstance(evidence, dict):
            errors.append(f"{scope}:RETAINED_PREMISE_REQUIRED")
            continue
        premises = evidence.get("premise_refs")
        if (not isinstance(premises, list) or not premises
                or any(not retained_quote(output, ref, range(1, index + 1)) for ref in premises)
                or len({json.dumps(ref, sort_keys=True, ensure_ascii=False) for ref in premises}) != len(premises)):
            errors.append(f"{scope}:RETAINED_PREMISE_REQUIRED")
        if (not specific_reason(evidence.get("entity_flow")) or not specific_reason(evidence.get("state_flow"))
                or semantic.normalize_text(evidence.get("entity_flow")) == semantic.normalize_text(evidence.get("state_flow"))):
            errors.append(f"{scope}:SPECIFIC_ENTITY_AND_STATE_FLOW_REQUIRED")
        bridge = evidence.get("bridge_ref")
        valid_bridge = retained_quote(output, bridge, range(index, index + 2))
        if bridge is not None and not valid_bridge:
            errors.append(f"{scope}:BRIDGE_NOT_IN_RETAINED_ADJACENT_SPEECH")
        left, right = segments[index - 1], segments[index]
        left_text = semantic.normalize_text(left["text"])
        discovery = bool(re.search(r"入坑|刷.{0,8}广告|全球.{0,12}(?:亿|都在玩)|(?:亿|千万|百万)人.{0,6}玩", left_text))
        purpose = right.get("purpose_contract")
        method = ((isinstance(purpose, dict) and purpose.get("function") == "mechanism")
                  or bool(re.search(r"砍树|获取资源|建造|升级熔炉|提升温度|吸引.{0,6}幸存者", semantic.normalize_text(right["text"]))))
        if discovery and method and not (valid_bridge and gameplay_bridge(bridge["quote"])):
            errors.append(f"{scope}:DISCOVERY_TO_GAMEPLAY_SPOKEN_BRIDGE_REQUIRED")
    return errors


def summary(output: dict) -> dict:
    timeline = renderer.planned_timeline(output["segments"])
    shots, errors = shot_timeline(output)
    if errors:
        raise ValueError("; ".join(errors))
    return {"plan_id": output["plan_id"], "cast_policy": output["cast_policy"],
            "protagonist_ids": CAST.selected(output), "supporting_person_ids": CAST.supporting(output),
            "nameplate_windows": CAST.nameplate_windows(output, shots),
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
    required = ("semantic_pass", "visual_shots_pass")
    reasons = ("semantic_reason", "cast_reason", "shot_reason")
    if (any(finding.get(key) is not True for key in required)
            or any(not isinstance(finding.get(key), str) or not finding[key].strip() for key in reasons)
            or not CAST.review_cast(output, finding)
            or type(finding.get("visual_shot_count")) is not int
            or finding["visual_shot_count"] != len(output["visual_shots"])):
        return [f"{output['plan_id']}:WHOLE_EDIT_SEMANTIC_CAST_SHOT_REVIEW_REQUIRED"]
    if finding.get("narrative_pass") is not True or not specific_reason(finding.get("narrative_reason")):
        return [f"{output['plan_id']}:WHOLE_EDIT_NARRATIVE_REVIEW_REQUIRED"]
    return []


def transition_review_errors(plan: dict, review: dict, evidence: dict | None = None) -> list[str]:
    outputs = {output["plan_id"]: output for output in plan["outputs"]}
    transitions = {(pid, index): transition for pid, output in outputs.items()
                   for index, transition in enumerate(output["transitions"], 1)}
    findings, errors = indexed_findings(review.get("transitions"), set(transitions),
                                        lambda row: (row.get("plan_id"), row.get("transition_index")))
    final_rows = {row["plan_id"]: row for row in evidence["results"]} if evidence is not None else {}
    for (pid, index), finding in findings.items():
        if (finding.get("continuity_pass") is not True
                or not isinstance(finding.get("reason"), str) or not finding["reason"].strip()):
            errors.append(f"{pid}:transition:{index}:GROUNDED_TRANSITION_REVIEW_REQUIRED")
        if (finding.get("relation_supported") is not True or finding.get("missing_premise") is not False
                        or not specific_reason(finding.get("reason"))):
            errors.append(f"{pid}:transition:{index}:RELATION_AND_PREMISE_REVIEW_REQUIRED")
        left, right = outputs[pid]["segments"][index - 1:index + 1]
        if left.get("speaker_id") != right.get("speaker_id") and (
                finding.get("speaker_change_pass") is not True
                or not specific_reason(finding.get("speaker_change_reason"))):
            errors.append(f"{pid}:transition:{index}:SPEAKER_CHANGE_REVIEW_REQUIRED")
        if evidence is not None:
            row = final_rows.get(pid, {})
            expected = final_transition_evidence(outputs[pid], row)
            if (row.get("transitions") != expected or not row.get("output", {}).get("sha256")
                    or finding.get("output_sha256") != row["output"]["sha256"]
                    or finding.get("speech_evidence_digest") != expected[index - 1]["speech_evidence_digest"]):
                errors.append(f"{pid}:transition:{index}:FINAL_TRANSITION_SPEECH_BINDING_REQUIRED")
    return errors


def final_transition_evidence(output: dict, row: dict) -> list[dict]:
    """Expose retained quotes for fresh final review, not an automatic approval."""
    speech = {key: row.get(key) for key in ("output", "asr", "mix_evidence", "speech_preserved")}
    fingerprint = hashlib.sha256(json.dumps(speech, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    return [{"plan_id": output["plan_id"], "transition_index": index,
             "relation": transition["relation"], "content_evidence": transition["content_evidence"],
             "speech_evidence_digest": fingerprint} for index, transition in enumerate(output["transitions"], 1)]


def plan_review_errors(plan: dict, evidence: dict, review: dict) -> list[str]:
    outputs = {output["plan_id"]: output for output in plan["outputs"]}
    findings, errors = indexed_findings(review.get("outputs"), set(outputs), lambda row: row.get("plan_id"))
    for pid, finding in findings.items():
        errors.extend(output_review_errors(outputs[pid], finding))
    errors.extend(transition_review_errors(plan, review))
    shots = {(pid, index): shot for pid, output in outputs.items()
             for index, shot in enumerate(output["visual_shots"], 1)}
    findings, scope_errors = indexed_findings(review.get("visual_shots"), set(shots),
                                             lambda row: (row.get("plan_id"), row.get("shot_index")))
    errors.extend(scope_errors)
    for (pid, index), finding in findings.items():
        if (finding.get("shot_pass") is not True
                or not CAST.review_cast(outputs[pid], finding, shots[(pid, index)]["visible_person_ids"],
                                        CAST.primary_people(outputs[pid], shots[(pid, index)]))
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
        segment = outputs[pid]["segments"][index - 1]
        cast_reason = "cast_reason"
        if (not CAST.review_cast(outputs[pid], finding, segment["visible_person_ids"],
                                 CAST.primary_people(outputs[pid], segment))
                or not candidate.get("continuous_visual")
                or finding.get("continuous_visual_sha256") != candidate.get("continuous_visual", {}).get("sha256")
                or not isinstance(finding.get(cast_reason), str) or not finding[cast_reason].strip()):
            errors.append(f"{pid}:segment:{index}:FULL_INTERVAL_CAST_REVIEW_REQUIRED")
    return errors
