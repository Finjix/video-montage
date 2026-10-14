"""Fail closed unless every rendered segment is bound to decoded source frames."""
from __future__ import annotations

import math
from pathlib import Path

from montage.media import audio_balance

REQUIRED_INTEGER_FIELDS = ("source_in_frame", "speech_end_frame", "source_out_frame_exclusive",
                           "source_fps_num", "source_fps_den")


def validate_segment(segment: dict, index: int) -> list[str]:
    prefix = f"segment[{index}]"
    errors = [f"{prefix}:{field}:required_integer" for field in REQUIRED_INTEGER_FIELDS
              if isinstance(segment.get(field), bool) or not isinstance(segment.get(field), int)]
    if errors:
        return errors
    if not 0 <= segment["source_in_frame"] <= segment["speech_end_frame"] < segment["source_out_frame_exclusive"]:
        errors.append(f"{prefix}:invalid_frame_order")
    if segment["source_fps_num"] <= 0 or segment["source_fps_den"] <= 0:
        errors.append(f"{prefix}:invalid_source_fps")
    if not str(segment.get("source_path") or "").strip():
        errors.append(f"{prefix}:source_path:required")
    speed = segment.get("speed", 1.0)
    try:
        valid_speed = type(speed) in (int, float) and math.isfinite(speed) and speed > 0
    except OverflowError:
        valid_speed = False
    if not valid_speed:
        errors.append(f"{prefix}:speed:invalid")
    if float(segment.get("video_hold", 0.0) or 0.0) > 0:
        errors.append(f"{prefix}:video_hold:forbidden")
    return errors


def validate_source_progression(segments: list[dict]) -> list[str]:
    """Reject replayed source frames while allowing adjacent forward coalescing."""
    errors: list[str] = []
    last_by_source: dict[str, tuple[int, int, int, int, int]] = {}
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or validate_segment(segment, index):
            continue
        source_hash = str(segment.get("source_sha256") or "").strip().lower()
        source_path = str(segment.get("source_path") or "").strip()
        identity = source_hash or str(Path(source_path).resolve()).casefold()
        start, end = segment["source_in_frame"], segment["source_out_frame_exclusive"]
        fps = (segment["source_fps_num"], segment["source_fps_den"])
        previous = last_by_source.get(identity)
        if previous:
            previous_index, previous_start, previous_end, fps_num, fps_den = previous
            if fps != (fps_num, fps_den):
                errors.append(f"segment[{index}]:source_fps_changed:segment[{previous_index}]")
            elif start < previous_end:
                previous_segment = segments[previous_index]
                same_path = (str(Path(previous_segment["source_path"]).resolve()).casefold()
                             == str(Path(source_path).resolve()).casefold())
                same_speed = abs(float(previous_segment.get("speed", 1.0)) - float(segment.get("speed", 1.0))) <= 1e-12
                if not (previous_index == index - 1 and start >= previous_start and end > previous_end
                        and same_path and same_speed):
                    errors.append(f"segment[{index}]:source_frames_replayed:segment[{previous_index}]:"
                                  f"{start}-{min(end, previous_end) - 1}")
        if not previous or end > previous[2]:
            last_by_source[identity] = (index, start, end, *fps)
    return errors


def validate_plan_value(plan: dict) -> list[str]:
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["segments:required_nonempty_list"]
    errors: list[str] = []
    try:
        audio_balance.configuration(plan.get("audio_balance"))
    except ValueError as error:
        errors.append(str(error))
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segment[{index}]:must_be_object")
            continue
        errors.extend(validate_segment(segment, index))
    return errors + validate_source_progression(segments)
