"""FFmpeg renderer whose only edit authority is decoded native frame indexes."""

from __future__ import annotations

import math
import os
import subprocess
from fractions import Fraction
from pathlib import Path

from montage.common import FFMPEG, FFPROBE, probe, read, sha, write
from montage.media import audio_balance, audio_cleanup
from montage.media.frame_plan import validate_plan_value
from montage.media.timing import require_constant_frame_rate


RENDER_MODE = "source_frame_ranges/v1"


_LARGE_DIGESTS: dict[tuple, str] = {}


def require_render_paths(output: Path, evidence: Path, inputs: list[Path], *, partial: Path | None = None) -> None:
    partial = partial if partial is not None else output.with_suffix(".partial.mp4")
    targets = [path.resolve() for path in (output, partial, evidence, evidence.with_name(evidence.name + ".partial"))]
    if len(set(targets)) != len(targets) or {path.resolve() for path in inputs}.intersection(targets):
        raise ValueError("render output, evidence and input paths collide")


def atempo_chain(speed: float) -> str:
    try:
        valid = type(speed) in (int, float) and math.isfinite(speed) and speed > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("speed must be finite and positive")
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
            same_gain = float(previous.get("audio_gain_db", 0.0)) == float(current.get("audio_gain_db", 0.0))
            monotonic = int(current["source_in_frame"]) >= int(previous["source_in_frame"])
            touches_or_overlaps = int(current["source_in_frame"]) <= int(previous["source_out_frame_exclusive"])
            extends_forward = int(current["source_out_frame_exclusive"]) > int(previous["source_out_frame_exclusive"])
            cleanup_separate = any(item.get("audio_cleanup_policy") or item.get("audio_cleanup", {}).get("mute_ranges")
                                   for item in (previous, current))
            if same_source and same_hash and same_fps and same_speed and same_gain and monotonic and touches_or_overlaps and extends_forward and not cleanup_separate:
                previous["source_out_frame_exclusive"] = int(current["source_out_frame_exclusive"])
                previous["speech_end_frame"] = max(int(previous["speech_end_frame"]), int(current["speech_end_frame"]))
                previous["_candidate_ids"].extend(current["_candidate_ids"])
                previous["_source_segment_indexes"].extend(current["_source_segment_indexes"])
                previous["_coalesced"] = True
                previous["text"] = "".join(filter(None, [str(previous.get("text") or ""), str(current.get("text") or "")]))
                continue
        result.append(current)
    return result


def segment_output_frames(segment: dict, fps: int = 60) -> int:
    """Use the same native-frame rounding for planning and actual rendering."""
    duration = float(Fraction((segment["source_out_frame_exclusive"] - segment["source_in_frame"])
                              * segment["source_fps_den"], segment["source_fps_num"]))
    return round(duration / float(segment.get("speed", 1.0)) * fps)


def planned_timeline(segments: list[dict], fps: int = 60) -> list[dict]:
    result = []
    cursor = 0
    for segment in coalesce_segments(segments):
        count = segment_output_frames(segment, fps)
        result.append({**segment, "output_in_frame": cursor,
                       "output_out_frame_exclusive": cursor + count,
                       "expected_output_frames": count})
        cursor += count
    return result


def render(plan_path: Path, output: Path, evidence: Path, width: int = 1440, height: int = 2560, fps: int = 60,
           *, overwrite: bool = False) -> dict:
    plan = read(plan_path)
    errors = validate_plan_value(plan)
    if errors:
        raise ValueError("frame plan rejected: " + "; ".join(errors))
    partial = output.with_suffix(".partial.mp4")
    if (output.parent.name == "clean" and output.parent.parent.name == "temp"
            and output.parent.parent.parent.name.startswith("自动化混剪_")):
        partial = output.parent.parent / partial.name
    require_render_paths(output, evidence, [plan_path, *(Path(segment["source_path"]) for segment in plan["segments"])],
                         partial=partial)
    if partial.exists() or (output.exists() and not overwrite):
        raise FileExistsError(f"refusing overwrite: {output}")
    ffmpeg, ffprobe = FFMPEG, FFPROBE
    source_segments = plan["segments"]
    segments = coalesce_segments(source_segments)
    balance_config = audio_balance.configuration(plan.get("audio_balance"))
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-y"]
    filters = []
    concat_inputs = []
    evidence_segments = []
    audio_specs = []
    measurements = []
    expected_frames = 0
    for index, segment in enumerate(segments):
        source = Path(segment["source_path"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        declared_hash = str(segment.get("source_sha256") or "").lower()
        actual_hash = sha(source)
        if declared_hash and declared_hash != actual_hash:
            raise ValueError(f"source hash mismatch: {source}")
        start = int(segment["source_in_frame"])
        end = int(segment["source_out_frame_exclusive"])
        fps_num = int(segment["source_fps_num"])
        fps_den = int(segment["source_fps_den"])
        source_fps = Fraction(fps_num, fps_den)
        speed = float(segment.get("speed", 1.0))
        info = probe(source)
        video = next(item for item in info["streams"] if item["codec_type"] == "video")
        decoded_frames = require_constant_frame_rate(ffprobe, source, video)
        if end > decoded_frames:
            raise ValueError(f"source range beyond decoded video frames: {end}>{decoded_frames}: {source}")
        probed_fps = Fraction(video["avg_frame_rate"])
        if probed_fps != source_fps:
            raise ValueError(f"source fps mismatch for {source}: declared={source_fps}, probed={probed_fps}")
        frame_count = end - start
        nominal_duration = float(Fraction(frame_count * fps_den, fps_num)) / speed
        output_frame_count = segment_output_frames(segment, fps)
        output_duration = output_frame_count / fps
        expected_frames += output_frame_count
        audio_start = float(Fraction(start * fps_den, fps_num))
        audio_end = float(Fraction(end * fps_den, fps_num))
        command.extend(["-i", str(source)])
        filters.append(
            f"[{index}:v]select='between(n\\,{start}\\,{end - 1})',"
            f"setpts=N*{fps_den}/({fps_num}*TB)/{speed:.12f},"
            f"fps={fps},trim=end_frame={output_frame_count},setpts=N/({fps}*TB),"
            f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p[v{index}]"
        )
        audio = (
            f"[{index}:a]atrim=start={audio_start:.12f}:end={audio_end:.12f},"
            "asetpts=PTS-STARTPTS,aresample=48000"
        )
        cleanup = segment.get("audio_cleanup")
        if segment.get("audio_cleanup_policy") and not cleanup:
            raise ValueError("reviewed audio cleanup required before rendering")
        if cleanup:
            # Upmix before aeval: the bundled FFmpeg crashes on mono aeval -> stereo -> loudnorm.
            # Stereo sources are unchanged (the upmix is a no-op).
            upmix = "aformat=channel_layouts=stereo," if balance_config["enabled"] else ""
            audio = f"[{index}:a]" + upmix + audio_cleanup.filter_chain(cleanup, segment)
        # Reserve headroom before lossy encoding when the frame plan requests it.
        audio_gain_db = float(segment.get("audio_gain_db", 0.0))
        if not -24.0 <= audio_gain_db <= 0.0:
            raise ValueError("audio_gain_db must be between -24 and 0 dB")
        if abs(speed - 1.0) > 1e-12:
            audio += "," + atempo_chain(speed)
        if balance_config["enabled"]:
            audio += ",aformat=channel_layouts=stereo"
        audio += f",apad=pad_dur={output_duration:.12f},atrim=duration={output_duration:.12f}"
        audio_specs.append((audio, audio_gain_db))
        if balance_config["enabled"]:
            measurements.append(audio_balance.measure(ffmpeg, source, audio.split("]", 1)[1]))
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
                "audio_gain_db": audio_gain_db,
                **({"audio_cleanup": cleanup} if cleanup else {}),
                "expected_output_frames": output_frame_count,
                "nominal_duration_seconds": nominal_duration,
                "output_duration_seconds": output_duration,
            }
        )
    common_target, balanced = (audio_balance.choose_gains(measurements, balance_config)
                               if balance_config["enabled"] else (None, []))
    for index, (audio, manual_gain) in enumerate(audio_specs):
        balance_gain = balanced[index]["gain_db"] if balanced else 0.0
        total_gain = balance_gain + manual_gain
        if total_gain:
            audio += f",volume={total_gain:.6f}dB"
        filters.append(audio + f"[a{index}]")
        if balanced:
            evidence_segments[index]["audio_balance"] = {**balanced[index], "total_gain_db": total_gain}
    filters.append("".join(concat_inputs) + f"concat=n={len(segments)}:v=1:a=1[vout][aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
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
        output_info = probe(partial)
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
        if balanced:
            cursor = 0
            for segment in evidence_segments:
                end = cursor + segment["expected_output_frames"]
                measured = audio_balance.measure(ffmpeg, partial,
                    f"atrim=start={cursor / fps:.12f}:end={end / fps:.12f},asetpts=PTS-STARTPTS")
                balance = segment["audio_balance"]
                expected_lufs = (balance["integrated_lufs"] + balance["total_gain_db"]
                                 if balance["status"] == "balanced" else None)
                balance["output_verification"] = audio_balance.verify(measured, expected_lufs, balance_config)
                cursor = end
            checks["audio_balance"] = all(s["audio_balance"]["output_verification"]["decision"] == "pass"
                                          for s in evidence_segments)
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
        "plan_sha256": sha(plan_path),
        "export_path": str(output.resolve()),
        "export_sha256": sha(output),
        "expected_output_frames": expected_frames,
        "actual_output_frames": actual_frames,
        "source_segment_count": len(source_segments),
        "render_segment_count": len(segments),
        "coalesced_adjacent_source_groups": [item.get("_candidate_ids", []) for item in segments if item.get("_coalesced")],
        "audio_balance": {"policy": audio_balance.POLICY, **balance_config, "common_target_lufs": common_target},
        "segments": evidence_segments,
        "checks": checks,
        "command": command,
    }
    write(evidence, value)
    return value


