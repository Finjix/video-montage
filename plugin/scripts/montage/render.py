"""Clean renders (1440x2560, 60 fps) from resolved native frame ranges, their QC, subtitle drafts and cut sheets."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import numpy as np

from montage import audio as AUDIO
from montage import catalog as CATALOG
from montage import rules
from montage.common import FFMPEG, digest, log, pool, probe, read, run, sha, write
from montage.packaging import segmentation

WIDTH, HEIGHT, FPS = 1440, 2560, 60
FADE = 0.005
PREROLL = 2.0
VERSION = 7  # bump when the render recipe changes


def video_seek(clip: dict) -> float:
    """Input seek to the exact start of the clip's first frame.

    Rounded up to the nanosecond: FFmpeg keeps frames whose timestamp is >= the seek after converting it to
    the stream time base, so a value just below the boundary keeps one frame too many in 1/fps time bases.
    """
    return math.ceil(clip["in"] * clip["fps_den"] / clip["fps_num"] * 1e9) / 1e9


def audio_seek(clip: dict) -> float:
    """Audio is decoded from a little earlier and trimmed by samples (an exact audio seek drifts a few samples)."""
    return max(0.0, clip["in"] * clip["fps_den"] / clip["fps_num"] - PREROLL)


def audio_chain(clip: dict, seek: float) -> str:
    """Source samples of the clip, stereo 48 kHz, everything outside the protected speech muted."""
    fps = clip["fps_num"] / clip["fps_den"]
    start, end = clip["in"] / fps, clip["out"] / fps
    a, b = clip["protect"][0] - start, clip["protect"][1] - start
    envelope = f"clip((t-{a - FADE:.6f})/{FADE},0,1)*clip(({b + FADE:.6f}-t)/{FADE},0,1)"
    return (f"atrim=start={start - seek:.9f}:end={end - seek:.9f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,aeval=exprs='val(ch)*({envelope})':c=same")


def render_key(output: dict) -> str:
    return digest([VERSION, [{key: clip[key] for key in ("path", "sha256", "in", "out", "protect", "fps_num", "fps_den")}
                             for clip in output["clips"]]])


def encode(output: dict, target: Path) -> list[float]:
    measurements = []
    for clip in output["clips"]:
        seek = audio_seek(clip)
        measurements.append(AUDIO.measure(Path(clip["path"]), audio_chain(clip, seek), seek=seek))
    gains = AUDIO.balance(measurements)
    command = [str(FFMPEG), "-hide_banner", "-nostdin", "-y"]
    filters, pairs = [], []
    for index, (clip, gain) in enumerate(zip(output["clips"], gains)):
        video, sound = 2 * index, 2 * index + 1
        command += ["-ss", f"{video_seek(clip):.9f}", "-an", "-i", clip["path"],
                    "-ss", f"{audio_seek(clip):.9f}", "-vn", "-i", clip["path"]]
        count = clip["frames"]
        native = clip["out"] - clip["in"]
        filters.append(f"[{video}:v]trim=end_frame={native},setpts=N*{clip['fps_den']}/({clip['fps_num']}*TB),fps={FPS},"
                       f"trim=end_frame={count},setpts=N/({FPS}*TB),scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1,"
                       f"format=yuv420p[v{index}]")
        duration = count / FPS
        filters.append(f"[{sound}:a]{audio_chain(clip, audio_seek(clip))},volume={gain:.3f}dB,"
                       f"apad=pad_dur={duration:.6f},atrim=duration={duration:.6f}[a{index}]")
        pairs.append(f"[v{index}][a{index}]")
    filters.append("".join(pairs) + f"concat=n={len(pairs)}:v=1:a=1[vout][aout]")
    partial = target.with_name(target.stem + ".partial.mp4")
    command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-r", str(FPS),
                "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(partial)]
    try:
        run(command)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return gains


def lineage(job: Path, output: dict, target: Path) -> list[str]:
    """Each clip's first and last rendered frames must be its planned source frames (fingerprint match)."""
    rendered = CATALOG.fingerprints(target).astype(np.int16)
    errors, prints = [], {}
    for number, clip in enumerate(output["clips"], 1):
        if clip["source"] not in prints:
            prints[clip["source"]] = np.load(job / "catalog" / f"{clip['source']}.npz")["prints"].astype(np.int16)
        source = prints[clip["source"]]
        slack = 0 if (clip["fps_num"], clip["fps_den"]) == (FPS, 1) else 1
        for label, out_frame, frame, allowed in (("first", clip["out_in"], clip["in"], 0),
                                                 ("last", clip["out_out"] - 1, clip["out"] - 1, slack)):
            if out_frame >= len(rendered):
                errors.append(f"clip {number}: rendered video has no frame {out_frame}")
                continue
            error = {k: float(np.abs(source[frame + k] - rendered[out_frame]).mean())
                     for k in range(-2, 3) if 0 <= frame + k < len(source)}
            best = min(error, key=error.get)
            if abs(best) > allowed and error[0] > error[best] + 1.0:
                errors.append(f"clip {number}: {label} rendered frame shows source frame {frame + best}, planned {frame}")
    return errors


def quality(job: Path, output: dict, target: Path) -> tuple[dict, list[str]]:
    errors = lineage(job, output, target)
    info = probe(target)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    frames = int(video.get("nb_frames") or 0)
    if frames != output["frames"] or video["avg_frame_rate"] != "60/1" or (int(video["width"]), int(video["height"])) != (WIDTH, HEIGHT):
        errors.append(f"clean render is {frames} frames {video['avg_frame_rate']} {video['width']}x{video['height']}; "
                      f"expected {output['frames']} at 60/1 {WIDTH}x{HEIGHT}")
    samples = AUDIO.pcm(target)
    clipping = AUDIO.clipped(samples)
    if clipping:
        errors.append(f"{clipping} clipped audio samples")
    cuts = []
    for clip in output["clips"][1:]:
        before, after = AUDIO.edge_db(samples, clip["out_in"])
        cuts.append([clip["out_in"], before, after])
        if max(before, after) > -30:
            errors.append(f"cut at frame {clip['out_in']} is not silent ({before}/{after} dBFS)")
    loudness = AUDIO.measure(target)
    return {"frames": frames, "clipped_samples": clipping, "cuts": cuts, "lufs": loudness["lufs"],
            "true_peak": loudness["true_peak"]}, errors


def subtitle_rows(output: dict, games: list[str], protected: tuple[str, ...]) -> list[dict]:
    """Draft cues from the corrected, aligned words, mapped onto the clean timeline; each cue stays in its clip."""
    rows = []
    for clip in output["clips"]:
        fps = clip["fps_num"] / clip["fps_den"]
        start = clip["in"] / fps
        scale = (clip["frames"] / FPS) / ((clip["out"] - clip["in"]) / fps)
        words = [{"word": w["word"], "start": (w["start"] - start) * scale, "end": (w["end"] - start) * scale}
                 for w in clip["words"]]
        shot = {"output_in_frame": clip["out_in"], "asr": {"segments": [{"text": clip["text"], "words": words}]}}
        lower, upper = clip["out_in"] / FPS, clip["out_out"] / FPS
        for row in segmentation.split_shot(shot, names=tuple(games), protected_terms=protected):
            rows.append({"start": max(lower, row["start"]), "end": min(upper, row["end"]), "text": row["text"], "clip": clip["out_in"]})
    return merge_lone_names(rows, games)


def merge_lone_names(rows: list[dict], games: list[str]) -> list[dict]:
    """A cue that is only a game name must show 0.8 s and appear once per video; otherwise it joins a neighbour."""
    names = {rules.normalize(name) for name in games}

    def joinable(left: dict, right: dict) -> bool:
        return left["clip"] == right["clip"] and segmentation.character_count(left["text"] + right["text"]) <= segmentation.MAX_LINE_CHARACTERS

    result, standalone = [], False
    for index, row in enumerate(rows):
        lone = rules.normalize(row["text"]) in names
        if not lone or (not standalone and row["end"] - row["start"] >= 0.8):
            standalone = standalone or lone
            result.append(dict(row))
        elif result and joinable(result[-1], row):
            result[-1].update(text=result[-1]["text"] + row["text"], end=row["end"])
        elif index + 1 < len(rows) and joinable(row, rows[index + 1]):
            rows[index + 1] = {**rows[index + 1], "text": row["text"] + rows[index + 1]["text"], "start": row["start"]}
        else:
            result.append(dict(row))
    return result


def write_srt(rows: list[dict], path: Path, duration_ms: int) -> None:
    blocks, previous = [], 0
    for row in rows:
        start = max(previous, round(row["start"] * 1000))
        end = min(duration_ms, max(start + 100, round(row["end"] * 1000)))
        if end <= start:
            continue
        blocks.append(f"{len(blocks) + 1}\n{timestamp(start)} --> {timestamp(end)}\n{row['text']}")
        previous = end
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def timestamp(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, milli = divmod(rem, 1000)
    return f"{hour:02}:{minute:02}:{second:02},{milli:03}"


def cut_sheet(output: dict, video: Path, target: Path) -> Path:
    """First and last frame of every shot, in order: compare across each cut for jumps and hidden transitions."""
    numbers = []
    for shot in output["shots"]:
        numbers += [shot["out_in"], shot["out_out"] - 1]
    images = CATALOG.grab(video, numbers)
    tiles = []
    for index, shot in enumerate(output["shots"], 1):
        tiles.append((images[shot["out_in"]], f"#{index} {shot['source']} first"))
        tiles.append((images[shot["out_out"] - 1], f"#{index} last {shot['frames']}f"))
    return CATALOG.tile_sheet(tiles, target, columns=8, title=f"{output['plan_id']} cuts (first/last frame of each shot)")


def render_one(job: Path, output: dict, games: list[str], protected: tuple[str, ...]) -> dict:
    started = time.monotonic()
    pid = output["plan_id"]
    target = job / "clean" / f"{pid}.mp4"
    record_path = job / "clean" / f"{pid}.json"
    key = render_key(output)
    previous = read(record_path) if record_path.is_file() else None
    reused = bool(previous and previous.get("key") == key and target.is_file() and sha(target) == previous.get("sha256"))
    target.parent.mkdir(parents=True, exist_ok=True)
    gains = previous["gains"] if reused else encode(output, target)
    qc, errors = (previous["qc"], previous["errors"]) if reused else quality(job, output, target)
    subtitle = job / "subtitles" / f"subtitle-{pid}.txt"
    rows = subtitle_rows(output, games, protected)
    draft_key = digest([VERSION, rows])
    edited = bool(previous and subtitle.is_file() and sha(subtitle) != previous.get("draft_sha256"))
    notes = []
    if not reused or not subtitle.is_file() or (previous.get("draft_key") != draft_key and not edited):
        write_srt(rows, subtitle, round(output["frames"] / FPS * 1000))
        draft_sha = sha(subtitle)
    else:
        draft_sha = previous["draft_sha256"]
        if previous.get("draft_key") != draft_key:
            notes.append("speech changed but the edited subtitle file was kept; re-check it")
    sheet = cut_sheet(output, target, job / "sheets" / f"{pid}-cuts.jpg")
    record = {"key": key, "plan_id": pid, "sha256": sha(target), "path": str(target), "frames": output["frames"],
              "gains": gains, "qc": qc, "errors": errors, "draft_key": draft_key, "draft_sha256": draft_sha, "output": output}
    write(record_path, record)
    state = "reused" if reused else f"rendered in {time.monotonic() - started:.1f} s"
    log(f"render: {pid} {state}" + (f", {len(errors)} problems" if errors else ""))
    return {"plan_id": pid, "errors": errors, "notes": notes, "sheet": str(sheet), "subtitle": str(subtitle), "qc": qc,
            "reused": reused}


def render_all(job: Path, outputs: list[dict], games: list[str], protected: tuple[str, ...]) -> list[dict]:
    """Every output; a failure in one is reported as its problem and never stops the others."""
    def attempt(output):
        try:
            return render_one(job, output, games, protected)
        except (RuntimeError, ValueError, OSError) as error:
            log(f"render: {output['plan_id']} failed")
            # An older render of this plan ID must not be packaged in its place.
            for stale in (job / "clean" / f"{output['plan_id']}.json", job / "clean" / f"{output['plan_id']}.mp4"):
                stale.unlink(missing_ok=True)
            return {"plan_id": output["plan_id"], "errors": [f"render failed: {str(error)[-600:]}"], "notes": [],
                    "sheet": None, "subtitle": None, "qc": {"lufs": None}, "reused": False}
    with pool(len(outputs)) as executor:
        return list(executor.map(attempt, outputs))
