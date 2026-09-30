#!/usr/bin/env python3
"""Versioned, fail-closed Codex + machine evidence workflow for new montage jobs.

This does not interpret or upgrade any v260928 executor state or review receipt.
Codex supplies editorial and visual judgments; the local gates verify their
inputs, media hashes, ASR, PCM and rendered output. No hearing or forced
alignment claim is made by this workflow.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "dependencies/ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = ROOT / "dependencies/models"
STATE_SCHEMA = "video-montage-autonomous-state/v260929"
ORDER_SCHEMA = "video-montage-autonomous-work-order/v260929"
PLAN_SCHEMA = "video-montage-autonomous-plan/v260929"
REVIEW_SCHEMA = "video-montage-codex-review/v260929"
MAX_ROUNDS = 3


def module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(value)
    return value


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def ref(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path)}


def require_ref(value: dict, label: str) -> Path:
    if not isinstance(value, dict) or not value.get("path") or not value.get("sha256"):
        raise ValueError(f"{label}: missing reference")
    path = Path(value["path"]).resolve()
    if not path.is_file() or sha(path) != value["sha256"]:
        raise ValueError(f"{label}: changed or missing")
    return path


def run(command: list[str], *, binary: bool = False):
    result = subprocess.run(command, capture_output=True, text=not binary,
                            encoding=None if binary else "utf-8", errors=None if binary else "replace")
    if result.returncode:
        detail = result.stderr[-1500:] if result.stderr else b"" if binary else ""
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def video(path: Path) -> dict:
    info = probe(path)
    stream = next(row for row in info["streams"] if row["codec_type"] == "video")
    fps = Fraction(stream["avg_frame_rate"])
    frames = int(stream.get("nb_frames") or round(float(info["format"]["duration"]) * fps))
    return {"fps_num": fps.numerator, "fps_den": fps.denominator, "frames": frames,
            "duration": float(info["format"]["duration"]), "width": int(stream["width"]),
            "height": int(stream["height"])}


def pcm(path: Path, start: float | None = None, end: float | None = None, rate: int = 16000) -> np.ndarray:
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path)]
    if start is not None or end is not None:
        options = []
        if start is not None:
            options.append(f"start={start:.9f}")
        if end is not None:
            options.append(f"end={end:.9f}")
        command += ["-af", "atrim=" + ":".join(options) + ",asetpts=PTS-STARTPTS"]
    command += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"]
    return np.frombuffer(run(command, binary=True), dtype="<i2").astype(np.float32) / 32768.0


def normalized(text: str) -> str:
    return "".join(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", str(text))).casefold()


def context_corroborates_asr(expected: str, observed: str, asset_copy: dict | None) -> bool:
    """Accept a tiny ASR substitution only when source and visible copy agree.

    Missing/extra characters are not accepted: they might be cut-off speech.
    The source-ASR containment check is performed by the caller.
    """
    left, right = normalized(expected), normalized(observed)
    if not asset_copy or not left or len(left) != len(right) or left[0] != right[0] or left[-1] != right[-1]:
        return False
    opcodes = [item for item in difflib.SequenceMatcher(None, left, right).get_opcodes() if item[0] != "equal"]
    if any(kind != "replace" or i2 - i1 != j2 - j1 for kind, i1, i2, j1, j2 in opcodes):
        return False
    changes = [(i1, i2) for _, i1, i2, _, _ in opcodes]
    if not changes or sum(end - start for start, end in changes) > max(1, len(left) // 20):
        return False
    copy_texts = [normalized(row.get("text", "")) for row in asset_copy.get("assets", [])]
    return all(any(left[max(0, start - before):min(len(left), end + after)] in copy
                   for copy in copy_texts for before in (0, 1, 2) for after in (0, 1, 2)
                   if end - start + before + after >= 3)
               for start, end in changes)


def attempt_dir(value: dict) -> Path:
    return Path(value["output_root"]) / f"attempt-{int(value.get('repair_round', 0)) + 1:02d}"


def audio_metrics(samples: np.ndarray, rate: int = 16000) -> dict:
    if samples.size == 0:
        return {"empty": True}
    power = float(np.mean(samples * samples))
    window = max(1, round(rate * 0.04))
    head, tail = samples[:window], samples[-window:]
    def db(value):
        return round(20 * math.log10(max(float(np.sqrt(np.mean(value * value))), 1e-8)), 2)
    return {"empty": False, "rms_dbfs": round(10 * math.log10(max(power, 1e-12)), 2),
            "head_40ms_dbfs": db(head), "tail_40ms_dbfs": db(tail),
            "peak": round(float(np.max(np.abs(samples))), 6),
            "clipped_samples": int(np.count_nonzero(np.abs(samples) >= 0.999)),
            "duration_seconds": round(samples.size / rate, 6)}


def isolated_transients(samples: np.ndarray, rate: int = 16000) -> int:
    """Count isolated near full-scale impulses; this is not a speech classifier."""
    if samples.size < rate // 10:
        return 0
    loud = np.flatnonzero(np.abs(samples) > .85)
    if loud.size == 0:
        return 0
    events = 0
    previous = -rate
    radius = round(rate * .02)
    for index in loud:
        if index - previous < radius:
            continue
        previous = int(index)
        left = samples[max(0, index - radius):max(0, index - radius // 2)]
        right = samples[min(samples.size, index + radius // 2):min(samples.size, index + radius)]
        if left.size and right.size and max(float(np.sqrt(np.mean(left * left))),
                                            float(np.sqrt(np.mean(right * right)))) < .05:
            events += 1
    return events


def cut_pcm_metrics(samples: np.ndarray, cut_frames: list[int], *, clean: bool, rate: int = 16000) -> list[dict]:
    """Check the decoded signal on both sides of each rendered edit."""
    results = []
    width = round(rate * .04)
    for frame in cut_frames:
        center = round(frame * rate / 60)
        if center < width or center + width > samples.size:
            raise ValueError("cut outside decoded audio")
        before, after = samples[center - width:center], samples[center:center + width]
        db = lambda chunk: 20 * math.log10(max(float(np.sqrt(np.mean(chunk * chunk))), 1e-8))
        before_db, after_db = db(before), db(after)
        impact = isolated_transients(samples[max(0, center - width * 2):min(samples.size, center + width * 2)], rate)
        clipped = int(np.count_nonzero(np.abs(np.concatenate((before, after))) >= .999))
        passed = clipped == 0 and impact == 0 and (
            max(before_db, after_db) <= -30 if clean else abs(before_db - after_db) <= 18)
        results.append({"frame": frame, "pre_40ms_dbfs": round(before_db, 2),
                        "post_40ms_dbfs": round(after_db, 2), "isolated_transients": impact,
                        "clipped_samples": clipped, "decision": "pass" if passed else "reject"})
    return results


def render_cut_frames(render_value: dict) -> list[int]:
    cumulative = 0
    cuts = []
    for segment in render_value["segments"][:-1]:
        cumulative += segment["expected_output_frames"]
        cuts.append(cumulative)
    return cuts


def voice_over_music_db(clean_audio: np.ndarray, music: np.ndarray, gain_db: float) -> float:
    if music.size < 16000:
        raise ValueError("BGM decode empty")
    gain = 10 ** (gain_db / 20)
    ratios = []
    window = 8000
    for start in range(0, clean_audio.size - window + 1, window):
        speech = clean_audio[start:start + window]
        spoken_rms = float(np.sqrt(np.mean(speech * speech)))
        if spoken_rms < 10 ** (-35 / 20):
            continue
        indexes = np.arange(start, start + window) % music.size
        music_rms = float(np.sqrt(np.mean(music[indexes] * music[indexes]))) * gain
        ratios.append(20 * math.log10(max(spoken_rms, 1e-8) / max(music_rms, 1e-8)))
    return round(float(np.quantile(ratios, .1)), 2) if ratios else float("-inf")


def subtitle_change_supported(change: dict, asset_copy: dict, source_index: dict, plan: dict) -> bool:
    evidence = change.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    texts = {}
    for asset in asset_copy.get("assets", []):
        texts[("asset_copy", asset.get("sha256"))] = normalized(asset.get("text", ""))
    for source in source_index["sources"]:
        asr_path = require_ref(source["asr"], "source ASR")
        texts[("source_asr", sha(asr_path))] = normalized(read(asr_path)["asr"]["text"])
    texts[("plan_speech", sha(Path(plan["path"]))) ] = normalized("".join(
        segment["text"] for output in read(Path(plan["path"]))["outputs"] for segment in output["segments"]))
    for item in evidence:
        if not isinstance(item, dict):
            return False
        text = normalized(item.get("text", ""))
        if not text or text not in texts.get((item.get("kind"), item.get("sha256")), ""):
            return False
    return True


def character_times(asr: dict) -> list[tuple[int, int]]:
    times = []
    for segment in asr.get("segments", []):
        for word in segment.get("words", []):
            characters = normalized(word.get("word", ""))
            if not characters:
                continue
            start = round(float(word["start"]) * 1000)
            end = round(float(word["end"]) * 1000)
            for index in range(len(characters)):
                times.append((start + round((end - start) * index / len(characters)),
                              start + round((end - start) * (index + 1) / len(characters))))
    return times


def validate_subtitle_timing(start: int, end: int, first: tuple[int, int],
                             last: tuple[int, int], shot: tuple[int, int]) -> None:
    shot_start, shot_end = shot
    if start < shot_start or end > shot_end:
        raise ValueError("subtitle cue appears outside its rendered shot")
    # Word timestamps are provisional. Only clamp an edge when that word's
    # interval still overlaps the hash-bound rendered shot; unrelated words
    # or arbitrary timing changes must continue to fail.
    if first[1] <= shot_start or last[0] >= shot_end:
        raise ValueError("subtitle ASR words do not overlap their rendered shot")
    expected_start = max(first[0], shot_start)
    expected_end = min(last[1], shot_end)
    if abs(start - expected_start) > 250 or abs(end - expected_end) > 250:
        raise ValueError("corrected cue does not follow ASR word timestamps")


def subtitle_segment_bounds(cues: list[dict], plan_output: dict, render_value: dict) -> list[tuple[int, int]]:
    """Bind each spoken cue to the actual rendered shot that contains its words."""
    segments = plan_output["segments"]
    rendered = render_value["segments"]
    if len(segments) != len(rendered):
        raise ValueError("subtitle/render segment scope mismatch")
    spans = []
    frame_cursor = text_cursor = 0
    for segment, result in zip(segments, rendered):
        frames = result["expected_output_frames"]
        length = len(normalized(segment["text"]))
        if not isinstance(frames, int) or frames <= 0 or length <= 0:
            raise ValueError("invalid rendered segment span")
        spans.append((text_cursor, text_cursor + length, round(frame_cursor * 1000 / 60),
                      round((frame_cursor + frames) * 1000 / 60)))
        frame_cursor += frames
        text_cursor += length
    bounds = []
    cursor = 0
    for cue in cues:
        length = len(normalized(cue["text"]))
        hits = [(start_ms, end_ms) for first, last, start_ms, end_ms in spans
                if first <= cursor and cursor + length <= last]
        if len(hits) != 1:
            raise ValueError("subtitle cue crosses rendered shot boundary")
        bounds.append(hits[0])
        cursor += length
    if cursor != text_cursor:
        raise ValueError("subtitle cues do not cover rendered speech")
    return bounds


def transcribe(model, path: Path) -> dict:
    asr, _ = module("autonomous_asr", "components/semantic/scripts/v9_source_asr.py").transcribe(model, str(path), "zh")
    return asr


def load_model():
    asr = module("autonomous_asr_model", "components/semantic/scripts/v9_source_asr.py")
    return asr.build_model("large-v3-turbo", MODEL_ROOT, "auto", "int8", "float16")[0]


def state(job: Path) -> dict:
    value = read(job / "autonomous_state.json")
    if value.get("schema") != STATE_SCHEMA or value.get("review_mode") != "codex_asr_pcm":
        raise ValueError("not a new autonomous job; legacy state cannot be migrated")
    require_ref(value["work_order"], "work order")
    return value


def save(job: Path, value: dict, phase: str) -> None:
    value["phase"] = phase
    value.setdefault("events", []).append({"at": datetime.now(timezone.utc).isoformat(), "phase": phase})
    write(job / "autonomous_state.json", value)


def fail_round(job: Path, value: dict, phase: str, reasons: list[str]) -> None:
    count = int(value.get("repair_round", 0)) + 1
    value["repair_round"] = count
    value["last_failures"] = reasons
    save(job, value, "failed" if count >= MAX_ROUNDS else "repair_required")
    write(job / "reports" / f"repair_round_{count}.json",
          {"schema": "video-montage-autonomous-repair/v260929", "phase": phase,
           "round": count, "max_rounds": MAX_ROUNDS, "failures": reasons,
           "decision": "failed" if count >= MAX_ROUNDS else "repair_required"})
    raise RuntimeError(f"{phase}: {'; '.join(reasons)}; repair round {count}/{MAX_ROUNDS}")


def init(args) -> None:
    job = args.job_dir.resolve()
    if job.exists() and any(job.iterdir()):
        raise ValueError("refusing non-empty job directory")
    order = read(args.work_order)
    sources = order.get("sources")
    if order.get("schema") != ORDER_SCHEMA or not isinstance(sources, list) or not sources or int(order.get("requested_outputs", 0)) < 1:
        raise ValueError("new autonomous work order required")
    seen = set()
    for row in sources:
        path = Path(row["path"]).resolve()
        if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
            raise ValueError(f"invalid original source: {path}")
        identity = sha(path)
        if identity in seen:
            raise ValueError("duplicate source bytes")
        seen.add(identity)
        if row.get("sha256") and row["sha256"] != identity:
            raise ValueError(f"source hash mismatch: {path}")
    asset = Path(order["asset_root"]).resolve()
    if not asset.is_dir():
        raise ValueError("asset root missing")
    output = Path(order["output_root"]).resolve()
    job.mkdir(parents=True)
    save(job, {"schema": STATE_SCHEMA, "review_mode": "codex_asr_pcm", "work_order": ref(args.work_order),
               "source_hashes": {str(Path(row["path"]).resolve()): sha(Path(row["path"])) for row in sources},
               "asset_root": str(asset), "output_root": str(output), "repair_round": 0,
               "max_repair_rounds": MAX_ROUNDS}, "initialized")


def prepare(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"initialized", "prepared", "repair_required"}:
        raise ValueError("prepare requires initialized job")
    order = read(require_ref(value["work_order"], "work order"))
    asset_root = Path(value["asset_root"])
    assets = []
    for path in sorted(asset_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".png", ".jpg", ".jpeg"}:
            row = {"path": str(path.resolve()), "sha256": sha(path), "kind": path.suffix.lower().lstrip(".")}
            if path.suffix.lower() in {".md", ".txt"}:
                row["text"] = path.read_text(encoding="utf-8-sig")
            assets.append(row)
    if not assets:
        raise ValueError("asset copy pack has no readable copy sources")
    asset_path = job / "evidence" / "asset_copy_sources.json"
    write(asset_path, {"schema": "video-montage-asset-copy-sources/v260929", "assets": assets})
    model = load_model()
    source_rows = []
    for index, row in enumerate(order["sources"], 1):
        path = Path(row["path"]).resolve()
        if sha(path) != value["source_hashes"].get(str(path)):
            raise ValueError(f"source changed: {path}")
        spec = video(path)
        asr_path = job / "evidence" / "source_asr" / f"source_{index:03d}.json"
        if not asr_path.exists():
            write(asr_path, {"schema": "video-montage-autonomous-asr/v260929", "source": ref(path),
                             "video": spec, "asr": transcribe(model, path)})
        result = read(asr_path)
        if result.get("source", {}).get("sha256") != sha(path) or not result.get("asr", {}).get("segments"):
            raise ValueError("source ASR invalid")
        source_rows.append({"source": ref(path), "asr": ref(asr_path), "video": spec})
    index_path = job / "evidence" / "source_index.json"
    write(index_path, {"schema": "video-montage-autonomous-source-index/v260929", "sources": source_rows,
                       "asset_copy_sources": ref(asset_path)})
    value["source_index"] = ref(index_path)
    save(job, value, "prepared")


def asset_copy(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    index = read(require_ref(value["source_index"], "source index"))
    source_path = require_ref(index["asset_copy_sources"], "asset copy sources")
    originals = read(source_path)["assets"]
    proposed = read(args.copy_text)
    if (proposed.get("schema") != "video-montage-codex-asset-copy/v260929"
            or proposed.get("reviewer_role") != "codex" or proposed.get("sources_sha256") != sha(source_path)):
        raise ValueError("asset copy text unbound")
    rows = proposed.get("assets", [])
    by_hash = {row.get("sha256"): row for row in rows}
    if len(by_hash) != len(rows) or set(by_hash) != {row["sha256"] for row in originals}:
        raise ValueError("asset copy text does not cover all assets")
    for original in originals:
        text = by_hash[original["sha256"]].get("text")
        if not isinstance(text, str) or (original["kind"] in {"md", "txt"} and text != original["text"]):
            raise ValueError("asset copy source text changed or missing")
    value["asset_copy"] = ref(args.copy_text)
    save(job, value, "copy_indexed")


def check_plan(plan: dict, source_index: dict, count: int) -> list[str]:
    errors = []
    if plan.get("schema") != PLAN_SCHEMA or len(plan.get("outputs", [])) != count:
        return ["plan schema or complete output scope"]
    source_map = {row["source"]["sha256"]: row for row in source_index["sources"]}
    ids = []
    for output in plan["outputs"]:
        pid = output.get("plan_id")
        ids.append(pid)
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
            errors.append("unsafe plan ID")
        segments = output.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append(f"{pid}: empty segments")
            continue
        frame_gate = module("auto_frame_gate", "components/semantic/scripts/v20_frame_plan_gate.py")
        errors.extend(f"{pid}:{error}" for error in frame_gate.validate_plan_value({"segments": segments}))
        for segment in segments:
            source = source_map.get(segment.get("source_sha256"))
            if source is None or str(Path(segment.get("source_path", "")).resolve()) != source["source"]["path"]:
                errors.append(f"{pid}: source not in work order")
                continue
            spec = source["video"]
            if (segment.get("source_fps_num"), segment.get("source_fps_den")) != (spec["fps_num"], spec["fps_den"]):
                errors.append(f"{pid}: source FPS mismatch")
            if int(segment.get("source_out_frame_exclusive", 0)) > spec["frames"]:
                errors.append(f"{pid}: source range beyond end")
            if not normalized(segment.get("text", "")):
                errors.append(f"{pid}: empty exact speech")
    if len(ids) != len(set(ids)):
        errors.append("duplicate plan ID")
    return errors


def frames(path: Path, frame_numbers: list[int], output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    numbers = sorted(set(frame_numbers))
    if not numbers:
        return []
    targets = [output / f"frame_{number:07d}.jpg" for number in numbers]
    intervals = []
    first = last = numbers[0]
    for number in numbers[1:]:
        if number == last + 1:
            last = number
        else:
            intervals.append((first, last)); first = last = number
    intervals.append((first, last))
    expression = "+".join(f"between(n\\,{left}\\,{right})" for left, right in intervals)
    with tempfile.TemporaryDirectory(prefix="frames-", dir=output) as directory:
        pattern = Path(directory) / "selected_%06d.jpg"
        run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path),
             "-vf", f"select='{expression}',scale=360:-2", "-fps_mode", "passthrough",
             "-frames:v", str(len(numbers)), "-q:v", "3", str(pattern)])
        selected = sorted(Path(directory).glob("selected_*.jpg"))
        if len(selected) != len(numbers):
            raise RuntimeError(f"decoded frame count mismatch: {path} {len(selected)}/{len(numbers)}")
        for source, target in zip(selected, targets):
            os.replace(source, target)
    return [{"frame": number, **ref(target)} for number, target in zip(numbers, targets)]


def visual_boundary_metrics(visual: list[dict], start: int, end: int) -> dict:
    """Compare the first/last 13 frames and scan a half-second around each edge.

    A transition may leave the first few frames looking like the previous shot
    or arrive just before the last 13 frames. Preserve both sets of per-frame
    scores so Codex can inspect position, scale, and shot continuity.
    """
    by_frame = {row["frame"]: Path(row["path"]) for row in visual}

    def inspect(numbers: list[int], nearby: list[int]) -> dict:
        if len(numbers) < 2 or any(number not in by_frame for number in nearby):
            return {"decision": "reject", "reason": "boundary frame evidence incomplete"}
        pictures = {}
        for number in nearby:
            with Image.open(by_frame[number]) as picture:
                pictures[number] = np.asarray(picture.convert("RGB").resize((64, 114)), dtype=np.float32) / 255.0
        def differences(sequence: list[int]) -> list[float]:
            return [round(float(np.mean(np.abs(pictures[left] - pictures[right]))), 4)
                    for left, right in zip(sequence, sequence[1:])]
        adjacent = differences(numbers)
        nearby_adjacent = differences(nearby)
        endpoint_distance = round(float(np.mean(np.abs(pictures[numbers[0]] - pictures[numbers[-1]]))), 4)
        suspected = max(nearby_adjacent) > 0.07 or endpoint_distance > 0.12
        return {"frames": numbers, "adjacent_mean_absolute_differences": adjacent,
                "endpoint_distance": endpoint_distance, "largest_adjacent_difference": max(adjacent),
                "nearby_frames": nearby, "nearby_adjacent_mean_absolute_differences": nearby_adjacent,
                "largest_nearby_adjacent_difference": max(nearby_adjacent),
                "decision": "reject" if suspected else "pass"}

    head = list(range(start, min(end, start + 13)))
    tail = list(range(max(start, end - 13), end))
    nearby_count = min(30, max(13, (end - start) // 2))
    entry = inspect(head, list(range(start, min(end, start + nearby_count))))
    exit_ = inspect(tail, list(range(max(start, end - nearby_count), end)))
    return {"schema": "video-montage-visual-boundary/v260929", "entry": entry, "exit": exit_,
            "decision": "pass" if entry["decision"] == exit_["decision"] == "pass" else "reject"}


def plan_evidence(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"prepared", "copy_indexed", "repair_required", "plan_evidenced"}:
        raise ValueError("plan evidence requires source preparation")
    source_index = read(require_ref(value["source_index"], "source index"))
    order = read(require_ref(value["work_order"], "work order"))
    plan = read(args.plan)
    errors = check_plan(plan, source_index, int(order["requested_outputs"]))
    if errors:
        fail_round(job, value, "plan", errors)
    model = load_model()
    asset_copy_value = read(require_ref(value["asset_copy"], "asset copy")) if value.get("asset_copy") else None
    source_asr = {row["source"]["sha256"]: normalized(read(require_ref(row["asr"], "source ASR"))["asr"]["text"])
                  for row in source_index["sources"]}
    result = []
    for output in plan["outputs"]:
        pid = output["plan_id"]
        for index, segment in enumerate(output["segments"], 1):
            source = Path(segment["source_path"])
            fps = Fraction(segment["source_fps_num"], segment["source_fps_den"])
            start = segment["source_in_frame"] / float(fps)
            end = segment["source_out_frame_exclusive"] / float(fps)
            target = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "candidates" / pid / f"segment_{index:03d}" / "audio.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(source),
                 "-af", f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS",
                 "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)])
            samples = pcm(target)
            metrics = audio_metrics(samples)
            asr = transcribe(model, target)
            expected = normalized(segment["text"])
            actual = normalized(asr["text"])
            clean_gap = metrics["head_40ms_dbfs"] <= -36 and metrics["tail_40ms_dbfs"] <= -36
            asr_exact = bool(actual) and actual == expected
            source_text_match = expected in source_asr[segment["source_sha256"]]
            text_match = asr_exact or (source_text_match and context_corroborates_asr(
                segment["text"], asr["text"], asset_copy_value))
            transient_count = isolated_transients(samples)
            numbers = []
            for boundary in (segment["source_in_frame"], segment["source_out_frame_exclusive"]):
                numbers.extend(range(max(0, boundary - round(float(fps) * .5)),
                                     min(video(source)["frames"], boundary + round(float(fps) * .5))))
            visual = frames(source, numbers, target.parent / "frames")
            visual_boundary = visual_boundary_metrics(
                visual, segment["source_in_frame"], segment["source_out_frame_exclusive"])
            row = {"plan_id": pid, "segment_index": index, "candidate_id": segment.get("candidate_id"),
                   "source": ref(source), "pcm": ref(target), "frames": visual, "metrics": metrics,
                   "asr": asr, "expected_text": segment["text"], "asr_exact": asr_exact,
                   "text_match": text_match,
                   "source_text_match": source_text_match, "isolated_transients": transient_count,
                   "clean_gap": clean_gap, "visual_boundary": visual_boundary,
                   "decision": "pass" if text_match and source_text_match and clean_gap and metrics["clipped_samples"] == 0 and transient_count == 0 and visual_boundary["decision"] == "pass" else "reject"}
            result.append(row)
    evidence_path = job / "evidence" / f"plan_evidence_round_{value['repair_round'] + 1}.json"
    write(evidence_path, {"schema": "video-montage-plan-evidence/v260929", "plan": ref(args.plan),
                          "source_index": value["source_index"], "results": result})
    value["plan"] = ref(args.plan); value["plan_evidence"] = ref(evidence_path)
    failures = [f"{row['plan_id']}:{row['segment_index']}:ASR/PCM/visual boundary inconclusive" for row in result if row["decision"] != "pass"]
    if failures:
        fail_round(job, value, "candidate_evidence", failures)
    save(job, value, "plan_evidenced")


def approve_plan(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "plan_evidenced":
        raise ValueError("plan evidence required")
    evidence_path = require_ref(value["plan_evidence"], "plan evidence")
    evidence = read(evidence_path); review = read(args.review)
    if (review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "plan"
            or review.get("reviewer_role") != "codex" or review.get("evidence_sha256") != sha(evidence_path)
            or review.get("plan_sha256") != value["plan"]["sha256"]):
        raise ValueError("Codex plan review binding invalid")
    findings = review.get("segments", [])
    expected = {(row["plan_id"], row["segment_index"]) for row in evidence["results"]}
    actual = {(row.get("plan_id"), row.get("segment_index")) for row in findings}
    if len(findings) != len(expected) or actual != expected or any(row.get("semantic_pass") is not True or row.get("visual_pass") is not True or not row.get("reason") for row in findings):
        fail_round(job, value, "codex_plan_review", ["incomplete or failed visual/semantic findings"])
    finding_by_key = {(row["plan_id"], row["segment_index"]): row for row in findings}
    for row in evidence["results"]:
        if "visual_boundary" in row:
            finding = finding_by_key[(row["plan_id"], row["segment_index"])]
            if (finding.get("entry_visual_pass") is not True
                    or finding.get("exit_visual_pass") is not True
                    or not finding.get("boundary_reason")):
                fail_round(job, value, "codex_plan_review", ["missing first/last frame sequence review"])
    asset_copy_value = read(require_ref(value["asset_copy"], "asset copy")) if value.get("asset_copy") else {}
    source_index = read(require_ref(value["source_index"], "source index"))
    for row in evidence["results"]:
        if row["asr_exact"]:
            continue
        correction = finding_by_key[(row["plan_id"], row["segment_index"])].get("asr_correction")
        if (not isinstance(correction, dict) or correction.get("observed") != row["asr"]["text"]
                or correction.get("expected") != row["expected_text"]
                or not subtitle_change_supported(correction, asset_copy_value, source_index, value["plan"])):
            fail_round(job, value, "codex_plan_review", ["uncorroborated candidate ASR correction"])
    value["plan_review"] = ref(args.review)
    save(job, value, "plan_approved")


def render_clean(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "plan_approved":
        raise ValueError("Codex plan approval required")
    require_ref(value["plan_evidence"], "plan evidence")
    require_ref(value["plan_review"], "plan review")
    plan_path = require_ref(value["plan"], "plan")
    plan = read(plan_path)
    renderer = module("autonomous_renderer", "components/semantic/scripts/portable_frame_renderer.py")
    rows = []
    for output in plan["outputs"]:
        pid = output["plan_id"]
        unit = job / f"attempt-{value['repair_round'] + 1:02d}" / "plans" / f"{pid}.json"
        write(unit, {"segments": output["segments"], "transitions": output.get("transitions", [])})
        target = job / f"attempt-{value['repair_round'] + 1:02d}" / "clean" / f"{pid}.mp4"
        evidence_path = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "render" / f"{pid}.json"
        renderer.render(unit, target, evidence_path, 1440, 2560, 60, "libx264")
        rows.append({"plan_id": pid, "output": ref(target), "render_evidence": ref(evidence_path),
                     "expected_text": "".join(segment["text"] for segment in output["segments"])})
    clean = job / "manifests" / "clean_delivery.json"
    write(clean, {"schema": "video-montage-autonomous-clean/v260929", "decision": "pending_qc",
                  "plan": value["plan"], "plan_review": value["plan_review"], "results": rows})
    value["clean_delivery"] = ref(clean)
    save(job, value, "clean_rendered")


def clean_qc(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_rendered", "repair_required"}:
        raise ValueError("clean rendering required")
    clean_path = require_ref(value["clean_delivery"], "clean delivery"); clean = read(clean_path)
    model = load_model(); rows = []; failures = []
    copy_value = read(require_ref(value["asset_copy"], "asset copy"))
    for row in clean["results"]:
        output = require_ref(row["output"], "clean output")
        asr = transcribe(model, output)
        samples = pcm(output)
        metrics = audio_metrics(samples)
        cut_metrics = cut_pcm_metrics(samples, render_cut_frames(read(require_ref(row["render_evidence"], "render evidence"))), clean=True)
        expected = normalized(row["expected_text"]); actual = normalized(asr["text"])
        exact = bool(actual) and actual == expected
        match = exact or context_corroborates_asr(row["expected_text"], asr["text"], copy_value)
        if not match or metrics["clipped_samples"] or any(cut["decision"] != "pass" for cut in cut_metrics):
            failures.append(f"{row['plan_id']}: clean ASR/clipping/cut PCM mismatch")
        rows.append({"plan_id": row["plan_id"], "output": row["output"], "asr": asr,
                     "metrics": metrics, "cut_pcm": cut_metrics, "expected_text": row["expected_text"],
                     "asr_exact": exact, "text_match": match})
    report_path = job / "reports" / "clean_qc.json"
    write(report_path, {"schema": "video-montage-autonomous-clean-qc/v260929",
                        "clean_delivery": ref(clean_path), "results": rows,
                        "decision": "pass" if not failures else "reject", "failures": failures})
    value["clean_qc"] = ref(report_path)
    if failures:
        fail_round(job, value, "clean_qc", failures)
    save(job, value, "clean_validated")


def subtitle_draft(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_validated", "repair_required"} or not value.get("asset_copy") or not value.get("clean_qc"):
        raise ValueError("clean QC and Codex asset copy index required")
    if read(require_ref(value["clean_qc"], "clean QC")).get("decision") != "pass":
        raise ValueError("passing clean QC required")
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    packager = module("autonomous_packager", "components/packaging/scripts/package_video.py")
    output = attempt_dir(value)
    rows = []
    for row in clean["results"]:
        draft = packager.draft_one(require_ref(row["output"], "clean output"), row["plan_id"], output)
        source = Path(draft["subtitle_txt_path"])
        snapshot = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "subtitle_drafts" / source.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source.read_bytes())
        draft["subtitle_txt_path"] = str(snapshot.resolve())
        draft["subtitle_txt_sha256"] = sha(snapshot)
        rows.append(draft)
    report = output / "reports" / "subtitle_draft.json"
    write(report, {"schema": "video-montage-autonomous-subtitle-draft/v260929",
                   "clean_delivery": value["clean_delivery"], "asset_copy": value["asset_copy"], "results": rows})
    value["subtitle_draft"] = ref(report)
    save(job, value, "subtitle_drafted")


def subtitle_review(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "subtitle_drafted":
        raise ValueError("subtitle draft required")
    draft_path = require_ref(value["subtitle_draft"], "subtitle draft")
    draft = read(draft_path); review = read(args.review)
    if (review.get("schema") != "video-montage-codex-subtitle-review/v260929"
            or review.get("draft_sha256") != sha(draft_path)
            or review.get("asset_copy_sha256") != value["asset_copy"]["sha256"]):
        raise ValueError("subtitle review binding invalid")
    packager = module("autonomous_packager_subs", "components/packaging/scripts/package_video.py")
    asset_copy_value = read(require_ref(value["asset_copy"], "Codex asset copy"))
    source_index = read(require_ref(value["source_index"], "source index"))
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    clean_qc_value = read(require_ref(value["clean_qc"], "clean QC"))
    asr_by_id = {row["plan_id"]: row["asr"] for row in clean_qc_value["results"]}
    expected = {row["plan_id"]: normalized(row["expected_text"]) for row in clean["results"]}
    by_id = {row.get("plan_id"): row for row in review.get("results", [])}
    if set(by_id) != set(expected) or len(by_id) != len(review.get("results", [])):
        raise ValueError("subtitle review scope mismatch")
    revised = []
    for row in draft["results"]:
        pid = row["plan_id"]
        draft_sub = Path(row["subtitle_txt_path"])
        if sha(draft_sub) != row["subtitle_txt_sha256"]:
            raise ValueError("subtitle draft changed")
        duration = round(packager.video_spec(Path(row["input_path"]))["duration"] * 1000)
        before = packager.parse_srt(draft_sub, duration)
        finding = by_id[pid]
        cues = finding.get("cues", [])
        if len(cues) != len(before) or finding.get("draft_subtitle_sha256") != sha(draft_sub):
            raise ValueError("subtitle cue scope/binding mismatch")
        blocks = []
        cursor = 0
        previous_end = 0
        timings = character_times(asr_by_id[pid])
        shot_bounds = subtitle_segment_bounds(
            [{"text": cue["after"]} for cue in cues], plan_by_id[pid],
            read(require_ref(clean_by_id[pid]["render_evidence"], "render evidence")))
        for index, (old, correction) in enumerate(zip(before, cues), 1):
            original = old["text"]
            start, end = correction.get("start_ms"), correction.get("end_ms")
            if (correction.get("index") != index
                    or correction.get("draft_start_ms") != old["start_ms"]
                    or correction.get("draft_end_ms") != old["end_ms"]
                    or correction.get("before") != original):
                raise ValueError(f"{pid}: draft cue binding changed")
            after = correction.get("after")
            if not isinstance(after, str) or not after.strip() or (after != original and not subtitle_change_supported(
                    correction, asset_copy_value, source_index, value["plan"])):
                raise ValueError(f"{pid}: unsupported subtitle change")
            length = len(normalized(after))
            if (not isinstance(start, int) or not isinstance(end, int) or start < previous_end
                    or end <= start or cursor + length > len(timings)):
                raise ValueError(f"{pid}: invalid corrected subtitle timing")
            validate_subtitle_timing(start, end, timings[cursor],
                                     timings[cursor + length - 1], shot_bounds[index - 1])
            blocks.append(f"{index}\n{packager.format_timestamp(start)} --> {packager.format_timestamp(end)}\n{after}")
            previous_end = end
            cursor += length
        if cursor != len(timings):
            raise ValueError(f"{pid}: corrected cue words do not cover output ASR")
        if normalized("".join(cue["after"] for cue in cues)) != expected[pid]:
            raise ValueError(f"{pid}: subtitle words differ from exact source speech")
        corrected = attempt_dir(value) / "subtitles" / f"subtitle-{pid}.txt"
        corrected.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        packager.parse_srt(corrected, duration)
        revised.append({"plan_id": pid, "subtitle": ref(corrected), "draft": ref(draft_sub)})
    report = job / "reports" / "subtitle_review.json"
    write(report, {"schema": "video-montage-autonomous-subtitle-review/v260929",
                   "draft": ref(draft_path), "codex_review": ref(args.review), "results": revised,
                   "decision": "pass"})
    value["subtitle_review"] = ref(report)
    save(job, value, "subtitle_approved")


def package(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "subtitle_approved":
        raise ValueError("context-reviewed subtitles required")
    review = read(require_ref(value["subtitle_review"], "subtitle review"))
    config = read(args.config)
    by_id = {row["plan_id"]: row for row in review["results"]}
    if {row.get("plan_id") for row in config.get("outputs", [])} != set(by_id):
        raise ValueError("packaging config scope mismatch")
    for row in config["outputs"]:
        subtitle = require_ref(by_id[row["plan_id"]]["subtitle"], "corrected subtitle")
        supplied = Path(row.get("subtitle_txt", ""))
        if not supplied.is_absolute():
            supplied = args.config.parent / supplied
        if supplied.resolve() != subtitle or row.get("subtitle_sha256") != sha(subtitle):
            raise ValueError("packaging must use reviewed subtitle hash")
    packager = module("autonomous_packager_render", "components/packaging/scripts/package_video.py")
    manifest = attempt_dir(value) / "manifests" / "packaging_manifest.json"
    packager.render(args.config.resolve(), attempt_dir(value), manifest,
                    autonomous_clean=require_ref(value["clean_delivery"], "clean delivery"),
                    autonomous_clean_qc=require_ref(value["clean_qc"], "clean QC"))
    value["packaging_delivery"] = ref(manifest)
    value["packaging_config"] = ref(args.config)
    save(job, value, "packaged")


def final_evidence(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "packaged":
        raise ValueError("packaging required")
    manifest_path = require_ref(value["packaging_delivery"], "packaging delivery")
    manifest = read(manifest_path)
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    packager = module("autonomous_packager_final", "components/packaging/scripts/package_video.py")
    model = load_model(); rows = []; failures = []
    copy_value = read(require_ref(value["asset_copy"], "asset copy"))
    for row in manifest["results"]:
        pid = row["plan_id"]; output = Path(row["output_path"])
        if not output.is_file() or sha(output) != row["output_sha256"]:
            failures.append(f"{pid}: packaged output changed"); continue
        if row.get("input", {}).get("sha256") != clean_by_id[pid]["output"]["sha256"]:
            failures.append(f"{pid}: clean input binding mismatch"); continue
        asr = transcribe(model, output)
        output_audio = pcm(output)
        metrics = audio_metrics(output_audio)
        expected = normalized(clean_by_id[pid]["expected_text"])
        asr_exact = normalized(asr["text"]) == expected
        asr_match = asr_exact or context_corroborates_asr(clean_by_id[pid]["expected_text"], asr["text"], copy_value)
        clean_audio = pcm(require_ref(clean_by_id[pid]["output"], "clean output"))
        bgm_db = None
        if row.get("bgm"):
            music = pcm(Path(row["bgm"]["path"]))
            bgm_db = voice_over_music_db(clean_audio, music, float(row["bgm"]["gain_db"]))
        cues = packager.parse_srt(Path(row["subtitles"]["path"]), round(row["output_spec"]["duration"] * 1000))
        render_path = require_ref(clean_by_id[pid]["render_evidence"], "render evidence")
        render_value = read(render_path)
        cut_metrics = cut_pcm_metrics(output_audio, render_cut_frames(render_value), clean=False)
        try:
            shot_bounds = subtitle_segment_bounds(cues, plan_by_id[pid], render_value)
            subtitle_timing_pass = all(cue["start_ms"] >= shot_start and cue["end_ms"] <= shot_end
                                       for cue, (shot_start, shot_end) in zip(cues, shot_bounds))
        except ValueError:
            subtitle_timing_pass = False
        frame_numbers = [min(row["input_frames"] - 1, max(0, round((cue["start_ms"] + cue["end_ms"]) * 60 / 2000))) for cue in cues]
        for cue in cues:
            start_frame = round(cue["start_ms"] * 60 / 1000)
            end_frame = round(cue["end_ms"] * 60 / 1000)
            frame_numbers.extend([max(0, start_frame - 1), start_frame,
                                  min(row["input_frames"] - 1, end_frame - 1),
                                  min(row["input_frames"] - 1, end_frame)])
        for layer in [row.get("nameplate"), *row.get("text_pins", [])]:
            if layer:
                frame_numbers += [max(0, layer["start_frame"] - 1), layer["start_frame"],
                                  min(row["input_frames"] - 1, layer["end_frame_exclusive"] - 1),
                                  min(row["input_frames"] - 1, layer["end_frame_exclusive"])]
        cut_positions = [0]
        cumulative = 0
        for segment in render_value["segments"]:
            cumulative += segment["expected_output_frames"]
            cut_positions.append(cumulative)
        for cut in cut_positions:
            frame_numbers.extend(range(max(0, cut - 72), min(row["input_frames"], cut + 72)))
        frame_numbers += [0, row["input_frames"] - 1]
        visual = frames(output, frame_numbers, job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "packaged_frames" / pid)
        decision = (asr_match and subtitle_timing_pass and metrics["clipped_samples"] == 0
                    and all(cut["decision"] == "pass" for cut in cut_metrics)
                    and (bgm_db is None or bgm_db >= 6))
        if not decision:
            failures.append(f"{pid}: ASR/subtitle timing/clipping/BGM masking failure")
        rows.append({"plan_id": pid, "output": ref(output), "asr": asr, "asr_exact": asr_exact,
                     "asr_match": asr_match,
                     "metrics": metrics, "cut_pcm": cut_metrics, "voice_over_bgm_db": bgm_db, "frames": visual,
                     "subtitle_cue_count": len(cues), "subtitle_timing_pass": subtitle_timing_pass,
                     "decision": "pass" if decision else "reject"})
    report = job / "reports" / "final_evidence.json"
    write(report, {"schema": "video-montage-autonomous-final-evidence/v260929", "manifest": ref(manifest_path),
                   "subtitle_review": value["subtitle_review"], "results": rows,
                   "decision": "pass" if not failures else "reject", "failures": failures})
    value["final_evidence"] = ref(report)
    if failures:
        fail_round(job, value, "final_evidence", failures)
    save(job, value, "final_evidenced")


def audit_provenance(value: dict) -> None:
    """Rehash every source, candidate, review and delivered artifact at completion."""
    for name in ("work_order", "source_index", "asset_copy", "plan", "plan_evidence", "plan_review",
                 "clean_delivery", "clean_qc", "subtitle_draft", "subtitle_review", "packaging_config",
                 "packaging_delivery", "final_evidence"):
        require_ref(value[name], name)
    index = read(Path(value["source_index"]["path"]))
    assets = read(require_ref(index["asset_copy_sources"], "asset copy sources"))
    for row in assets["assets"]:
        require_ref(row, "source asset")
    for row in index["sources"]:
        require_ref(row["source"], "original source")
        require_ref(row["asr"], "original source ASR")
    for row in read(Path(value["plan_evidence"]["path"]))["results"]:
        require_ref(row["source"], "candidate source")
        require_ref(row["pcm"], "candidate PCM")
        for frame in row["frames"]:
            require_ref(frame, "candidate frame")
    for row in read(Path(value["clean_delivery"]["path"]))["results"]:
        require_ref(row["output"], "clean output")
        require_ref(row["render_evidence"], "clean render evidence")
    for row in read(Path(value["subtitle_review"]["path"]))["results"]:
        require_ref(row["draft"], "subtitle draft")
        require_ref(row["subtitle"], "reviewed subtitle")
    for row in read(Path(value["final_evidence"]["path"]))["results"]:
        require_ref(row["output"], "packaged output")
        for frame in row["frames"]:
            require_ref(frame, "packaged frame")


def complete(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "final_evidenced":
        raise ValueError("final evidence required")
    evidence_path = require_ref(value["final_evidence"], "final evidence")
    evidence = read(evidence_path); review = read(args.review)
    if (review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "final"
            or review.get("reviewer_role") != "codex" or review.get("evidence_sha256") != sha(evidence_path)):
        raise ValueError("Codex final visual review binding invalid")
    audit_provenance(value)
    by_id = {row.get("plan_id"): row for row in review.get("outputs", [])}
    if set(by_id) != {row["plan_id"] for row in evidence["results"]} or len(by_id) != len(review.get("outputs", [])):
        raise ValueError("final review scope mismatch")
    for row in evidence["results"]:
        finding = by_id[row["plan_id"]]
        if (finding.get("output_sha256") != row["output"]["sha256"]
                or finding.get("visual_pass") is not True or finding.get("subtitle_pass") is not True
                or finding.get("overlay_pass") is not True or not finding.get("reason")):
            fail_round(job, value, "codex_final_review", [f"{row['plan_id']}: incomplete visual finding"])
        require_ref(row["output"], "packaged output")
        for frame in row["frames"]:
            require_ref(frame, "reviewed packaged frame")
    packager = module("autonomous_packager_validate", "components/packaging/scripts/package_video.py")
    technical_path = job / "reports" / "packaging_technical.json"
    technical = packager.validate(require_ref(value["packaging_delivery"], "packaging delivery"), technical_path,
                                  autonomous_evidence=evidence_path, autonomous_review=args.review)
    if technical["decision"] != "pass":
        fail_round(job, value, "packaging_technical", technical["failures"])
    receipt = job / "video_montage_autonomous_completion.json"
    write(receipt, {"schema": "video-montage-autonomous-completion/v260929", "decision": "pass",
                    "review_mode": "codex_asr_pcm", "forced_alignment_claimed": False,
                    "human_listening_claimed": False, "work_order": value["work_order"],
                    "source_index": value["source_index"], "asset_copy": value["asset_copy"],
                    "plan": value["plan"], "plan_evidence": value["plan_evidence"],
                    "plan_review": value["plan_review"], "clean_delivery": value["clean_delivery"],
                    "clean_qc": value["clean_qc"], "subtitle_review": value["subtitle_review"],
                    "packaging_delivery": value["packaging_delivery"], "packaging_technical": ref(technical_path),
                    "final_evidence": value["final_evidence"], "final_review": ref(args.review),
                    "outputs": [row["output"] for row in evidence["results"]]})
    value["completion"] = ref(receipt)
    save(job, value, "complete")
    print(str(receipt))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "prepare", "asset-copy", "plan-evidence", "approve-plan", "render-clean",
                 "clean-qc", "subtitle-draft", "subtitle-review", "package", "final-evidence", "complete", "status"):
        action = sub.add_parser(name)
        action.add_argument("--job-dir", type=Path, required=True)
        if name == "init": action.add_argument("--work-order", type=Path, required=True)
        if name == "asset-copy": action.add_argument("--copy-text", type=Path, required=True)
        if name == "plan-evidence": action.add_argument("--plan", type=Path, required=True)
        if name in {"approve-plan", "subtitle-review", "complete"}: action.add_argument("--review", type=Path, required=True)
        if name == "package": action.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    actions = {"init": init, "prepare": prepare, "asset-copy": asset_copy, "plan-evidence": plan_evidence,
               "approve-plan": approve_plan, "render-clean": render_clean, "clean-qc": clean_qc,
               "subtitle-draft": subtitle_draft, "subtitle-review": subtitle_review, "package": package,
               "final-evidence": final_evidence, "complete": complete}
    if args.command == "status":
        print(json.dumps(state(args.job_dir.resolve()), ensure_ascii=False, indent=2))
        return
    try:
        actions[args.command](args)
    except Exception as error:
        if args.command not in {"init", "prepare"} and (args.job_dir / "autonomous_state.json").is_file():
            current = state(args.job_dir.resolve())
            if current.get("phase") not in {"repair_required", "failed", "complete"}:
                fail_round(args.job_dir.resolve(), current, args.command, [f"{type(error).__name__}: {error}"])
        raise


if __name__ == "__main__":
    main()
