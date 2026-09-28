#!/usr/bin/env python3
"""Delete inclusive frame ranges and the audio samples derived from those frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from portable_frame_renderer import atomic_json, probe, runtime_binary, sha256


SCHEMA = "frame-delete-registry/v260928"


def normalize_integer_ranges(raw_ranges: list, total_frames: int, name: str) -> list[list[int]]:
    result: list[list[int]] = []
    for raw in sorted(raw_ranges):
        if not isinstance(raw, list) or len(raw) != 2 or any(isinstance(x, bool) or not isinstance(x, int) for x in raw):
            raise ValueError(f"invalid {name} frame range: {raw}")
        start, end = raw
        if not (0 <= start <= end < total_frames):
            raise ValueError(f"out-of-bounds {name} frame range: {raw}")
        if result and start <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def normalize_ranges(value: dict, total_frames: int, fps: int) -> list[list[int]]:
    if value.get("schema") != SCHEMA:
        raise ValueError("frame delete registry schema mismatch")
    if int(value.get("fps", 0)) != fps:
        raise ValueError("registry fps does not match the input")
    forbidden = {"start_seconds", "end_seconds", "remove_seconds", "time_ranges"}
    if forbidden.intersection(value):
        raise ValueError("time-based delete fields are forbidden")
    if "protected_spoken_ranges_60fps_inclusive" not in value:
        raise ValueError("protected spoken frame ranges are required")
    result = normalize_integer_ranges(value.get("ranges", []), total_frames, "delete")
    protected = normalize_integer_ranges(value.get("protected_spoken_ranges_60fps_inclusive", []), total_frames, "protected spoken")
    for delete_start, delete_end in result:
        for spoken_start, spoken_end in protected:
            if max(delete_start, spoken_start) <= min(delete_end, spoken_end):
                raise ValueError(f"delete range overlaps protected spoken frames: {[delete_start, delete_end]} vs {[spoken_start, spoken_end]}")
    return result


def validate_alignment_evidence(registry: dict, registry_path: Path) -> dict:
    binding = registry.get("spoken_alignment_evidence")
    if not isinstance(binding, dict):
        raise ValueError("spoken alignment evidence binding is required")
    raw_path = str(binding.get("path") or "").strip()
    expected_hash = str(binding.get("sha256") or "").strip().lower()
    if not raw_path or len(expected_hash) != 64:
        raise ValueError("spoken alignment evidence path and sha256 are required")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (registry_path.parent / path).resolve()
    if not path.is_file() or sha256(path) != expected_hash:
        raise ValueError("spoken alignment evidence is missing or hash-mismatched")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("decision") != "pass" or value.get("final_syllable_complete") is not True:
        raise ValueError("spoken alignment evidence did not pass complete-final-syllable review")
    return {"path": str(path), "sha256": expected_hash}


def render(source: Path, output: Path, registry_path: Path, evidence: Path, encoder: str) -> dict:
    ffmpeg = runtime_binary("ffmpeg")
    ffprobe = runtime_binary("ffprobe")
    info = probe(ffprobe, source)
    video = next(item for item in info["streams"] if item["codec_type"] == "video")
    audio = next(item for item in info["streams"] if item["codec_type"] == "audio")
    if video["avg_frame_rate"] != "60/1":
        raise ValueError("frame repair requires an exact 60/1 input")
    total_frames = int(video.get("nb_frames") or round(float(info["format"]["duration"]) * 60))
    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    alignment = validate_alignment_evidence(registry, registry_path)
    ranges = normalize_ranges(registry, total_frames, 60)
    if not ranges:
        raise ValueError("at least one frame range is required")
    rejected = "+".join(f"between(n\\,{start}\\,{end})" for start, end in ranges)
    filters = [f"[0:v]select='not({rejected})',setpts=N/(60*TB),format=yuv420p[vout]"]
    cursor = 0
    labels = []
    for start, end in ranges:
        if cursor < start:
            label = f"a{len(labels)}"
            filters.append(f"[0:a]atrim=start={cursor / 60:.12f}:end={start / 60:.12f},asetpts=PTS-STARTPTS[{label}]")
            labels.append(f"[{label}]")
        cursor = end + 1
    if cursor < total_frames:
        label = f"a{len(labels)}"
        filters.append(f"[0:a]atrim=start={cursor / 60:.12f}:end={total_frames / 60:.12f},asetpts=PTS-STARTPTS[{label}]")
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]")
    partial = output.with_suffix(".partial.mp4")
    if output.exists() or partial.exists():
        raise FileExistsError(f"refusing overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    codec = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    )
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(source),
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-r", "60", "-fps_mode", "cfr", *codec,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(partial),
    ]
    run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode:
        raise RuntimeError(run.stderr[-5000:])
    result_info = probe(ffprobe, partial)
    result_video = next(item for item in result_info["streams"] if item["codec_type"] == "video")
    expected_frames = total_frames - sum(end - start + 1 for start, end in ranges)
    actual_frames = int(result_video.get("nb_frames") or round(float(result_info["format"]["duration"]) * 60))
    if actual_frames != expected_frames:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"frame count mismatch: expected={expected_frames} actual={actual_frames}")
    os.replace(partial, output)
    value = {
        "schema": "frame-range-repair-evidence/v260928",
        "render_mode": "output_frame_delete_ranges/v260928",
        "seconds_only_fallback": False,
        "source_path": str(source.resolve()),
        "source_sha256": sha256(source),
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": sha256(registry_path),
        "deleted_ranges_60fps_inclusive": ranges,
        "protected_spoken_ranges_60fps_inclusive": registry["protected_spoken_ranges_60fps_inclusive"],
        "spoken_alignment_evidence": alignment,
        "deleted_frame_count": total_frames - expected_frames,
        "output_path": str(output.resolve()),
        "output_sha256": sha256(output),
        "output_frames": actual_frames,
        "audio_sample_ranges": "derived exclusively from frame/60 boundaries",
        "video_hold": 0,
        "command": command,
    }
    atomic_json(evidence, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delete-registry", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--encoder", choices=("h264_nvenc", "libx264"), default="libx264")
    args = parser.parse_args()
    value = render(args.input.resolve(), args.output.resolve(), args.delete_registry.resolve(), args.evidence.resolve(), args.encoder)
    print(json.dumps({"decision": "pass", "deleted_frames": value["deleted_frame_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
