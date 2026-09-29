#!/usr/bin/env python3
"""Portable FFmpeg renderer whose only edit authority is decoded frame indexes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v20_frame_plan_gate import validate_plan_value


RENDER_MODE = "source_frame_ranges/v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def runtime_binary(name: str) -> Path:
    environment = os.environ.get(f"MONTAGE_{name.upper()}")
    candidates = []
    if environment:
        candidates.append(Path(environment))
    script = Path(__file__).resolve()
    candidates.extend(
        [
            script.parents[3] / "dependencies" / "ffmpeg" / "bin" / f"{name}.exe",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"bundled {name}.exe not found; set MONTAGE_{name.upper()}")


def probe(ffprobe: Path, path: Path) -> dict:
    run = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(run.stdout)


def atempo_chain(speed: float) -> str:
    if speed <= 0:
        raise ValueError("speed must be positive")
    factors = []
    while speed > 2.0:
        factors.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    factors.append(speed)
    return ",".join(f"atempo={factor:.12f}" for factor in factors)


def coalesce_segments(segments: list[dict]) -> list[dict]:
    """Merge monotonic adjacent ranges from one source when they touch or overlap."""
    result: list[dict] = []
    for source_index, raw in enumerate(segments, 1):
        current = dict(raw)
        current["_candidate_ids"] = list(raw.get("_candidate_ids") or [raw.get("candidate_id") or raw.get("segment_id")])
        current["_source_segment_indexes"] = list(raw.get("_source_segment_indexes") or [source_index])
        if result:
            previous = result[-1]
            same_source = str(Path(previous["source_path"]).resolve()).lower() == str(Path(current["source_path"]).resolve()).lower()
            same_hash = str(previous.get("source_sha256") or "").lower() == str(current.get("source_sha256") or "").lower()
            same_fps = (previous["source_fps_num"], previous["source_fps_den"]) == (current["source_fps_num"], current["source_fps_den"])
            same_speed = abs(float(previous.get("speed", 1.0)) - float(current.get("speed", 1.0))) <= 1e-12
            monotonic = int(current["source_in_frame"]) >= int(previous["source_in_frame"])
            touches_or_overlaps = int(current["source_in_frame"]) <= int(previous["source_out_frame_exclusive"])
            extends_forward = int(current["source_out_frame_exclusive"]) > int(previous["source_out_frame_exclusive"])
            if same_source and same_hash and same_fps and same_speed and monotonic and touches_or_overlaps and extends_forward:
                previous["source_out_frame_exclusive"] = int(current["source_out_frame_exclusive"])
                previous["speech_end_frame"] = max(int(previous["speech_end_frame"]), int(current["speech_end_frame"]))
                previous["_candidate_ids"].extend(current["_candidate_ids"])
                previous["_source_segment_indexes"].extend(current["_source_segment_indexes"])
                previous["_coalesced"] = True
                previous["text"] = "".join(filter(None, [str(previous.get("text") or ""), str(current.get("text") or "")]))
                continue
        result.append(current)
    return result


def render(plan_path: Path, output: Path, evidence: Path, width: int, height: int, fps: int, encoder: str) -> dict:
    plan = load_json(plan_path)
    errors = validate_plan_value(plan)
    if errors:
        raise ValueError("frame plan rejected: " + "; ".join(errors))
    ffmpeg = runtime_binary("ffmpeg")
    ffprobe = runtime_binary("ffprobe")
    source_segments = plan["segments"]
    segments = coalesce_segments(source_segments)
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-y"]
    filters = []
    concat_inputs = []
    evidence_segments = []
    expected_frames = 0
    for index, segment in enumerate(segments):
        source = Path(segment["source_path"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        declared_hash = str(segment.get("source_sha256") or "").lower()
        actual_hash = sha256(source)
        if declared_hash and declared_hash != actual_hash:
            raise ValueError(f"source hash mismatch: {source}")
        start = int(segment["source_in_frame"])
        end = int(segment["source_out_frame_exclusive"])
        fps_num = int(segment["source_fps_num"])
        fps_den = int(segment["source_fps_den"])
        source_fps = Fraction(fps_num, fps_den)
        speed = float(segment.get("speed", 1.0))
        info = probe(ffprobe, source)
        video = next(item for item in info["streams"] if item["codec_type"] == "video")
        probed_fps = Fraction(video["avg_frame_rate"])
        if probed_fps != source_fps:
            raise ValueError(f"source fps mismatch for {source}: declared={source_fps}, probed={probed_fps}")
        frame_count = end - start
        nominal_duration = float(Fraction(frame_count * fps_den, fps_num)) / speed
        segment_output_frames = round(nominal_duration * fps)
        output_duration = segment_output_frames / fps
        expected_frames += segment_output_frames
        audio_start = float(Fraction(start * fps_den, fps_num))
        audio_end = float(Fraction(end * fps_den, fps_num))
        command.extend(["-i", str(source)])
        filters.append(
            f"[{index}:v]select='between(n\\,{start}\\,{end - 1})',"
            f"setpts=N*{fps_den}/({fps_num}*TB)/{speed:.12f},"
            f"fps={fps},trim=end_frame={segment_output_frames},setpts=N/({fps}*TB),"
            f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p[v{index}]"
        )
        audio = (
            f"[{index}:a]atrim=start={audio_start:.12f}:end={audio_end:.12f},"
            "asetpts=PTS-STARTPTS,aresample=48000"
        )
        if abs(speed - 1.0) > 1e-12:
            audio += "," + atempo_chain(speed)
        audio += f",apad=pad_dur={output_duration:.12f},atrim=duration={output_duration:.12f}[a{index}]"
        filters.append(audio)
        concat_inputs.append(f"[v{index}][a{index}]")
        evidence_segments.append(
            {
                "segment_index": index + 1,
                "source_candidate_ids": segment.get("_candidate_ids", []),
                "source_segment_indexes": segment.get("_source_segment_indexes", []),
                "coalesced_adjacent_source": bool(segment.get("_coalesced", False)),
                "source_path": str(source),
                "source_sha256": actual_hash,
                "source_in_frame": start,
                "speech_end_frame": int(segment["speech_end_frame"]),
                "source_out_frame_exclusive": end,
                "source_fps_num": fps_num,
                "source_fps_den": fps_den,
                "audio_start_seconds_derived_from_frame": audio_start,
                "audio_end_seconds_derived_from_frame": audio_end,
                "speed": speed,
                "expected_output_frames": segment_output_frames,
                "nominal_duration_seconds": nominal_duration,
                "output_duration_seconds": output_duration,
            }
        )
    filters.append("".join(concat_inputs) + f"concat=n={len(segments)}:v=1:a=1[vout][aout]")
    partial = output.with_suffix(".partial.mp4")
    if output.exists() or partial.exists():
        raise FileExistsError(f"refusing overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    codec = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    )
    command.extend(
        [
            "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
            "-r", str(fps), "-fps_mode", "cfr", *codec,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(partial),
        ]
    )
    try:
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if run.returncode:
            raise RuntimeError(run.stderr[-5000:])
        output_info = probe(ffprobe, partial)
        video = next(item for item in output_info["streams"] if item["codec_type"] == "video")
        audio = next(item for item in output_info["streams"] if item["codec_type"] == "audio")
        actual_frames = int(video.get("nb_frames") or round(float(output_info["format"]["duration"]) * fps))
        checks = {
            "render_mode_frame_only": True,
            "h264": video["codec_name"] == "h264",
            "dimensions": (int(video["width"]), int(video["height"])) == (width, height),
            "fps": video["avg_frame_rate"] == f"{fps}/1",
            "aac_48k": audio["codec_name"] == "aac" and audio["sample_rate"] == "48000",
            "frame_count_close": abs(actual_frames - expected_frames) <= len(segments),
        }
        if not all(checks.values()):
            raise RuntimeError(f"render validation failed: {checks}")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, output)
    value = {
        "schema": "portable-frame-render-evidence/v260928",
        "render_mode": RENDER_MODE,
        "seconds_only_fallback": False,
        "plan_path": str(plan_path.resolve()),
        "plan_sha256": sha256(plan_path),
        "export_path": str(output.resolve()),
        "export_sha256": sha256(output),
        "expected_output_frames": expected_frames,
        "actual_output_frames": actual_frames,
        "source_segment_count": len(source_segments),
        "render_segment_count": len(segments),
        "coalesced_adjacent_source_groups": [item.get("_candidate_ids", []) for item in segments if item.get("_coalesced")],
        "segments": evidence_segments,
        "checks": checks,
        "command": command,
    }
    atomic_json(evidence, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=2560)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--encoder", choices=("h264_nvenc", "libx264"), default="libx264")
    args = parser.parse_args()
    value = render(args.plan.resolve(), args.output.resolve(), args.evidence.resolve(), args.width, args.height, args.fps, args.encoder)
    print(json.dumps({"decision": "pass", "render_mode": value["render_mode"], "output": value["export_path"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
