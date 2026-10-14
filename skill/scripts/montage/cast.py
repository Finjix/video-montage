"""Selected celebrity identities and first-appearance nameplate contracts.

This module verifies declarations and evidence bindings, not face recognition.
"""
from __future__ import annotations

import re
from pathlib import Path

from montage.common import sha

POLICY = "selected-celebrities/v3"
DEFAULT_NAMEPLATE_FRAMES = 192
MIN_FINAL_NAMEPLATE_FRAMES = 48
MIN_FINAL_NAMEPLATE_GAP_FRAMES = 18


def person_id(value) -> bool:
    return (isinstance(value, str) and re.fullmatch(r"person:[a-z0-9][a-z0-9_-]*", value) is not None
            and value.split(":", 1)[1] not in {"unknown", "unidentified", "uncertain", "anonymous"})


def selected(output: dict) -> list[str]:
    return output.get("protagonist_ids", [])


def valid_people(value, allowed: list[str]) -> bool:
    return (isinstance(value, list) and bool(value) and all(person_id(p) for p in value)
            and len(value) == len(set(value)) and set(value).issubset(allowed))


def supporting(output: dict) -> list[str]:
    return output.get("supporting_person_ids", [])


def primary_people(output: dict, row: dict) -> list[str]:
    # Without supporting actors the visible selected celebrities are the subjects.
    return row.get("primary_person_ids", [p for p in row.get("visible_person_ids", []) if p in selected(output)])


def valid_visible(output: dict, row: dict) -> bool:
    people, extras = selected(output), supporting(output)
    if (not isinstance(extras, list) or any(not person_id(p) for p in extras)
            or len(extras) != len(set(extras)) or set(extras) & set(people)):
        return False
    visible = row.get("visible_person_ids")
    if not valid_people(visible, people + extras) or not set(visible) & set(people):
        return False
    # With supporting actors, the visual subject must be explicitly declared.
    if set(visible) & set(extras) and "primary_person_ids" not in row:
        return False
    return valid_people(primary_people(output, row), [p for p in visible if p in people])


def registry(order: dict, *, verify_files: bool = True) -> list[dict]:
    if order.get("cast_policy") != POLICY:
        raise ValueError("unknown or missing cast policy")
    rows = order.get("celebrities")
    if not isinstance(rows, list) or not rows:
        raise ValueError("celebrity registry required")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not person_id(row.get("person_id")) or row["person_id"] in seen:
            raise ValueError("unique canonical celebrity identities required")
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError("celebrity display name required")
        plate = row.get("nameplate")
        if (not isinstance(plate, dict) or not isinstance(plate.get("path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(plate.get("sha256", "")))):
            raise ValueError("hash-bound celebrity nameplate required")
        path = Path(plate["path"])
        if not path.is_absolute():
            raise ValueError("celebrity nameplate must have an absolute path")
        if verify_files and (not path.is_file() or sha(path) != plate["sha256"]):
            raise ValueError("celebrity nameplate changed or missing")
        seen.add(row["person_id"])
        result.append({"person_id": row["person_id"], "name": row["name"],
                       "nameplate": {"path": str(path.resolve()), "sha256": plate["sha256"]}})
    return result


def output_errors(output: dict) -> list[str]:
    people = selected(output)
    if not valid_people(people, people) or "protagonist_id" in output:
        return ["UNIQUE_SELECTED_PROTAGONISTS_REQUIRED"]
    extras = supporting(output)
    if (not isinstance(extras, list) or any(not person_id(p) for p in extras)
            or len(extras) != len(set(extras)) or set(extras) & set(people)):
        return ["VALID_DISTINCT_SUPPORTING_CAST_REQUIRED"]
    errors, observed, observed_extras = [], set(), set()
    for index, segment in enumerate(output.get("segments", []), 1):
        visible = segment.get("visible_person_ids")
        if not valid_visible(output, segment):
            errors.append(f"segment:{index}:OTHER_OR_UNKNOWN_VISIBLE_PERSON")
        else:
            observed.update(set(visible) & set(people))
            observed_extras.update(set(visible) & set(extras))
        if not person_id(segment.get("speaker_id")):
            errors.append(f"segment:{index}:CANONICAL_SPEAKER_REQUIRED")
    if observed != set(people):
        errors.append("SELECTED_CAST_NOT_ACTUALLY_VISIBLE")
    if observed_extras != set(extras):
        errors.append("SUPPORTING_CAST_NOT_ACTUALLY_VISIBLE")
    return errors


def review_cast(output: dict, finding: dict, visible=None, primary=None) -> bool:
    reason = finding.get("protagonist_primary_reason")
    if (finding.get("selected_cast_pass") is not True or finding.get("protagonist_primary_pass") is not True
            or not isinstance(reason, str) or len(reason.strip()) < 8):
        return False
    if visible is None:
        return (finding.get("supporting_person_ids", []) == supporting(output)
                and finding.get("protagonist_ids") == selected(output))
    return (finding.get("visible_person_ids") == visible
            and valid_visible(output, {"visible_person_ids": visible,
                                      **({"primary_person_ids": primary} if primary is not None else {})})
            and finding.get("primary_person_ids") == (primary if primary is not None else
                                                      [p for p in visible if p in selected(output)]))


def speaker_change_errors(output: dict) -> list[str]:
    errors = []
    for index, transition in enumerate(output.get("transitions", []), 1):
        left, right = output["segments"][index - 1:index + 1]
        if left.get("speaker_id") == right.get("speaker_id"):
            continue
        change = transition.get("content_evidence", {}).get("speaker_change", {})
        if (change.get("from_person_id") != left.get("speaker_id")
                or change.get("to_person_id") != right.get("speaker_id")
                or change.get("subject_relation") not in {"question_answer", "parallel_experience", "shared_topic"}
                or not isinstance(change.get("reason"), str) or len(change["reason"].strip()) < 8):
            errors.append(f"transition:{index}:DISTINCT_SPEAKER_SUBJECT_FLOW_REQUIRED")
        # Shared game topics cannot transfer one player's account to another.
        personal = lambda text: re.search(r"我(?!们)|本人|(?:这个|该|这)(?:账号|帐号|个号)", text) is not None
        if personal(left.get("text", "")) and personal(right.get("text", "")):
            if (change.get("subject_relation") != "parallel_experience"
                    or transition.get("relation") not in {
                        "speaker_experience", "contrast", "counterpoint", "explanation", "claim_support", "proof"}):
                errors.append(f"transition:{index}:PERSONAL_ACCOUNT_OR_RESULT_TRANSFER_FORBIDDEN")
    return errors


def nameplate_windows(output: dict, shots: list[dict]) -> list[dict]:
    """Use real 60 fps coverage; supporting actors do not trigger celebrity plates."""
    seen, windows = set(), []
    for index, shot in enumerate(shots):
        if not valid_visible(output, shot):
            raise ValueError("selected celebrity must remain the visual subject beside declared supporting actors")
        people = [p for p in shot["visible_person_ids"] if p in selected(output)]
        newcomers = [person for person in people if person not in seen]
        if len(newcomers) > 1:
            raise ValueError("simultaneous first appearances need separate introduction shots")
        for person in newcomers:
            if people != [person]:
                raise ValueError("first introduction requires a solo selected-celebrity shot")
            start, limit = shot["output_in_frame"], shot["output_out_frame_exclusive"]
            for next_shot in shots[index + 1:]:
                next_people = [p for p in next_shot["visible_person_ids"] if p in selected(output)]
                if (not valid_visible(output, next_shot) or next_people != [person]
                        or next_shot["output_in_frame"] != limit):
                    break
                limit = next_shot["output_out_frame_exclusive"]
            end = min(start + DEFAULT_NAMEPLATE_FRAMES, limit)
            if (end * 5 + 5) // 6 - (start * 5 + 5) // 6 < MIN_FINAL_NAMEPLATE_FRAMES:
                raise ValueError("first-appearance nameplate reads for less than 0.8 final seconds")
            windows.append({"person_id": person, "start_frame": start, "end_frame_exclusive": end})
        seen.update(people)
    if seen != set(selected(output)):
        raise ValueError("nameplate timeline must cover the actual selected cast")
    for current, following in zip(windows, windows[1:]):
        # ceil(clean * 5/6) maps visibility to the final 1.2x/60 fps timeline.
        next_final = (following["start_frame"] * 5 + 5) // 6
        last_allowed_final = next_final - MIN_FINAL_NAMEPLATE_GAP_FRAMES
        current["end_frame_exclusive"] = min(current["end_frame_exclusive"], last_allowed_final * 6 // 5)
        if ((current["end_frame_exclusive"] * 5 + 5) // 6
                - (current["start_frame"] * 5 + 5) // 6 < MIN_FINAL_NAMEPLATE_FRAMES):
            raise ValueError("nameplate blank gap would leave less than 0.8 final seconds; reselect first appearance")
    return windows


def verify_nameplates(output: dict, shots: list[dict], layers: list[dict], celebrities: list[dict]) -> None:
    expected = nameplate_windows(output, shots)
    assets = {row["person_id"]: row["nameplate"] for row in celebrities}
    if not isinstance(layers, list) or len(layers) != len(expected):
        raise ValueError("one first-appearance nameplate per selected celebrity required")
    for actual, window in zip(layers, expected):
        asset = assets.get(window["person_id"])
        if (not asset or any(actual.get(key) != value for key, value in window.items())
                or Path(actual.get("path", "")).resolve() != Path(asset["path"]).resolve()
                or actual.get("sha256") != asset["sha256"]):
            raise ValueError("nameplate identity, asset or first-appearance interval mismatch")
