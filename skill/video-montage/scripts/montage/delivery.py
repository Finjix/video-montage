"""Join packaged A, a complete B and an end card on one frame and sample clock."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from montage import audio
from montage.common import DELIVERY_LUFS, DELIVERY_TOLERANCE_LU, FFMPEG, delivery_lufs, probe, run

FPS, RATE = 60, 48000
SAMPLES_PER_FRAME = RATE // FPS
ROLES = ("a", "b", "endcard")
_COUNTER = re.compile(r"\[showinfo@(a|b|endcard) @ [^\]]+\]\s+n:\s*(\d+)")


def _streams(path):
    streams = probe(path)["streams"]
    videos = [s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError(f"{path}: missing picture")
    return videos[0], next((s for s in streams if s["codec_type"] == "audio"), None)


def _canvas(stream):
    sar = stream.get("sample_aspect_ratio") or "1:1"
    aspect = Fraction(sar.replace(":", "/")) if sar not in ("N/A", "0:1") else 1
    width = max(2, 2 * round(stream["width"] * aspect / 2))
    height = max(2, 2 * round(stream["height"] / 2))
    rotation = next((s["rotation"] for s in stream.get("side_data_list", []) if "rotation" in s), 0)
    return (height, width) if abs(rotation) % 180 == 90 else (width, height)


def _select_b(path, frame_range):
    from montage.catalog import video_clock

    if len(frame_range) != 2 or any(type(n) is not int for n in frame_range):
        raise ValueError("b_range needs two integer native frame numbers [start, stop)")
    clock = video_clock(path)
    start, stop = frame_range
    if not 0 <= start < stop <= clock["frames"]:
        raise ValueError(f"b_range must be within the {clock['frames']} source frames")
    seconds = Fraction(clock["fps_den"], clock["fps_num"])
    return (f"trim=start_frame={start}:end_frame={stop},",
            (round(start * seconds * RATE), round(stop * seconds * RATE)))


def _picture(work, paths, streams, canvas, b_trim):
    width, height = canvas
    conform = (f"setpts=PTS-STARTPTS,fps={FPS}:start_time=0,"
               f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:"
               "out_color_matrix=bt709:out_range=tv,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1,"
               "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
    args, graph, filters = [], [], []
    for i, role in enumerate(ROLES):
        args += ["-threads", "2", "-i", str(paths[i])]
        filters.append((b_trim if role == "b" else "") + conform)
        graph.append(f"[{i}:{streams[i][0]['index']}]{filters[i]},showinfo@{role}=checksum=0[v{i}]")
    graph.append(f"[v0][v1][v2]concat=n=3:v=1:a=0,settb=1/{FPS},setpts=N[out]")
    picture = work / "picture.mp4"
    command = [str(FFMPEG), "-hide_banner", "-nostdin", "-nostats", "-xerror", *args,
               "-filter_complex_threads", "2", "-filter_complex", ";".join(graph), "-map", "[out]", "-an",
               "-c:v", "libx264", "-threads:v", "4", "-preset", "veryfast", "-crf", "18", "-profile:v", "high",
               "-r", str(FPS), "-fps_mode", "cfr", "-video_track_timescale", "60000", str(picture)]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"picture assembly failed: {result.stderr[-2500:]}")
    counts = {role: 0 for role in ROLES}
    for role, number in _COUNTER.findall(result.stderr):
        counts[role] = int(number) + 1
    if not all(counts.values()):
        raise ValueError(f"an input has no complete output frame: {counts}")
    return picture, counts, filters


def _start(stream):
    if "start_pts" in stream:
        return int(stream["start_pts"]) * Fraction(stream["time_base"])
    return Fraction(stream.get("start_time", "0"))


def _audio_source(path, streams, samples, wav, selected):
    video, sound = streams
    if sound:
        offset = round((_start(sound) - _start(video)) * RATE)
        args = ["-i", path, "-map", f"0:{sound['index']}"]
        align = (f"asetpts=PTS-STARTPTS+({offset})/{RATE}/TB,aresample={RATE}:async=1:first_pts=0,"
                 "aformat=channel_layouts=stereo,")
        if selected:
            start, stop = selected
            align += f"atrim=start_sample={start}:end_sample={stop},asetpts=N/SR/TB,"
    else:
        args = ["-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=stereo"]
        align = ""
    run([FFMPEG, "-v", "error", "-nostdin", "-xerror", *args, "-vn", "-af",
         f"{align}apad=whole_len={samples},atrim=end_sample={samples},asetpts=N/SR/TB",
         "-c:a", "pcm_f32le", wav])


def _mode(level, sound, samples):
    if not sound:
        return "missing_audio"
    if level["true_peak"] is None:
        return "silence"
    if samples < RATE * 0.4:
        return "too_short"
    if level["lufs"] is None or level["lufs"] < audio.MIN_SIGNAL_LUFS:
        return "below_gate"
    return "balanced"


def _soundtrack(work, parts, target):
    # Iterate on the encoded AAC. The limiter and encoder can change both LUFS and true peak.
    candidate = work / "sound.m4a"
    gains = [target - p["input"]["lufs"] if p["audio_mode"] == "balanced" else 0.0 for p in parts]
    ceilings = [-2.0] * len(parts)
    common_gain = 0.0
    for attempt in range(8):
        graph, inputs = [], []
        for i, p in enumerate(parts):
            inputs += ["-i", work / (p["role"] + ".wav")]
            chain = [f"volume={gains[i]:.5f}dB:precision=double"]
            peak = p["input"]["true_peak"]
            if peak is not None and peak + gains[i] > ceilings[i]:
                limit = 10 ** (ceilings[i] / 20)
                chain += ["aresample=192000", f"alimiter=limit={limit:.8f}:level=0:latency=1", f"aresample={RATE}"]
            samples = p["frames"] * SAMPLES_PER_FRAME
            chain.extend([f"apad=whole_len={samples}", f"atrim=end_sample={samples}", "asetpts=N/SR/TB"])
            graph.append(f"[{i}:a]" + ",".join(chain) + f"[s{i}]")
        graph.append("[s0][s1][s2]concat=n=3:v=0:a=1,asetpts=N/SR/TB[out]")
        run([FFMPEG, "-v", "error", "-nostdin", "-y", *inputs, "-filter_complex", ";".join(graph),
             "-map", "[out]", "-c:a", "aac", "-b:a", "256k", "-ar", str(RATE), "-ac", "2",
             "-movie_timescale", str(RATE), candidate])
        integrated = audio.measure(candidate)
        if integrated["lufs"] is None:
            raise ValueError("encoded mix has no measurable loudness")
        master_error = target - integrated["lufs"]
        passed = abs(master_error) <= DELIVERY_TOLERANCE_LU and (integrated["true_peak"] is None or integrated["true_peak"] <= -1)
        common_gain = min(0.9, max(-0.9, common_gain + master_error))
        for i, p in enumerate(parts):
            first = p["start_frame"] * SAMPLES_PER_FRAME
            stop = first + p["frames"] * SAMPLES_PER_FRAME
            level = audio.measure(candidate, f"atrim=start_sample={first}:end_sample={stop},asetpts=N/SR/TB")
            p["output"] = level
            if p["audio_mode"] == "balanced":
                if level["lufs"] is None:
                    raise ValueError(f"{p['role']}: measurable source became silent")
                error = target - level["lufs"]
                passed &= abs(error) <= audio.TOLERANCE_LU
                gains[i] += error + common_gain
            if level["true_peak"] is not None and level["true_peak"] > -1:
                passed = False
                ceilings[i] -= level["true_peak"] + 1.25
        if passed:
            return candidate, integrated
    levels = {p["role"]: p["output"] for p in parts}
    raise ValueError(f"encoded audio outside loudness/peak limits at {target} LUFS: {levels}, mix {integrated}")


def _thumbnail(path, filters, first_frame=0):
    # Seek just before a CFR frame so conversion to microseconds cannot skip it.
    seek = ["-ss", f"{(first_frame * 1_000_000 // FPS) / 1_000_000:.6f}"] if first_frame else []
    return run([FFMPEG, "-v", "error", "-nostdin", *seek, "-i", path, "-map", "0:V:0",
                "-vf", filters + "extractplanes=y,scale=64:96:flags=area", "-frames:v", "1", "-threads:v", "1",
                "-f", "rawvideo", "-"], binary=True)


def _verify(path, paths, parts, filters, frames, canvas):
    video, sound = _streams(path)
    duration = Fraction(frames, FPS)
    if (not sound or int(video["nb_frames"]) != frames or _canvas(video) != canvas
            or Fraction(video["avg_frame_rate"]) != FPS or int(sound["sample_rate"]) != RATE or sound["channels"] != 2):
        raise ValueError("delivered streams differ from the assembled frame/sample clock")
    for stream in (video, sound):
        if _start(stream) != 0 or int(stream["duration_ts"]) * Fraction(stream["time_base"]) != duration:
            raise ValueError("assembled picture and audio must start at zero and end on the same frame")
    run([FFMPEG, "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode", "-i", path,
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    for segment, source, conform in zip(parts[1:], paths[1:], filters[1:]):
        first = segment["start_frame"]
        reference = _thumbnail(source, conform + ",")
        actual = _thumbnail(path, "", first)
        if len(reference) != 64 * 96 or len(actual) != len(reference):
            raise ValueError(f"{segment['role']}: missing reference or output seam frame")
        mae = sum(abs(left - right) for left, right in zip(reference, actual)) / len(reference)
        if mae > 3:
            raise ValueError(f"{segment['role']}: wrong picture at frame {first} (mean luma error {mae:.2f})")
        segment["seam_luma_mae"] = round(mae, 3)


def assemble(a: Path, b: Path, endcard: Path, output: Path, target_lufs: float = DELIVERY_LUFS,
             b_range: tuple[int, int] | None = None) -> dict:
    """Encode, verify, then atomically publish one MP4. Return exact segment ranges and decoded AAC levels."""
    target_lufs = delivery_lufs(target_lufs)
    paths = [Path(p).resolve(strict=True) for p in (a, b, endcard)]
    output = Path(output).resolve()
    if output in paths or output.suffix.lower() != ".mp4":
        raise ValueError("delivery output must be a separate MP4 path")
    streams = [_streams(path) for path in paths]
    canvas = _canvas(streams[0][0])
    b_trim, b_samples = _select_b(paths[1], b_range) if b_range is not None else ("", None)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".assembly-", dir=output.parent) as directory:
        work = Path(directory)
        picture, counts, filters = _picture(work, paths, streams, canvas, b_trim)
        parts, frames = [], 0
        for role, path, source_streams in zip(ROLES, paths, streams):
            wav = work / (role + ".wav")
            samples = counts[role] * SAMPLES_PER_FRAME
            _audio_source(path, source_streams, samples, wav, b_samples if role == "b" else None)
            level = audio.measure(wav)
            parts.append({"role": role, "source": str(path), "start_frame": frames, "frames": counts[role],
                          "input": level, "audio_mode": _mode(level, source_streams[1], samples)})
            if role == "b" and b_range is not None:
                parts[-1]["source_frames"] = list(b_range)
            frames += counts[role]
        soundtrack, integrated = _soundtrack(work, parts, target_lufs)
        candidate = work / "delivery.mp4"
        run([FFMPEG, "-v", "error", "-nostdin", "-i", picture, "-i", soundtrack, "-map", "0:v:0", "-map", "1:a:0",
             "-c", "copy", "-movie_timescale", str(RATE), "-movflags", "+faststart", candidate])
        _verify(candidate, paths, parts, filters, frames, canvas)
        os.replace(candidate, output)
    return {"frames": frames, "fps": FPS, "width": canvas[0], "height": canvas[1],
            "audio_samples": frames * SAMPLES_PER_FRAME, "target_lufs": target_lufs,
            "segments": parts, "integrated": integrated}
