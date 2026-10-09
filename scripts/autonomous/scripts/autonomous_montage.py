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
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "assets/dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "assets/dependencies/ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = ROOT / "assets/dependencies/models"
STATE_SCHEMA = "video-montage-autonomous-state/v260929"
ORDER_SCHEMA = "video-montage-autonomous-work-order/v260929"
PLAN_SCHEMA = "video-montage-autonomous-plan/v260929"
REVIEW_SCHEMA = "video-montage-codex-review/v260929"
MAX_ROUNDS = None  # Repair until the complete delivery passes; no retry ceiling.


def module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(value)
    return value


SOURCE_TIMING = module("autonomous_source_timing", "scripts/semantic/scripts/source_timing.py")
EDITING = module("autonomous_editing_policy", "scripts/autonomous/scripts/planning_policy.py")


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
    SOURCE_TIMING.require_constant_frame_rate(FFPROBE, path, stream)
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
    # A single digit and its Chinese spelling represent the same spoken token.
    # Multi-digit numbers remain untouched: 13 is not the spoken sequence 一三.
    text = re.sub(r"(?<!\d)[0-9](?!\d)", lambda m: "零一二三四五六七八九"[int(m[0])], str(text))
    return "".join(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", text)).casefold()


def subtitle_spelling(text: str) -> str:
    """Canonical spelling of a reviewed colloquial ASR phrase, not fuzzy matching.

    The raw source/clean/final ASR gates remain exact. Subtitle edits still need
    hash-bound source evidence and keep every character and timestamp accounted for.
    """
    return normalized(text).replace("真呆劲", "真带劲")


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
    if value.get("delivery_directory"):
        return Path(value["delivery_directory"])
    root = Path(value["output_root"])
    if value.get("delivery_layout") == "chinese/v1":
        return root
    return root / "attempt-01"


def processing_dir(value: dict) -> Path:
    return Path(value["pending_delivery_directory"]) if value.get("pending_delivery_directory") else attempt_dir(value)


def start_pending_delivery(job: Path, value: dict) -> Path:
    pending = job / f"attempt-{value['repair_round'] + 1:02d}" / "pending" / attempt_dir(value).name
    pending.mkdir(parents=True, exist_ok=True)
    value["pending_delivery_directory"] = str(pending.resolve())
    return pending


def bind_existing_delivery(value: dict) -> None:
    """Pin old jobs to the directory already holding their actual delivery."""
    if not value.get("delivery_directory") and value.get("output_root"):
        directory = attempt_dir(value)
        for key, rows_key, output_key in (("packaging_delivery", "results", "output_path"),
                                           ("clean_delivery", "results", "output")):
            if value.get(key):
                rows = read(require_ref(value[key], key)).get(rows_key, [])
                if rows:
                    output = rows[0][output_key]
                    path = Path(output["path"] if isinstance(output, dict) else output)
                    directory = path.parent.parent if value.get("delivery_layout") == "chinese/v1" else path.parent
                    break
        value["delivery_directory"] = str(directory.resolve())
    value["repair_delivery_policy"] = "overwrite"


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


def packaged_mix_evidence(output_audio: np.ndarray, clean_audio: np.ndarray,
                          music: np.ndarray | None, gain_db: float = 0, rate: int = 16000) -> dict:
    """Verify the actual decoded mix against its hash-bound, ASR-checked voice.

    A new word or a missing word creates a local residual and must fail even if
    whole-file correlation looks high. This is signal preservation evidence,
    never a claim that the packaged ASR transcript matched or was heard.
    """
    if len(output_audio) != len(clean_audio) or not len(clean_audio):
        return {"decision": "reject", "reason": "decoded voice duration changed"}
    expected = clean_audio.astype(np.float64).copy()
    if music is not None:
        if len(music) < len(expected):
            return {"decision": "reject", "reason": "looped music requires separate timing evidence"}
        expected += music[:len(expected)] * 10 ** (gain_db / 20)
    residual = output_audio - expected
    db = lambda x: 20 * math.log10(max(float(np.sqrt(np.mean(x * x))), 1e-8))
    windows = [db(residual[i:i + rate // 4]) for i in range(0, len(residual), rate // 4)]
    correlation = float(np.corrcoef(expected, output_audio)[0, 1])
    rms = db(residual); maximum = max(windows)
    passed = math.isfinite(correlation) and correlation >= .999 and rms <= -45 and maximum <= -38
    return {"decision": "pass" if passed else "reject", "correlation": round(correlation, 7),
            "residual_rms_dbfs": round(rms, 2), "worst_250ms_residual_dbfs": round(maximum, 2),
            "checked_samples": len(expected), "basis": "decoded_clean_voice_plus_hash_bound_bgm"}


def delivery_mix_evidence(output_audio: np.ndarray, row: dict) -> dict:
    """Rebuild the same full mix and tempo transform from authorized inputs."""
    packager = module("delivery_audio_packager", "scripts/packaging/scripts/package_video.py")
    if packager.delivery_speed(row) == 1.0:
        return packaged_mix_evidence(output_audio, pcm(Path(row["input"]["path"])),
            pcm(Path(row["bgm"]["path"])) if row.get("bgm") else None,
            float(row["bgm"]["gain_db"]) if row.get("bgm") else 0)
    require_ref(row["input"], "delivery audio input")
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", row["input"]["path"]]
    if row.get("bgm"):
        require_ref(row["bgm"], "delivery BGM")
        command += ["-stream_loop", "-1", "-i", row["bgm"]["path"]]
        filters = (f"[1:a]volume={row['bgm']['gain_db']}dB[m];"
                   "[0:a][m]amix=inputs=2:duration=first:normalize=0,")
    else:
        filters = "[0:a]"
    filters += "asetpts=PTS-STARTPTS,atempo=1.2,apad[a]"
    # Match encoder sample rate and channel negotiation before decoding to mono.
    with tempfile.TemporaryDirectory(prefix="montage-tempo-proof-") as directory:
        expected_path = Path(directory) / "expected.m4a"
        command += ["-filter_complex", filters, "-map", "[a]", "-vn", "-c:a", "aac",
                    "-b:a", "192k", "-ar", "48000", "-t",
                    f"{packager.final_frames(row['input_frames']) / 60:.6f}", str(expected_path)]
        run(command)
        expected = pcm(expected_path)
    proof = packaged_mix_evidence(output_audio, expected, None)
    proof["basis"] = "hash_bound_clean_mix_then_atempo_1.2"
    return proof


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


def rendered_plan_segments(plan_output: dict, render_value: dict) -> list[dict]:
    """Resolve each rendered shot's contiguous original segment indexes."""
    originals = plan_output["segments"]
    rendered = render_value["segments"]
    mapped = []
    covered = []
    for index, shot in enumerate(rendered, 1):
        indexes = shot.get("source_segment_indexes")
        if indexes is None:
            if len(originals) != len(rendered):
                raise ValueError("shot ASR plan/render scope mismatch")
            indexes = [index]
        if (not isinstance(indexes, list) or not indexes
                or any(type(i) is not int or i < 1 or i > len(originals) for i in indexes)):
            raise ValueError("invalid rendered source segment mapping")
        covered.extend(indexes)
        mapped.append({"text": "".join(originals[i - 1]["text"] for i in indexes)})
    if covered != list(range(1, len(originals) + 1)):
        raise ValueError("incomplete or replayed rendered source segment mapping")
    return mapped


def subtitle_segment_bounds(cues: list[dict], plan_output: dict, render_value: dict) -> list[tuple[int, int]]:
    """Bind each spoken cue to the actual rendered shot that contains its words."""
    segments = rendered_plan_segments(plan_output, render_value)
    rendered = render_value["segments"]
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


def transcribe(model, path: Path, expected: str | None = None, asset_copy: dict | None = None) -> dict:
    asr, _ = module("autonomous_asr", "scripts/semantic/scripts/v9_source_asr.py").transcribe(
        model, str(path), "zh", initial_prompt="以下是简体中文口播。")
    if expected and normalized(asr["text"]) != normalized(expected) and not context_corroborates_asr(expected, asr["text"], asset_copy):
        # Independent time augmentation, with no target words in the prompt.
        # Preserve both actual recognitions and scale word times back to the
        # candidate clock. This never changes the render or relaxes its gates.
        trials = [{"tempo": 1.0, "text": asr["text"]}]
        for tempo in (1.1, 1.3):
            probe = path.with_name(path.stem + f".asr-tempo{round(tempo * 100)}.wav")
            run([str(FFMPEG), "-v", "error", "-i", str(path), "-af", f"atempo={tempo}", "-y", str(probe)])
            alternative, _ = module("autonomous_asr_retry", "scripts/semantic/scripts/v9_source_asr.py").transcribe(
                model, str(probe), "zh", initial_prompt="以下是简体中文口播。")
            trials.append({"tempo": tempo, "audio": ref(probe), "text": alternative["text"]})
            if normalized(alternative["text"]) == normalized(expected) or context_corroborates_asr(expected, alternative["text"], asset_copy):
                for segment in alternative["segments"]:
                    for row in [segment, *segment.get("words", [])]:
                        row["start"] = round(row["start"] * tempo, 3)
                        row["end"] = round(row["end"] * tempo, 3)
                asr = alternative
                asr["recognition_tempo"] = tempo
                break
        asr["recognition_trials"] = trials
    return asr


def load_model():
    asr = module("autonomous_asr_model", "scripts/semantic/scripts/v9_source_asr.py")
    return asr.build_model("large-v3-turbo", MODEL_ROOT, "auto", "int8", "float16")[0]


def state(job: Path) -> dict:
    value = read(job / "autonomous_state.json")
    if value.get("schema") != STATE_SCHEMA or value.get("review_mode") != "codex_asr_pcm":
        raise ValueError("not a new autonomous job; legacy state cannot be migrated")
    order = read(require_ref(value["work_order"], "work order"))
    policy = order.get("planning_policy")
    if policy and (policy != EDITING.POLICY or value.get("planning_policy") != policy):
        raise ValueError("work-order planning policy changed or missing in state")
    return value


def semantic_first(value: dict) -> bool:
    policy = value.get("planning_policy")
    if policy is not None and policy != EDITING.POLICY:
        raise ValueError("unknown planning policy")
    if policy is None and value.get("plan"):
        plan = read(require_ref(value["plan"], "plan policy"))
        if plan.get("planning_policy") == EDITING.POLICY:
            raise ValueError("semantic-first plan cannot lose its job planning policy")
    return policy == EDITING.POLICY


def save(job: Path, value: dict, phase: str) -> None:
    value["phase"] = phase
    value.setdefault("events", []).append({"at": datetime.now(timezone.utc).isoformat(), "phase": phase})
    write(job / "autonomous_state.json", value)


def fail_round(job: Path, value: dict, phase: str, reasons: list[str]) -> None:
    count = int(value.get("repair_round", 0)) + 1
    value["repair_round"] = count
    value["last_failures"] = reasons
    value["continue_until_complete"] = True
    value["max_repair_rounds"] = MAX_ROUNDS
    save(job, value, "repair_required")
    write(job / "reports" / f"repair_round_{count}.json",
          {"schema": "video-montage-autonomous-repair/v260929", "phase": phase,
           "round": count, "max_rounds": MAX_ROUNDS, "failures": reasons,
           "decision": "repair_required",
           "continuation_authorization": value.get("continuation_authorization")})
    raise RuntimeError(f"{phase}: {'; '.join(reasons)}; repair round {count}; repair required until complete")


def repair(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    bind_existing_delivery(value)
    value.pop("pending_delivery_directory", None)
    value.pop("reburn_only", None)
    value["planning_policy"] = EDITING.POLICY
    for key in ("plan", "plan_evidence", "plan_review", "editing_context", "batch_diversity", "diversity_selection",
                "clean_delivery", "clean_qc", "subtitle_draft", "subtitle_review", "packaging_delivery", "packaging_config"):
        value.pop(key, None)
    if value.pop("compact_delivery", False):
        for key in ("source_index", "asset_copy", "plan", "plan_evidence", "plan_review",
                    "batch_diversity", "clean_delivery", "clean_qc", "subtitle_draft",
                    "subtitle_review", "packaging_delivery", "packaging_config"):
            value.pop(key, None)
    if args.continue_until_complete:
        if not args.authorization or not args.authorization.strip():
            raise ValueError("explicit user continuation authorization required")
        value["continue_until_complete"] = True
        value["continuation_authorization"] = args.authorization
    for key in ("completion", "final_evidence", "packaging_technical", "final_review"):
        value.pop(key, None)
    (job / "video_montage_autonomous_completion.json").unlink(missing_ok=True)
    (job / "reports" / "packaging_technical.json").unlink(missing_ok=True)
    if value.get("output_root"):
        module("repair_layout", "scripts/packaging/scripts/package_video.py").invalidate_delivery_receipts(attempt_dir(value))
    try:
        fail_round(job, value, "requested_repair", [args.reason])
    except RuntimeError:
        if value["phase"] == "failed":
            raise
    print(json.dumps({"phase": value["phase"], "repair_round": value["repair_round"]}, ensure_ascii=False))


def init(args) -> None:
    order = read(args.work_order)
    sources = order.get("sources")
    if order.get("schema") != ORDER_SCHEMA or not isinstance(sources, list) or not sources or int(order.get("requested_outputs", 0)) < 1:
        raise ValueError("new autonomous work order required")
    if order.get("planning_policy", EDITING.POLICY) != EDITING.POLICY:
        raise ValueError("new jobs require semantic-continuity/v1")
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
    parent = Path(order.get("output_root", ROOT / "work")).resolve()
    if parent.name != "work":
        raise ValueError("output_root must be the work directory")
    packager = module("init_output_layout", "scripts/packaging/scripts/package_video.py")
    if args.job_dir is not None:
        job = args.job_dir.resolve()
        output = job.parent
        if (output.parent != parent or not packager.OUTPUT_NAME.fullmatch(output.name)
                or job != packager.runtime_directory(output)):
            raise ValueError("job-dir must be work/自动化混剪_xx/临时文件")
    else:
        output = parent / ("自动化混剪_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S"))
        job = packager.runtime_directory(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing non-empty delivery directory: {output}")
    if job.exists() and any(job.iterdir()):
        raise FileExistsError(f"refusing non-empty runtime directory: {job}")
    job.mkdir(parents=True)
    packager.ensure_delivery_layout(output)
    order_snapshot = job / "work_order.json"
    write(order_snapshot, {**order, "planning_policy": EDITING.POLICY})
    save(job, {"schema": STATE_SCHEMA, "review_mode": "codex_asr_pcm", "work_order": ref(order_snapshot),
               "source_hashes": {str(Path(row["path"]).resolve()): sha(Path(row["path"])) for row in sources},
               "asset_root": str(asset), "output_root": str(output), "delivery_directory": str(output),
               "repair_delivery_policy": "overwrite", "delivery_layout": "chinese/v1", "records_policy": "delivery-temporary/v1",
               "temporary_root": str(output / "临时文件"), "repair_round": 0,
               "packaging_design_policy": "codex/v1",
               "planning_policy": EDITING.POLICY,
               "max_repair_rounds": MAX_ROUNDS, "continue_until_complete": True}, "initialized")
    print(json.dumps({"job_dir": str(job), "delivery_directory": str(output)}, ensure_ascii=False))


def prepare(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"initialized", "prepared", "repair_required"}:
        raise ValueError("prepare requires initialized job")
    value["planning_policy"] = EDITING.POLICY
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


def check_plan(plan: dict, source_index: dict, count: int, policy: str | None = None) -> list[str]:
    errors = []
    if plan.get("schema") != PLAN_SCHEMA or len(plan.get("outputs", [])) != count:
        return ["plan schema or complete output scope"]
    source_map = {row["source"]["sha256"]: row for row in source_index["sources"]}
    ids = []
    rejected = read(ROOT / "references/semantic/wuzimu-v20-invalid-intervals.json")["entries"]
    rejection_gate = module("autonomous_rejected_intervals", "scripts/semantic/scripts/v20_fail_closed.py")
    for output in plan["outputs"]:
        pid = output.get("plan_id")
        ids.append(pid)
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
            errors.append("unsafe plan ID")
        segments = output.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append(f"{pid}: empty segments")
            continue
        frame_gate = module("auto_frame_gate", "scripts/semantic/scripts/v20_frame_plan_gate.py")
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
            if all(type(segment.get(key)) is int for key in ("source_in_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den")) and segment["source_fps_num"] > 0 and segment["source_fps_den"] > 0:
                candidate = {**segment, "source_in": segment["source_in_frame"] * segment["source_fps_den"] / segment["source_fps_num"],
                             "source_out": segment["source_out_frame_exclusive"] * segment["source_fps_den"] / segment["source_fps_num"]}
                for entry in rejected:
                    if rejection_gate.interval_hits(candidate, entry):
                        errors.append(f"{pid}: rejected original source interval: {entry['entry_id']}")
    if len(ids) != len({pid.casefold() if isinstance(pid, str) else None for pid in ids}):
        errors.append("duplicate plan ID")
    if policy is not None and policy != EDITING.POLICY:
        errors.append("unknown planning policy")
    if policy == EDITING.POLICY or plan.get("planning_policy") == EDITING.POLICY:
        if plan.get("planning_policy") != EDITING.POLICY:
            errors.append("semantic-continuity/v1 plan required")
        if not errors:
            for output in plan["outputs"]:
                try:
                    errors.extend(f"{output['plan_id']}:{error}" for error in EDITING.audit_output(output))
                except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
                    errors.append(f"{output['plan_id']}: invalid editorial annotations: {error}")
    return errors


def require_diversity(value: dict, evidence: dict) -> dict | None:
    reference = evidence.get("batch_diversity")
    if not reference:  # Existing evidence/receipts retain their original contract.
        return None
    if reference != value.get("batch_diversity"):
        raise ValueError("batch diversity evidence binding changed")
    report = read(require_ref(reference, "batch diversity"))
    if report.get("plan") != value["plan"]:
        raise ValueError("batch diversity does not describe the approved plan")
    require_ref(report["plan"], "diversity plan")
    if report.get("options"):
        require_ref(report["options"], "coherent option pool")
    return report


def frames(path: Path, frame_numbers: list[int], output: Path, *, width: int = 360) -> list[dict]:
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
             "-vf", f"select='{expression}',scale={width}:-2", "-fps_mode", "passthrough",
             "-frames:v", str(len(numbers)), "-q:v", "3", str(pattern)])
        selected = sorted(Path(directory).glob("selected_*.jpg"))
        if len(selected) != len(numbers):
            raise RuntimeError(f"decoded frame count mismatch: {path} {len(selected)}/{len(numbers)}")
        for source, target in zip(selected, targets):
            os.replace(source, target)
    return [{"frame": number, **ref(target)} for number, target in zip(numbers, targets)]


def verify_continuous_visual(reference: dict, source: dict, start: int, end: int) -> dict:
    evidence = read(require_ref(reference, "continuous visual evidence"))
    if (evidence.get("schema") != "video-montage-continuous-visual/v1" or evidence.get("source") != source
            or evidence.get("source_in_frame") != start or evidence.get("source_out_frame_exclusive") != end
            or evidence.get("width") != 960 or evidence.get("decision") != "pending_review"):
        raise ValueError("continuous visual evidence source/range changed")
    rows = evidence.get("frames", [])
    if (not isinstance(rows, list) or len(rows) != end - start
            or any(not isinstance(row, dict) or type(row.get("frame")) is not int
                   or row["frame"] != number for number, row in zip(range(start, end), rows))):
        raise ValueError("continuous visual evidence must cover every selected native frame")
    checked = set()
    for row in rows:
        identity = (row.get("path"), row.get("sha256"))
        if identity not in checked:
            require_ref(row, "continuous native frame")
            checked.add(identity)
    for sheet in evidence.get("contact_sheets", []):
        require_ref(sheet, "continuous frame navigation sheet")
    return evidence


def continuous_visual(path: Path, start: int, end: int, directory: Path) -> dict:
    """Cache every selected native frame; sheets only help navigate originals."""
    source = ref(path)
    target = directory / source["sha256"] / f"{start:09d}_{end:09d}" / "frames.json"
    if target.exists():
        reference = ref(target)
        verify_continuous_visual(reference, source, start, end)
        return reference
    target.parent.mkdir(parents=True, exist_ok=True)
    visual = frames(path, list(range(start, end)), target.parent / "frames", width=960)
    sheets = []
    for offset in range(0, len(visual), 16):
        selected = visual[offset:offset + 16]
        sheet = Image.new("RGB", (960, 1840), "#171717")
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(selected):
            x, y = (index % 4) * 240, (index // 4) * 460
            with Image.open(row["path"]) as picture:
                picture.thumbnail((240, 432))
                sheet.paste(picture, (x, y + 26))
            draw.text((x + 4, y + 5), f"frame {row['frame']}", fill="white")
        sheet_path = target.parent / f"sheet_{offset // 16 + 1:05d}.jpg"
        sheet.save(sheet_path, quality=90)
        sheets.append({"first_frame": selected[0]["frame"], "last_frame": selected[-1]["frame"], **ref(sheet_path)})
    write(target, {"schema": "video-montage-continuous-visual/v1", "source": source,
                   "source_in_frame": start, "source_out_frame_exclusive": end, "width": 960,
                   "frames": visual, "contact_sheets": sheets, "decision": "pending_review"})
    return ref(target)


def require_editing_approval(value: dict) -> None:
    """Recheck editorial approvals before rendering and publishing new edits."""
    if not semantic_first(value):
        return
    plan_path = require_ref(value["plan"], "semantic-first plan")
    plan = read(plan_path)
    errors = check_plan(plan, read(require_ref(value["source_index"], "source index")),
                        int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]), EDITING.POLICY)
    if errors:
        raise ValueError("semantic-first plan rejected: " + "; ".join(errors))
    if value.get("reburn_only"):
        context = read(require_ref(value["editing_context"], "retained clean editing authorization"))
        if (context.get("planning_policy") != EDITING.POLICY or context.get("decision") != "pass"
                or context.get("plan") != value["plan"] or context.get("clean_delivery") != value["clean_delivery"]
                or context.get("outputs") != [EDITING.summary(output) for output in plan["outputs"]]):
            raise ValueError("retained clean editing authorization changed")
        return
    evidence_path = require_ref(value["plan_evidence"], "semantic-first plan evidence")
    evidence = read(evidence_path)
    review = read(require_ref(value["plan_review"], "semantic-first plan review"))
    if (evidence.get("planning_policy") != EDITING.POLICY or evidence.get("plan") != value["plan"]
            or evidence.get("source_index") != value["source_index"]
            or review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "plan"
            or review.get("reviewer_role") != "codex" or review.get("planning_policy") != EDITING.POLICY
            or review.get("evidence_sha256") != sha(evidence_path) or review.get("plan_sha256") != sha(plan_path)):
        raise ValueError("semantic-first editorial approval binding invalid")
    errors = EDITING.plan_review_errors(plan, evidence, review)
    if errors:
        raise ValueError("semantic-first editorial approval rejected: " + "; ".join(errors))
    segments = {(output["plan_id"], index): segment for output in plan["outputs"]
                for index, segment in enumerate(output["segments"], 1)}
    for row in evidence["results"]:
        segment = segments[(row["plan_id"], row["segment_index"])]
        if row.get("decision") != "pass":
            errors.append("candidate evidence failed")
        if row.get("source") != ref(Path(segment["source_path"])):
            errors.append("candidate source does not describe the selected source")
        verify_continuous_visual(row["continuous_visual"], row["source"],
                                 segment["source_in_frame"], segment["source_out_frame_exclusive"])
    if errors:
        raise ValueError("semantic-first editorial approval rejected: " + "; ".join(errors))


def final_editing_evidence(job: Path, value: dict, output: dict, path: Path, count: int) -> dict:
    errors = EDITING.frame_errors(count, final=True)
    if errors:
        raise ValueError("; ".join(errors))
    summary = EDITING.summary(output)
    summary["final_frames"] = count
    for shot in summary["visual_shots"]:
        shot["final_in_frame"] = (shot["output_in_frame"] * 5 + 5) // 6
        shot["final_out_frame_exclusive"] = (shot["output_out_frame_exclusive"] * 5 + 5) // 6
    return {"editing": summary, "continuous_visual": continuous_visual(
        path, 0, count, job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "final_continuous")}


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
    value["planning_policy"] = EDITING.POLICY
    for key in ("plan_review", "final_review", "completion", "clean_delivery", "clean_qc", "subtitle_review",
                "packaging_delivery", "final_evidence", "editing_context", "batch_diversity", "diversity_selection"):
        value.pop(key, None)
    errors = check_plan(plan, source_index, int(order["requested_outputs"]), EDITING.POLICY)
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
                 "-af", f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,volume={float(segment.get('audio_gain_db', 0.0)):.6f}dB",
                 "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)])
            samples = pcm(target)
            metrics = audio_metrics(samples)
            asr = transcribe(model, target, segment["text"], asset_copy_value)
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
            continuous = continuous_visual(source, segment["source_in_frame"], segment["source_out_frame_exclusive"],
                                           job / "evidence" / "source_continuous")
            visual_boundary = visual_boundary_metrics(
                visual, segment["source_in_frame"], segment["source_out_frame_exclusive"])
            row = {"plan_id": pid, "segment_index": index, "candidate_id": segment.get("candidate_id"),
                   "source": ref(source), "pcm": ref(target), "frames": visual, "metrics": metrics,
                   "continuous_visual": continuous,
                   "asr": asr, "expected_text": segment["text"], "asr_exact": asr_exact,
                   "text_match": text_match,
                   "source_text_match": source_text_match, "isolated_transients": transient_count,
                   "clean_gap": clean_gap, "visual_boundary": visual_boundary,
                   "decision": "pass" if text_match and source_text_match and clean_gap and metrics["clipped_samples"] == 0 and transient_count == 0 and visual_boundary["decision"] == "pass" else "reject"}
            result.append(row)
    evidence_path = job / "evidence" / f"plan_evidence_round_{value['repair_round'] + 1}.json"
    write(evidence_path, {"schema": "video-montage-plan-evidence/v260929", "plan": ref(args.plan),
                          "source_index": value["source_index"], "planning_policy": EDITING.POLICY,
                          "outputs": [EDITING.summary(output) for output in plan["outputs"]],
                          "results": result})
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
    diversity_report = require_diversity(value, evidence)
    if (review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "plan"
            or review.get("reviewer_role") != "codex" or review.get("evidence_sha256") != sha(evidence_path)
            or review.get("plan_sha256") != value["plan"]["sha256"]):
        raise ValueError("Codex plan review binding invalid")
    if not semantic_first(value) and diversity_report and (not isinstance(review.get("diversity_reason"), str)
                             or not review["diversity_reason"].strip()):
        raise ValueError("Codex must explain batch diversity and any necessary reuse")
    if semantic_first(value):
        if evidence.get("planning_policy") != EDITING.POLICY or review.get("planning_policy") != EDITING.POLICY:
            raise ValueError("semantic-first review required; legacy approval cannot authorize this edit")
        plan = read(require_ref(value["plan"], "plan"))
        policy_errors = check_plan(plan, read(require_ref(value["source_index"], "source index")),
                                  int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]), EDITING.POLICY)
        if policy_errors:
            fail_round(job, value, "codex_whole_edit_review", policy_errors)
        policy_errors += EDITING.plan_review_errors(plan, evidence, review)
        if policy_errors:
            fail_round(job, value, "codex_whole_edit_review", policy_errors)
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
    require_editing_approval(value)
    save(job, value, "plan_approved")


def render_clean(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "plan_approved":
        raise ValueError("Codex plan approval required")
    require_editing_approval(value)
    require_ref(value["plan_evidence"], "plan evidence")
    require_ref(value["plan_review"], "plan review")
    plan_path = require_ref(value["plan"], "plan")
    plan = read(plan_path)
    start_pending_delivery(job, value)
    renderer = module("autonomous_renderer", "scripts/semantic/scripts/portable_frame_renderer.py")
    rows = []
    for output in plan["outputs"]:
        pid = output["plan_id"]
        unit = job / f"attempt-{value['repair_round'] + 1:02d}" / "plans" / f"{pid}.json"
        write(unit, {"segments": output["segments"], "transitions": output.get("transitions", [])})
        if value.get("delivery_layout") == "chinese/v1":
            packager = module("autonomous_layout", "scripts/packaging/scripts/package_video.py")
            packager.ensure_delivery_layout(processing_dir(value))
            target = packager.delivery_category(processing_dir(value), "clean") / f"{pid}.mp4"
        else:
            target = processing_dir(value) / "clean" / f"{pid}.mp4"
        evidence_path = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "render" / f"{pid}.json"
        rendered = renderer.render(unit, target, evidence_path, 1440, 2560, 60, "libx264", overwrite=True)
        if semantic_first(value):
            errors = EDITING.render_errors(output, rendered, rendered["actual_output_frames"])
            if errors:
                fail_round(job, value, "clean_render_constraints", [f"{pid}:{error}" for error in errors])
        rows.append({"plan_id": pid, "output": ref(target), "render_evidence": ref(evidence_path),
                     "expected_text": "".join(segment["text"] for segment in output["segments"])})
    clean = processing_dir(value) / "临时文件" / "manifests" / "clean_delivery.json"
    write(clean, {"schema": "video-montage-autonomous-clean/v260929", "decision": "pending_qc",
                  "plan": value["plan"], "plan_review": value["plan_review"], "results": rows,
                  **({"planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"]} if semantic_first(value) else {})})
    value["clean_delivery"] = ref(clean)
    save(job, value, "clean_rendered")


def rendered_shot_asr(model, output: Path, rendered: dict, planned: dict,
                      copy_value: dict, directory: Path) -> dict:
    """Recheck clean speech per rendered shot and anchor word times to that shot.

    Whole-video ASR may assign a new shot's first word to the previous shot's
    silence. Keep that original ASR as evidence; independently verify every
    rendered audio range before using its word timestamps for subtitles.
    """
    planned_segments = rendered_plan_segments(planned, rendered)
    directory.mkdir(parents=True, exist_ok=True)
    cursor = 0
    segments = []
    texts = []
    observations = []
    for index, (shot, original) in enumerate(zip(rendered["segments"], planned_segments)):
        end = cursor + shot["expected_output_frames"]
        target = directory / f"shot_{index + 1:03d}.wav"
        run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(output),
             "-af", f"atrim=start={cursor / 60:.9f}:end={end / 60:.9f},asetpts=PTS-STARTPTS",
             "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)])
        observed = transcribe(model, target, original["text"], copy_value)
        observations.append({"shot_index": index + 1, "audio": ref(target), "asr": observed})
        if (normalized(observed["text"]) != normalized(original["text"])
                and not context_corroborates_asr(original["text"], observed["text"], copy_value)):
            raise ValueError(f"shot {index + 1}: rendered speech differs from source plan")
        texts.append(observed["text"])
        for segment in observed["segments"]:
            shifted = dict(segment)
            shifted["start"] = float(segment["start"]) + cursor / 60
            shifted["end"] = float(segment["end"]) + cursor / 60
            shifted["words"] = [{**word, "start": float(word["start"]) + cursor / 60,
                                  "end": float(word["end"]) + cursor / 60}
                                 for word in segment.get("words", [])]
            segments.append(shifted)
        cursor = end
    return {"text": " ".join(texts), "segments": segments,
            "shot_observations": observations,
            "timing_basis": "verified_rendered_shot_asr", "output": ref(output)}


def clean_qc(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_rendered", "repair_required"}:
        raise ValueError("clean rendering required")
    require_editing_approval(value)
    clean_path = require_ref(value["clean_delivery"], "clean delivery"); clean = read(clean_path)
    model = load_model(); rows = []; failures = []
    copy_value = read(require_ref(value["asset_copy"], "asset copy"))
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
    for row in clean["results"]:
        output = require_ref(row["output"], "clean output")
        if semantic_first(value):
            spec = video(output)
            rendered = read(require_ref(row["render_evidence"], "render evidence"))
            errors = EDITING.render_errors(plan_by_id[row["plan_id"]], rendered, spec["frames"])
            if (spec["fps_num"], spec["fps_den"]) != (60, 1):
                errors.append("CLEAN_REQUIRES_60_FPS")
            if errors:
                failures.extend(f"{row['plan_id']}:{error}" for error in errors)
                continue
        asr = transcribe(model, output)
        samples = pcm(output)
        metrics = audio_metrics(samples)
        cut_metrics = cut_pcm_metrics(samples, render_cut_frames(read(require_ref(row["render_evidence"], "render evidence"))), clean=True)
        expected = normalized(row["expected_text"]); actual = normalized(asr["text"])
        exact = bool(actual) and actual == expected
        match = exact or context_corroborates_asr(row["expected_text"], asr["text"], copy_value)
        if not match or metrics["clipped_samples"] or any(cut["decision"] != "pass" for cut in cut_metrics):
            failures.append(f"{row['plan_id']}: clean ASR/clipping/cut PCM mismatch")
        timing_asr = rendered_shot_asr(model, output,
            read(require_ref(row["render_evidence"], "render evidence")), plan_by_id[row["plan_id"]],
            copy_value, job / "evidence" / "clean_shot_asr" / row["plan_id"])
        rows.append({"plan_id": row["plan_id"], "output": row["output"], "asr": asr,
                     "timing_asr": timing_asr,
                     "metrics": metrics, "cut_pcm": cut_metrics, "expected_text": row["expected_text"],
                     "asr_exact": exact, "text_match": match})
    report_path = job / "reports" / "clean_qc.json"
    write(report_path, {"schema": "video-montage-autonomous-clean-qc/v260929",
                        "clean_delivery": ref(clean_path), "results": rows,
                        **({"planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"]} if semantic_first(value) else {}),
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
    packager = module("autonomous_packager", "scripts/packaging/scripts/package_video.py")
    output = processing_dir(value)
    rows = []
    for row in clean["results"]:
        draft = packager.draft_one(require_ref(row["output"], "clean output"), row["plan_id"], output, overwrite=True)
        source = Path(draft["subtitle_txt_path"])
        snapshot = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "subtitle_drafts" / source.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source.read_bytes())
        draft["subtitle_txt_path"] = str(snapshot.resolve())
        draft["subtitle_txt_sha256"] = sha(snapshot)
        rows.append(draft)
    report = packager.delivery_category(output, "reports") / "subtitle_draft.json"
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
    packager = module("autonomous_packager_subs", "scripts/packaging/scripts/package_video.py")
    asset_copy_value = read(require_ref(value["asset_copy"], "Codex asset copy"))
    source_index = read(require_ref(value["source_index"], "source index"))
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    clean_qc_value = read(require_ref(value["clean_qc"], "clean QC"))
    asr_by_id = {row["plan_id"]: row.get("timing_asr", row["asr"]) for row in clean_qc_value["results"]}
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
        if subtitle_spelling("".join(cue["after"] for cue in cues)) != subtitle_spelling(expected[pid]):
            raise ValueError(f"{pid}: subtitle words differ from exact source speech")
        corrected = module("autonomous_subtitle_layout", "scripts/packaging/scripts/package_video.py").subtitle_output(processing_dir(value), pid)
        corrected.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        packager.parse_srt(corrected, duration)
        revised.append({"plan_id": pid, "subtitle": ref(corrected), "draft": ref(draft_sub)})
    report = job / "reports" / "subtitle_review.json"
    write(report, {"schema": "video-montage-autonomous-subtitle-review/v260929",
                   "draft": ref(draft_path), "codex_review": ref(args.review), "results": revised,
                   "decision": "pass"})
    value["subtitle_review"] = ref(report)
    if value.get("reburn_config"):
        config_path = require_ref(value["reburn_config"], "reburn configuration")
        config = read(config_path)
        by_id = {row["plan_id"]: row for row in revised}
        for row in config["outputs"]:
            subtitle = by_id[row["plan_id"]]["subtitle"]
            row["subtitle_txt"] = subtitle["path"]
            row["subtitle_sha256"] = subtitle["sha256"]
            if row.get("subtitle_design"):
                cues = packager.parse_srt(Path(subtitle["path"]), round(packager.video_spec(Path(row["input_path"]))["duration"] * 1000))
                row["subtitle_design"] = packager.packaging_design.refresh(row["subtitle_design"], cues, subtitle["sha256"])
        write(config_path, config)
        value["reburn_config"] = ref(config_path)
    save(job, value, "subtitle_approved")


def package(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"subtitle_approved", "repair_required"}:
        raise ValueError("context-reviewed subtitles required")
    if semantic_first(value):
        require_editing_approval(value)
        require_clean_qc(value)
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
    packager = module("autonomous_packager_render", "scripts/packaging/scripts/package_video.py")
    if any(not isinstance(row.get("subtitle_design"), dict) for row in config["outputs"]):
        raise ValueError("every packaging output requires a Codex-authored subtitle_design; legacy configurations are unsupported")
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    asset_copy = read(require_ref(value["asset_copy"], "verified asset copy"))
    order = read(require_ref(value["work_order"], "work order"))
    for prepared in packager.prepared_rows(args.config.resolve()):
        if prepared.get("subtitle_design"):
            packager.packaging_design.verify_sources(prepared["subtitle_design"], prepared["cues"], asset_copy,
                clean_by_id[prepared["plan_id"]]["expected_text"], json.dumps(order.get("packaging_request", ""), ensure_ascii=False))
    manifest = packager.delivery_category(processing_dir(value), "manifests") / "packaging_manifest.json"
    packager.render(args.config.resolve(), processing_dir(value), manifest,
                    autonomous_clean=require_ref(value["clean_delivery"], "clean delivery"),
                    autonomous_clean_qc=require_ref(value["clean_qc"], "clean QC"), overwrite=True)
    value["packaging_delivery"] = ref(manifest)
    value["packaging_config"] = ref(Path(read(manifest)["config_snapshot_path"]))
    value["delivery_mode"] = "packaged"
    save(job, value, "packaged")


def final_evidence(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if getattr(args, "clean", False):
        clean_final_evidence(job, value)
        return
    if value["phase"] not in {"packaged", "repair_required"}:
        raise ValueError("packaging required")
    require_editing_approval(value)
    if semantic_first(value):
        require_clean_qc(value)
    manifest_path = require_ref(value["packaging_delivery"], "packaging delivery")
    manifest = read(manifest_path)
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    clean_qc_value = read(require_ref(value["clean_qc"], "clean QC"))
    if clean_qc_value.get("decision") != "pass":
        raise ValueError("packaged speech preservation requires passing clean QC")
    clean_qc_by_id = {row["plan_id"]: row for row in clean_qc_value["results"]}
    packager = module("autonomous_packager_final", "scripts/packaging/scripts/package_video.py")
    model = load_model(); rows = []; failures = []
    copy_value = read(require_ref(value["asset_copy"], "asset copy"))
    for row in manifest["results"]:
        pid = row["plan_id"]; output = Path(row["output_path"])
        if not output.is_file() or sha(output) != row["output_sha256"]:
            failures.append(f"{pid}: packaged output changed"); continue
        if row.get("input", {}).get("sha256") != clean_by_id[pid]["output"]["sha256"]:
            failures.append(f"{pid}: clean input binding mismatch"); continue
        editing = {}
        if semantic_first(value):
            spec = packager.video_spec(output)
            expected_frames = packager.final_frames(video(require_ref(clean_by_id[pid]["output"], "clean input"))["frames"])
            if row.get("final_speed") != packager.FINAL_SPEED or spec["frames"] != expected_frames:
                failures.append(f"{pid}: final speed/frame count changed"); continue
            errors = EDITING.frame_errors(spec["frames"], final=True)
            if errors:
                failures.extend(f"{pid}:{error}" for error in errors); continue
            editing = final_editing_evidence(job, value, plan_by_id[pid], output, spec["frames"])
        asr = transcribe(model, output)
        output_audio = pcm(output)
        metrics = audio_metrics(output_audio)
        expected = normalized(clean_by_id[pid]["expected_text"])
        asr_exact = normalized(asr["text"]) == expected
        asr_match = asr_exact or context_corroborates_asr(clean_by_id[pid]["expected_text"], asr["text"], copy_value)
        clean_audio = pcm(require_ref(clean_by_id[pid]["output"], "clean output"))
        bgm_db = None
        music = None
        if row.get("bgm"):
            music = pcm(Path(row["bgm"]["path"]))
            bgm_db = voice_over_music_db(clean_audio, music, float(row["bgm"]["gain_db"]))
        mix_evidence = delivery_mix_evidence(output_audio, row)
        speech_preserved = mix_evidence["decision"] == "pass" and clean_qc_by_id[pid]["text_match"]
        source_cues = packager.parse_srt(Path(row["subtitles"]["path"]), round(row["input_frames"] / 60 * 1000))
        cues = packager.delivery_cues(row)
        speed = packager.delivery_speed(row)
        output_frames = row["output_spec"]["frames"]
        render_path = require_ref(clean_by_id[pid]["render_evidence"], "render evidence")
        render_value = read(render_path)
        cut_metrics = cut_pcm_metrics(output_audio, [math.ceil(cut / speed) for cut in render_cut_frames(render_value)], clean=False)
        try:
            source_bounds = subtitle_segment_bounds(source_cues, plan_by_id[pid], render_value)
            shot_bounds = [(round(start / speed), round(end / speed)) for start, end in source_bounds]
            subtitle_timing_pass = all(cue["start_ms"] >= shot_start and cue["end_ms"] <= shot_end
                                       for cue, (shot_start, shot_end) in zip(cues, shot_bounds))
        except ValueError:
            subtitle_timing_pass = False
        frame_numbers = [min(output_frames - 1, max(0, round((cue["start_ms"] + cue["end_ms"]) * 60 / 2000))) for cue in cues]
        for cue in cues:
            start_frame = min(output_frames - 1, round(cue["start_ms"] * 60 / 1000))
            end_frame = min(output_frames, round(cue["end_ms"] * 60 / 1000))
            frame_numbers.extend([max(0, start_frame - 1), start_frame,
                                  min(output_frames - 1, end_frame - 1),
                                  min(output_frames - 1, end_frame)])
        for layer in [row.get("nameplate"), *row.get("text_pins", [])]:
            if layer:
                start = min(output_frames - 1, math.ceil(layer["start_frame"] / speed))
                end = min(output_frames - 1, math.ceil(layer["end_frame_exclusive"] / speed))
                frame_numbers += [max(0, start - 1), start, max(0, end - 1), end]
        cut_positions = [0]
        cumulative = 0
        for segment in render_value["segments"]:
            cumulative += segment["expected_output_frames"]
            cut_positions.append(math.ceil(cumulative / speed))
        for cut in cut_positions:
            frame_numbers.extend(range(max(0, cut - 72), min(output_frames, cut + 72)))
        if row.get("design"):
            frame_numbers += packager.packaging_design.evidence_frames(row["design"], speed, output_frames)
        frame_numbers += [0, output_frames - 1]
        visual = frames(output, frame_numbers, job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "packaged_frames" / pid)
        decision = ((asr_match or speech_preserved) and subtitle_timing_pass and metrics["clipped_samples"] == 0
                    and all(cut["decision"] == "pass" for cut in cut_metrics)
                    and (bgm_db is None or bgm_db >= 6))
        if not decision:
            failures.append(f"{pid}: ASR/subtitle timing/clipping/BGM masking failure")
        rows.append({"plan_id": pid, "output": ref(output), "asr": asr, "asr_exact": asr_exact,
                     **editing,
                     "asr_match": asr_match,
                     "speech_preserved": speech_preserved, "mix_evidence": mix_evidence,
                     "metrics": metrics, "cut_pcm": cut_metrics, "voice_over_bgm_db": bgm_db, "frames": visual,
                     "subtitle_cue_count": len(cues), "subtitle_timing_pass": subtitle_timing_pass,
                     "design_required": bool(row.get("design")),
                     **({"design": row["design"]} if row.get("design") else {}),
                     "decision": "pass" if decision else "reject"})
    report = job / "reports" / "final_evidence.json"
    write(report, {"schema": "video-montage-autonomous-final-evidence/v260929", "manifest": ref(manifest_path),
                   "subtitle_review": value["subtitle_review"], "results": rows,
                   **({"planning_policy": EDITING.POLICY, "plan": value["plan"]} if semantic_first(value) else {}),
                   "decision": "pass" if not failures else "reject", "failures": failures})
    value["final_evidence"] = ref(report)
    if failures:
        fail_round(job, value, "final_evidence", failures)
    save(job, value, "final_evidenced")


def require_clean_qc(value: dict) -> tuple[dict, dict]:
    clean_path = require_ref(value["clean_delivery"], "clean delivery")
    clean = read(clean_path)
    qc = read(require_ref(value["clean_qc"], "clean QC"))
    order = read(require_ref(value["work_order"], "work order"))
    by_id = {row["plan_id"]: row for row in qc.get("results", [])}
    ids = [row["plan_id"] for row in clean.get("results", [])]
    if (qc.get("decision") != "pass" or qc.get("clean_delivery") != ref(clean_path)
            or len(ids) != int(order["requested_outputs"]) or len(set(ids)) != len(ids)
            or len(by_id) != len(qc.get("results", [])) or set(by_id) != set(ids)):
        raise ValueError("complete hash-bound clean QC required")
    if semantic_first(value):
        if (clean.get("planning_policy") != EDITING.POLICY or qc.get("planning_policy") != EDITING.POLICY
                or clean.get("plan_sha256") != value["plan"]["sha256"] or qc.get("plan_sha256") != value["plan"]["sha256"]):
            raise ValueError("semantic-first clean QC binding invalid")
        plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
    for row in clean["results"]:
        measured = by_id[row["plan_id"]]
        require_ref(row["output"], "clean output")
        require_ref(row["render_evidence"], "clean render evidence")
        if semantic_first(value):
            spec = video(Path(row["output"]["path"]))
            errors = EDITING.render_errors(plan_by_id[row["plan_id"]], read(Path(row["render_evidence"]["path"])), spec["frames"])
            if errors or (spec["fps_num"], spec["fps_den"]) != (60, 1):
                raise ValueError("semantic-first clean render constraints failed: " + "; ".join(errors))
        if (measured.get("output") != row["output"] or measured.get("text_match") is not True
                or measured.get("metrics", {}).get("clipped_samples") != 0
                or not isinstance(measured.get("cut_pcm"), list)
                or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])):
            raise ValueError("clean QC does not authorize this output")
    return clean, qc


def clean_final_evidence(job: Path, value: dict) -> None:
    if value["phase"] != "clean_validated":
        raise ValueError("clean final evidence requires passing clean QC")
    require_editing_approval(value)
    clean, qc = require_clean_qc(value)
    rows = []; deliveries = []; failures = []
    packager = module("clean_final_speed", "scripts/packaging/scripts/package_video.py")
    model = load_model()
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]} if semantic_first(value) else {}
    for row in clean["results"]:
        source = require_ref(row["output"], "clean output")
        delivery = packager.speed_clean(source, processing_dir(value) / "临时文件" / "final_clean" / f"{row['plan_id']}.mp4")
        delivery["plan_id"] = row["plan_id"]
        deliveries.append(delivery)
        output = Path(delivery["output_path"])
        render = read(require_ref(row["render_evidence"], "render evidence"))
        count = delivery["output_frames"]
        editing = {}
        if semantic_first(value):
            measured = packager.video_spec(output)["frames"]
            errors = EDITING.frame_errors(measured, final=True)
            if measured != count:
                errors.append("FINAL_DECODED_FRAME_COUNT_CHANGED")
            if errors:
                failures.extend(f"{row['plan_id']}:{error}" for error in errors); continue
            editing = final_editing_evidence(job, value, plan_by_id[row["plan_id"]], output, measured)
        cuts = [math.ceil(cut / packager.FINAL_SPEED) for cut in render_cut_frames(render)]
        numbers = {0, count - 1}
        for cut in [0, *cuts, count]:
            numbers.update(range(max(0, cut - 72), min(count, cut + 72)))
        visual = frames(output, sorted(numbers), job / "evidence" / "clean_final_frames" / row["plan_id"])
        audio = pcm(output); metrics = audio_metrics(audio)
        cut_metrics = cut_pcm_metrics(audio, cuts, clean=True)
        asr = transcribe(model, output)
        asr_match = normalized(asr["text"]) == normalized(row["expected_text"])
        proof = delivery_mix_evidence(audio, delivery)
        passed = ((asr_match or proof["decision"] == "pass") and metrics["clipped_samples"] == 0
                  and all(cut["decision"] == "pass" for cut in cut_metrics))
        if not passed:
            failures.append(f"{row['plan_id']}: accelerated clean audio QC")
        rows.append({"plan_id": row["plan_id"], "output": ref(output), "frames": visual,
                     **editing,
                     "asr": asr, "asr_match": asr_match, "mix_evidence": proof,
                     "metrics": metrics, "cut_pcm": cut_metrics, "decision": "pass" if passed else "reject"})
    final_path = job / "manifests" / "final_clean_delivery.json"
    write(final_path, {"schema": "video-montage-final-clean/v1", "clean_delivery": value["clean_delivery"],
                       "final_speed": packager.FINAL_SPEED, "results": deliveries})
    value["final_clean_delivery"] = ref(final_path)
    path = job / "reports" / "final_evidence.json"
    write(path, {"schema": "video-montage-autonomous-final-evidence/v260929", "delivery_mode": "clean",
                 "clean_delivery": value["clean_delivery"], "clean_qc": value["clean_qc"],
                 "final_clean_delivery": value["final_clean_delivery"],
                 **({"planning_policy": EDITING.POLICY, "plan": value["plan"]} if semantic_first(value) else {}),
                 "results": rows, "decision": "reject" if failures else "pass", "failures": failures})
    value["final_evidence"] = ref(path)
    value["delivery_mode"] = "clean"
    if failures:
        fail_round(job, value, "final_evidence", failures)
    save(job, value, "final_evidenced")


def reburn(args) -> None:
    """Start a fresh subtitle review from the retained, verified clean inputs."""
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "complete" or not value.get("reburn_inputs"):
        raise ValueError("completed delivery with retained reburn inputs required")
    inputs = value["reburn_inputs"]
    for key in ("plan", "source_index", "asset_copy", "clean_delivery", "clean_qc", "packaging_delivery"):
        require_ref(inputs[key], f"reburn {key}")
        value[key] = inputs[key]
    if semantic_first(value):
        value["editing_context"] = inputs["editing_context"]
        require_ref(value["editing_context"], "retained editing authorization")
    clean, qc = require_clean_qc(value)
    previous = read(require_ref(value["packaging_delivery"], "previous packaging"))
    if args.plan_id not in {row["plan_id"] for row in previous["results"]}:
        raise ValueError("unknown reburn plan ID")
    edited = args.subtitle_txt.resolve()
    if not edited.is_file():
        raise FileNotFoundError(edited)
    value["repair_round"] = int(value.get("repair_round", 0)) + 1
    value.pop("compact_delivery", None)
    value.pop("completion", None)
    value["reburn_only"] = True
    require_editing_approval(value)
    value["delivery_mode"] = "packaged"
    pending = start_pending_delivery(job, value)
    packager = module("autonomous_reburn_packager", "scripts/packaging/scripts/package_video.py")
    packager.ensure_delivery_layout(pending)
    by_id = {row["plan_id"]: row for row in clean["results"]}
    drafts = []
    for row in previous["results"]:
        pid = row["plan_id"]
        source = edited if pid == args.plan_id else require_ref(row["subtitle_snapshot"], "unchanged subtitle")
        duration = round(packager.video_spec(require_ref(by_id[pid]["output"], "clean output"))["duration"] * 1000)
        packager.parse_srt(source, duration)
        snapshot = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "subtitle_drafts" / packager.subtitle_filename(pid)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, snapshot)
        drafts.append({"plan_id": pid, "input_path": by_id[pid]["output"]["path"],
                       "subtitle_txt_path": str(snapshot), "subtitle_txt_sha256": sha(snapshot)})
    draft_path = job / "reports" / "subtitle_draft.json"
    write(draft_path, {"schema": "video-montage-autonomous-subtitle-draft/v260929",
                       "clean_delivery": value["clean_delivery"], "asset_copy": value["asset_copy"], "results": drafts})
    value["subtitle_draft"] = ref(draft_path)
    config = read(Path(previous["config_snapshot_path"]))
    packager.require_design_config(config)
    if sha(Path(previous["config_snapshot_path"])) != previous["config_snapshot_sha256"]:
        raise ValueError("reburn configuration changed")
    config_path = pending / "临时文件" / "config" / "reburn_config.json"
    write(config_path, config)
    value["reburn_config"] = ref(config_path)
    (job / "video_montage_autonomous_completion.json").unlink(missing_ok=True)
    save(job, value, "subtitle_drafted")
    print(json.dumps({"draft": str(draft_path), "config": str(config_path), "next": "subtitle-review"}, ensure_ascii=False))


def publish_delivery(job: Path, value: dict) -> list[dict]:
    """Publish only bytes authorized by the final review; keep reusable bindings local."""
    pending = processing_dir(value).resolve()
    delivery = attempt_dir(value).resolve()
    if pending == delivery and value.get("delivery_mode") != "clean":
        return [row["output"] for row in read(require_ref(value["final_evidence"], "final evidence"))["results"]]
    packager = module("autonomous_publisher", "scripts/packaging/scripts/package_video.py")
    clean, qc = require_clean_qc(value)
    staging = job / f"attempt-{value['repair_round'] + 1:02d}" / "publication"
    staging.mkdir(parents=True, exist_ok=True)
    target_manifests = (delivery / "临时文件" if value.get("delivery_layout") == "chinese/v1" else job) / "manifests"
    copies = []

    def relocate(item):
        if isinstance(item, dict):
            return {key: relocate(row) for key, row in item.items()}
        if isinstance(item, list):
            return [relocate(row) for row in item]
        if isinstance(item, str):
            path = Path(item)
            if path.is_absolute() and path.is_relative_to(pending):
                return str(delivery / path.relative_to(pending))
        return item

    published_clean = {"schema": clean["schema"], "decision": "pass", "results": [],
                       **({"planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"]} if semantic_first(value) else {})}
    for row in clean["results"]:
        source = require_ref(row["output"], "authorized clean output")
        target = (target_manifests / "clean_inputs" / f"{row['plan_id']}.mp4"
                  if value.get("delivery_mode") == "clean" else Path(relocate(str(source))))
        if source != target:
            copies.append((source, target))
        timeline = read(require_ref(row["render_evidence"], "render evidence"))
        timeline = {key: timeline[key] for key in ("schema", "segments", "actual_output_frames", "expected_output_frames")}
        timeline.update(export_path=str(target), export_sha256=row["output"]["sha256"])
        timeline_path = staging / f"render-{row['plan_id']}.json"
        timeline_target = target_manifests / timeline_path.name
        write(timeline_path, timeline)
        copies.append((timeline_path, timeline_target))
        published_clean["results"].append({**row, "output": {"path": str(target), "sha256": row["output"]["sha256"]},
                                          "render_evidence": {"path": str(timeline_target), "sha256": sha(timeline_path)}})
    clean_path = staging / "clean_delivery.json"
    clean_target = target_manifests / clean_path.name
    write(clean_path, published_clean)
    clean_ref = {"path": str(clean_target), "sha256": sha(clean_path)}
    copies.append((clean_path, clean_target))
    qc = relocate(qc)
    qc["clean_delivery"] = clean_ref
    for measured in qc["results"]:
        clean_row = next(row for row in published_clean["results"] if row["plan_id"] == measured["plan_id"])
        measured["output"] = clean_row["output"]
        if measured.get("timing_asr"):
            measured["timing_asr"]["output"] = clean_row["output"]
    qc_path = staging / "clean_validation.json"
    qc_target = target_manifests / qc_path.name
    write(qc_path, qc)
    qc_ref = {"path": str(qc_target), "sha256": sha(qc_path)}
    copies.append((qc_path, qc_target))
    outputs = [row["output"] for row in published_clean["results"]]
    published = clean_ref
    if value.get("delivery_mode") == "clean":
        final = read(require_ref(value["final_clean_delivery"], "accelerated clean delivery"))
        final["clean_delivery"] = clean_ref
        outputs = []
        for row in final["results"]:
            target = packager.delivery_category(delivery, "clean") / f"{row['plan_id']}.mp4"
            copies.append((Path(row["output_path"]), target))
            row["output_path"] = str(target)
            row["input"] = next(item["output"] for item in published_clean["results"] if item["plan_id"] == row["plan_id"])
            outputs.append({"path": str(target), "sha256": row["output_sha256"]})
        final_path = staging / "final_clean_delivery.json"
        final_target = target_manifests / final_path.name
        write(final_path, final)
        copies.append((final_path, final_target))
        published = {"path": str(final_target), "sha256": sha(final_path)}
    else:
        packaged = read(require_ref(value["packaging_delivery"], "packaging delivery"))
        final = relocate(packaged)
        for old, row in zip(packaged["results"], final["results"]):
            copies.append((Path(old["output_path"]), Path(row["output_path"])))
            copies.append((Path(old["subtitle_snapshot"]["path"]), Path(row["subtitle_snapshot"]["path"])))
        original_config = Path(packaged["config_snapshot_path"])
        # Retain task-created artwork with the same atomic batch publication.
        assets = original_config.parent / "assets"
        if assets.exists():
            for asset in assets.rglob("*"):
                if asset.is_file():
                    copies.append((asset, Path(relocate(str(asset)))))
        config_path = staging / "packaging_config.json"
        write(config_path, relocate(read(original_config)))
        config_target = Path(final["config_snapshot_path"])
        copies.append((config_path, config_target))
        final.update(config_path=str(config_target), config_sha256=sha(config_path),
                     config_snapshot_path=str(config_target), config_snapshot_sha256=sha(config_path),
                     clean_delivery_path=str(clean_target), clean_delivery_sha256=clean_ref["sha256"],
                     controller_validation_path=str(qc_target), controller_validation_sha256=qc_ref["sha256"])
        package_path = staging / "packaging_manifest.json"
        package_target = target_manifests / package_path.name
        write(package_path, final)
        copies.append((package_path, package_target))
        published = {"path": str(package_target), "sha256": sha(package_path)}
        outputs = [{"path": row["output_path"], "sha256": row["output_sha256"]} for row in final["results"]]
    packager.publish_files(copies, job)
    value["published_delivery"] = published
    value["published_clean"] = clean_ref
    value["published_clean_qc"] = qc_ref
    for row in outputs:
        require_ref(row, "published authorized output")
    return outputs


def audit_provenance(value: dict) -> None:
    """Rehash every source, candidate, review and delivered artifact at completion."""
    names = ["work_order", "source_index", "asset_copy", "plan", "clean_delivery", "clean_qc", "final_evidence"]
    if not value.get("reburn_only"):
        names += ["plan_evidence", "plan_review"]
    if value.get("delivery_mode") != "clean":
        names += ["subtitle_draft", "subtitle_review", "packaging_config", "packaging_delivery"]
    for name in names:
        require_ref(value[name], name)
    require_editing_approval(value)
    index = read(Path(value["source_index"]["path"]))
    assets = read(require_ref(index["asset_copy_sources"], "asset copy sources"))
    for row in assets["assets"]:
        require_ref(row, "source asset")
    for row in index["sources"]:
        require_ref(row["source"], "original source")
        require_ref(row["asr"], "original source ASR")
    if value.get("reburn_only"):
        require_clean_qc(value)
        errors = check_plan(read(require_ref(value["plan"], "reburn plan")), index,
                            int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]),
                            EDITING.POLICY if semantic_first(value) else None)
        if errors:
            raise ValueError(f"reburn source plan rejected: {errors}")
    else:
        for row in read(Path(value["plan_evidence"]["path"]))["results"]:
            require_ref(row["source"], "candidate source")
            require_ref(row["pcm"], "candidate PCM")
            for frame in row["frames"]:
                require_ref(frame, "candidate frame")
        require_diversity(value, read(Path(value["plan_evidence"]["path"])))
    for row in read(Path(value["clean_delivery"]["path"]))["results"]:
        require_ref(row["output"], "clean output")
        require_ref(row["render_evidence"], "clean render evidence")
    if value.get("delivery_mode") != "clean":
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
    if evidence.get("decision", "pass") != "pass":
        raise ValueError("passing final evidence required")
    if semantic_first(value):
        if (evidence.get("planning_policy") != EDITING.POLICY or evidence.get("plan") != value["plan"]
                or review.get("planning_policy") != EDITING.POLICY):
            raise ValueError("semantic-first final review binding invalid")
        plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
        require_clean_qc(value)
    audit_provenance(value)
    by_id = {row.get("plan_id"): row for row in review.get("outputs", [])}
    if set(by_id) != {row["plan_id"] for row in evidence["results"]} or len(by_id) != len(review.get("outputs", [])):
        raise ValueError("final review scope mismatch")
    for row in evidence["results"]:
        finding = by_id[row["plan_id"]]
        if semantic_first(value):
            output = plan_by_id[row["plan_id"]]
            errors = EDITING.output_review_errors(output, finding)
            count = video(require_ref(row["output"], "final output"))["frames"]
            errors.extend(EDITING.frame_errors(count, final=True))
            expected = EDITING.summary(output)
            expected["final_frames"] = count
            for shot in expected["visual_shots"]:
                shot["final_in_frame"] = (shot["output_in_frame"] * 5 + 5) // 6
                shot["final_out_frame_exclusive"] = (shot["output_out_frame_exclusive"] * 5 + 5) // 6
            if (row.get("editing") != expected
                    or finding.get("continuous_visual_sha256") != row.get("continuous_visual", {}).get("sha256")):
                errors.append("FINAL_EDITING_EVIDENCE_CHANGED")
            verify_continuous_visual(row["continuous_visual"], row["output"], 0, count)
            if errors:
                fail_round(job, value, "codex_final_editing_review", errors)
        required_passes = ("visual_pass",) if value.get("delivery_mode") == "clean" else ("visual_pass", "subtitle_pass", "overlay_pass")
        if (finding.get("output_sha256") != row["output"]["sha256"]
                or any(finding.get(key) is not True for key in required_passes) or not finding.get("reason")
                or (row.get("design_required") and not module("design_review_gate", "scripts/packaging/scripts/package_video.py").packaging_design.review_ok(row.get("design"), finding))):
            fail_round(job, value, "codex_final_review", [f"{row['plan_id']}: incomplete visual finding"])
        require_ref(row["output"], "packaged output")
        for frame in row["frames"]:
            require_ref(frame, "reviewed packaged frame")
    packager = module("autonomous_packager_validate", "scripts/packaging/scripts/package_video.py")
    technical_path = job / "reports" / "packaging_technical.json"
    if value.get("delivery_mode") == "clean":
        clean, _ = require_clean_qc(value)
        if evidence.get("clean_delivery") != value["clean_delivery"] or evidence.get("clean_qc") != value["clean_qc"]:
            raise ValueError("clean final evidence binding changed")
        final_path = require_ref(value["final_clean_delivery"], "accelerated clean delivery")
        final = read(final_path)
        if (evidence.get("final_clean_delivery") != ref(final_path)
                or final.get("clean_delivery") != value["clean_delivery"]
                or final.get("final_speed") != packager.FINAL_SPEED
                or len(final.get("results", [])) != len(clean["results"])
                or {row["plan_id"] for row in final["results"]} != {row["plan_id"] for row in clean["results"]}):
            raise ValueError("accelerated clean binding changed")
        by_clean = {row["plan_id"]: row for row in clean["results"]}
        by_evidence = {row["plan_id"]: row for row in evidence["results"]}
        failures = []
        for row in final["results"]:
            output = require_ref({"path": row["output_path"], "sha256": row["output_sha256"]}, "accelerated clean output")
            spec = packager.video_spec(output)
            original = by_clean[row["plan_id"]]
            render = read(require_ref(original["render_evidence"], "render evidence"))
            measured = by_evidence[row["plan_id"]]
            if (row["input"] != original["output"] or row.get("final_speed") != packager.FINAL_SPEED
                    or row.get("input_frames") != render["actual_output_frames"]
                    or measured.get("output") != ref(output) or measured.get("decision") != "pass"
                    or measured.get("metrics", {}).get("clipped_samples") != 0
                    or not isinstance(measured.get("cut_pcm"), list)
                    or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])
                    or (measured.get("asr_match") is not True and delivery_mix_evidence(pcm(output), row)["decision"] != "pass")):
                failures.append(f"{row['plan_id']}: accelerated clean evidence")
            if ((spec["width"], spec["height"]) != (1440, 2560)
                    or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac"
                    or not packager.packaged_streams_ok(spec) or spec["frames"] != packager.final_frames(render["actual_output_frames"])):
                failures.append(f"{row['plan_id']}: clean specification")
            run([str(FFMPEG), "-v", "error", "-i", str(output), "-f", "null", "NUL"])
        technical = {"decision": "reject" if failures else "pass", "failures": failures, "delivery_mode": "clean"}
        write(technical_path, technical)
    else:
        package_path = require_ref(value["packaging_delivery"], "packaging delivery")
        packaged_rows = read(package_path).get("results", [])
        if not packaged_rows or any(row.get("final_speed") != packager.FINAL_SPEED for row in packaged_rows):
            raise ValueError("final 1.2x packaging required; rerender and obtain new final evidence")
        technical = packager.validate(package_path, technical_path,
                                      autonomous_evidence=evidence_path, autonomous_review=args.review)
    if technical["decision"] != "pass":
        fail_round(job, value, "packaging_technical", technical["failures"])
    published_outputs = publish_delivery(job, value)
    receipt = job / "video_montage_autonomous_completion.json"
    write(receipt, {"schema": "video-montage-autonomous-completion/v260929", "decision": "pass",
                    **({"planning_policy": EDITING.POLICY} if semantic_first(value) else {}),
                    "repair_round_count": value.get("repair_round", 0),
                    "continuation_authorization": value.get("continuation_authorization"),
                    "review_mode": "codex_asr_pcm", "forced_alignment_claimed": False,
                    "human_listening_claimed": False, "work_order": value["work_order"],
                    "source_index": value["source_index"], "asset_copy": value["asset_copy"],
                    "plan": value["plan"], "delivery_mode": value.get("delivery_mode", "packaged"),
                    "reburn_only": value.get("reburn_only", False),
                    **{key: value[key] for key in ("plan_evidence", "plan_review", "subtitle_review", "packaging_delivery", "published_delivery") if value.get(key)},
                    **({"batch_diversity": value["batch_diversity"]} if value.get("batch_diversity") else {}),
                    "clean_delivery": value["clean_delivery"],
                    "clean_qc": value["clean_qc"], "packaging_technical": ref(technical_path),
                    "final_evidence": value["final_evidence"], "final_review": ref(args.review),
                    "outputs": published_outputs})
    value["completion"] = ref(receipt)
    save(job, value, "complete")
    if value.get("delivery_layout") == "chinese/v1":
        output = attempt_dir(value)
        packager.ensure_delivery_layout(output)
    if value.get("records_policy") == "delivery-temporary/v1":
        compact_delivery(job, value)
    print(str(job / "autonomous_state.json"))


def compact_delivery(job: Path, value: dict) -> None:
    """Retain only inputs needed for reburn or restarting a full repair."""
    root = job.resolve()
    keep = {root / "autonomous_state.json", require_ref(value["work_order"], "work order")}
    for folder in (root / "config", root / "manifests"):
        if folder.exists():
            keep.update(path.resolve() for path in folder.rglob("*") if path.is_file())
    # Reburn's render gate still needs the bound clean manifest and its QC receipt.
    package_reference = value.get("published_delivery") or value.get("packaging_delivery")
    packaging_path = require_ref(package_reference, "published delivery")
    packaging = read(packaging_path)
    if packaging.get("controller_validation_path"):
        previous_qc = Path(packaging["controller_validation_path"]).resolve()
        reusable_qc = root / "manifests" / "clean_validation.json"
        reusable_qc.parent.mkdir(parents=True, exist_ok=True)
        if previous_qc != reusable_qc:
            shutil.copyfile(previous_qc, reusable_qc)
        packaging["controller_validation_path"] = str(reusable_qc)
        write(packaging_path, packaging)
        keep.add(reusable_qc)
    if packaging.get("config_snapshot_path"):
        snapshot = Path(packaging["config_snapshot_path"]).resolve()
        if not snapshot.is_file() or sha(snapshot) != packaging["config_snapshot_sha256"]:
            raise ValueError("retained packaging configuration changed")
        packaging["config_path"] = str(snapshot)
        packaging["config_sha256"] = sha(snapshot)
        write(packaging_path, packaging)
        keep.add(snapshot)

    for key in ("clean_delivery_path", "controller_validation_path"):
        if packaging.get(key):
            keep.add(Path(packaging[key]).resolve())
    retained = {key: value[key] for key in (
        "schema", "review_mode", "work_order", "source_hashes", "asset_root", "output_root",
        "delivery_directory", "repair_delivery_policy", "delivery_layout", "records_policy",
        "temporary_root", "repair_round", "max_repair_rounds", "continue_until_complete", "packaging_design_policy",
        "continuation_authorization", "planning_policy") if key in value}
    retained.update(phase="complete", compact_delivery=True)
    retained["delivery_mode"] = value.get("delivery_mode", "packaged")
    inputs = {}
    for key in ("plan", "asset_copy", "source_index"):
        if value.get(key):
            source = require_ref(value[key], key)
            target = root / "manifests" / f"reburn_{key}.json"
            document = read(source)
            if key == "source_index":
                for index, row in enumerate(document["sources"], 1):
                    asr = require_ref(row["asr"], "source ASR")
                    destination = root / "manifests" / "source_asr" / f"source_{index:03d}.json"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if asr != destination:
                        shutil.copyfile(asr, destination)
                    row["asr"] = ref(destination)
                    keep.add(destination)
                assets = require_ref(document["asset_copy_sources"], "asset copy sources")
                destination = root / "manifests" / "asset_copy_sources.json"
                if assets != destination:
                    shutil.copyfile(assets, destination)
                document["asset_copy_sources"] = ref(destination)
                keep.add(destination)
            if key == "source_index":
                write(target, document)
            elif source != target:
                shutil.copyfile(source, target)
            inputs[key] = ref(target)
            keep.add(target)
    inputs["packaging_delivery"] = ref(packaging_path)
    for key, preferred, field in (("clean_delivery", "published_clean", "clean_delivery_path"),
                                  ("clean_qc", "published_clean_qc", "controller_validation_path")):
        if value.get(preferred):
            inputs[key] = value[preferred]
        elif packaging.get(field):
            inputs[key] = ref(Path(packaging[field]))
    if semantic_first(value):
        context_path = root / "manifests" / "editing_context.json"
        context = {"planning_policy": EDITING.POLICY, "decision": "pass", "basis": "validated_clean_input",
                   "plan": inputs["plan"], "clean_delivery": inputs["clean_delivery"],
                   "outputs": [EDITING.summary(output) for output in read(Path(inputs["plan"]["path"]))["outputs"]]}
        write(context_path, context)
        inputs["editing_context"] = ref(context_path)
        keep.add(context_path)
    if value.get("delivery_mode") != "clean" and all(key in inputs for key in ("plan", "source_index", "asset_copy", "clean_delivery", "clean_qc")):
        retained["reburn_inputs"] = inputs
    retained["outputs"] = [{"path": row["output_path"], "sha256": row["output_sha256"]} for row in packaging.get("results", []) if row.get("output_path")]
    write(root / "autonomous_state.json", retained)
    for path in root.rglob("*"):
        if path.is_file() and path.resolve() not in keep:
            path.unlink()
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "prepare", "asset-copy", "plan-evidence", "approve-plan", "render-clean",
                 "clean-qc", "subtitle-draft", "subtitle-review", "package", "final-evidence", "complete", "repair", "reburn", "status"):
        action = sub.add_parser(name)
        action.add_argument("--job-dir", type=Path, required=name != "init")
        if name == "init": action.add_argument("--work-order", type=Path, required=True)
        if name == "asset-copy": action.add_argument("--copy-text", type=Path, required=True)
        if name == "plan-evidence": action.add_argument("--plan", type=Path, required=True)
        if name in {"approve-plan", "subtitle-review", "complete"}: action.add_argument("--review", type=Path, required=True)
        if name == "package": action.add_argument("--config", type=Path, required=True)
        if name == "final-evidence": action.add_argument("--clean", action="store_true")
        if name == "reburn":
            action.add_argument("--plan-id", required=True)
            action.add_argument("--subtitle-txt", type=Path, required=True)
        if name == "repair":
            action.add_argument("--reason", required=True)
            action.add_argument("--continue-until-complete", action="store_true")
            action.add_argument("--authorization")
    args = parser.parse_args()
    actions = {"init": init, "prepare": prepare, "asset-copy": asset_copy,
               "plan-evidence": plan_evidence,
               "approve-plan": approve_plan, "render-clean": render_clean, "clean-qc": clean_qc,
               "subtitle-draft": subtitle_draft, "subtitle-review": subtitle_review, "package": package,
               "final-evidence": final_evidence, "complete": complete, "repair": repair, "reburn": reburn}
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
