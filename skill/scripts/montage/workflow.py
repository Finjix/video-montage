"""Fail-closed agent + machine evidence workflow for montage jobs.

The agent supplies editorial, speech and visual judgments; the local gates verify
their inputs, media hashes, ASR, PCM and rendered output. No hearing or forced
alignment claim is made by this workflow.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from montage import batch as BATCH
from montage import cast as CAST
from montage import planning as EDITING
from montage import rejected_intervals as REJECTED
from montage import speech as SPEECH
from montage import text_library as TEXT_LIBRARY
from montage.common import (FFMPEG, FFPROBE, MODEL_ROOT, OUTPUT_ROOT, ROOT, background, probe, quietly, read, ref,
                            require_ref, run, sha, write)
from montage.media import asr as ASR
from montage.media import audio_balance as BALANCE
from montage.media import audio_cleanup as AUDIO
from montage.media import frame_plan as FRAME_GATE
from montage.media import remaster as REMASTER
from montage.media import renderer as RENDERER
from montage.media import timing as SOURCE_TIMING
from montage.media.mix import (FINAL_SPEED, audio_metrics, cut_pcm_metrics, delivery_mix_evidence, delivery_speed,
                               final_frames, isolated_transients, pcm, voice_over_music_db)
from montage.packaging import packager as PACKAGER
from montage.speech import character_times, normalized

STATE_SCHEMA = "video-montage-autonomous-state/v260929"
ORDER_SCHEMA = "video-montage-autonomous-work-order/v260929"
PLAN_SCHEMA = "video-montage-autonomous-plan/v260929"
REVIEW_SCHEMA = "video-montage-agent-review/v260929"
TEXT_PIN_POLICY = "required-full-coverage/v1"
READING_POLICY = "semantic-reading/v1"
POLICIES = {"planning_policy": EDITING.POLICY, "batch_review_policy": BATCH.POLICY, "cast_policy": CAST.POLICY,
            "audio_cleanup_policy": AUDIO.POLICY, "speech_review_policy": SPEECH.POLICY,
            "text_pin_policy": TEXT_PIN_POLICY}


def video(path: Path) -> dict:
    info = probe(path)
    stream = next(row for row in info["streams"] if row["codec_type"] == "video")
    frames = SOURCE_TIMING.require_constant_frame_rate(FFPROBE, path, stream)
    fps = Fraction(stream["avg_frame_rate"])
    return {"fps_num": fps.numerator, "fps_den": fps.denominator, "frames": frames,
            "duration": float(frames / fps), "width": int(stream["width"]),
            "height": int(stream["height"])}


def text_library_fields(value: dict) -> dict:
    return {"text_library": value["text_library"]}


def source_speech(row: dict) -> dict:
    return SPEECH.source_asr(row)


def require_speech_policy(value: dict, index: dict, plan: dict | None = None) -> None:
    TEXT_LIBRARY.require_binding(value.get("text_library"), index, *([plan] if plan is not None else []))
    if index.get("speech_review_policy") != SPEECH.POLICY or (plan is not None and plan.get("speech_review_policy") != SPEECH.POLICY):
        raise ValueError("source speech review policy cannot be lost or changed")
    for row in index["sources"]:
        TEXT_LIBRARY.require_binding(value.get("text_library"), row)
        if not row.get("reviewed_speech"):
            raise ValueError("agent source speech review required before planning")
        if value.get("asset_copy") and read(require_ref(row["reviewed_speech"], "reviewed source speech")).get("asset_copy", {}).get("sha256") != value["asset_copy"]["sha256"]:
            raise ValueError("source speech review asset-copy binding changed")
        source_speech(row)


def attempt_dir(value: dict) -> Path:
    return Path(value["delivery_directory"])


def processing_dir(value: dict) -> Path:
    return Path(value["pending_delivery_directory"]) if value.get("pending_delivery_directory") else attempt_dir(value)


def start_pending_delivery(job: Path, value: dict) -> Path:
    pending = job / f"attempt-{value['repair_round'] + 1:02d}" / "pending" / attempt_dir(value).name
    pending.mkdir(parents=True, exist_ok=True)
    value["pending_delivery_directory"] = str(pending.resolve())
    return pending


def render_cut_frames(render_value: dict) -> list[int]:
    cumulative = 0
    cuts = []
    for segment in render_value["segments"][:-1]:
        cumulative += segment["expected_output_frames"]
        cuts.append(cumulative)
    return cuts


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
        if source.get("reviewed_speech"):
            texts[("source_speech", source["reviewed_speech"]["sha256"])] = normalized(source_speech(source)["text"])
    texts[("plan_speech", sha(Path(plan["path"]))) ] = normalized("".join(
        segment["text"] for output in read(Path(plan["path"]))["outputs"] for segment in output["segments"]))
    for item in evidence:
        if not isinstance(item, dict):
            return False
        text = normalized(item.get("text", ""))
        if not text or text not in texts.get((item.get("kind"), item.get("sha256")), ""):
            return False
    return True


def subtitle_head_word_supported(change: dict, asset_copy: dict, source_index: dict, plan: dict) -> bool:
    """A flagged initial character may belong to a reviewed, evidenced word."""
    word = change.get("head_word")
    reason = change.get("head_word_reason")
    if (not isinstance(word, str) or len(normalized(word)) < 2
            or not normalized(change.get("after", "")).startswith(normalized(word))
            or not isinstance(reason, str) or not reason.strip()
            or not any(normalized(item.get("text", "")) == normalized(word)
                       for item in change.get("evidence", []) if isinstance(item, dict))):
        return False
    return subtitle_change_supported(change, asset_copy, source_index, plan)


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


def transcribe(model, path: Path) -> dict:
    """One neutral recognition; The agent reviews the words, ASR is never retried to match a target."""
    return ASR.transcribe(model, str(path), "zh", initial_prompt="以下是简体中文口播。")[0]


def load_model():
    return ASR.build_model("large-v3-turbo", MODEL_ROOT, "auto", "int8", "float16")[0]


def state(job: Path, *, discarding_plan: bool = False) -> dict:
    value = read(job / "autonomous_state.json")
    if value.get("schema") != STATE_SCHEMA or value.get("review_mode") != "agent_asr_pcm":
        raise ValueError("not an autonomous montage job")
    order = read(require_ref(value["work_order"], "work order"))
    for key, policy in POLICIES.items():
        if value.get(key) != policy or order.get(key) != policy:
            raise ValueError(f"work-order {key} changed or missing in state")
    if value.get("celebrities") != CAST.registry(order):
        raise ValueError("work-order celebrity registry changed in state")
    TEXT_LIBRARY.require_binding(order.get("text_library"), value)
    if value.get("plan") and not discarding_plan:
        plan = read(require_ref(value["plan"], "plan policy"))
        if any(plan.get(key) != value[key] for key in ("planning_policy", "batch_review_policy",
                                                       "speech_review_policy", "audio_cleanup_policy")):
            raise ValueError("plan cannot lose or change its job policies")
        TEXT_LIBRARY.require_binding(value.get("text_library"), plan)
    return value


def save(job: Path, value: dict, phase: str) -> None:
    value["phase"] = phase
    value.setdefault("events", []).append({"at": datetime.now(timezone.utc).isoformat(), "phase": phase})
    write(job / "autonomous_state.json", value)


def fail_round(job: Path, value: dict, phase: str, reasons: list[str]) -> None:
    count = int(value.get("repair_round", 0)) + 1
    value["repair_round"] = count
    value["last_failures"] = reasons
    save(job, value, "repair_required")
    write(job / "reports" / f"repair_round_{count}.json",
          {"schema": "video-montage-autonomous-repair/v260929", "phase": phase,
           "round": count, "failures": reasons, "decision": "repair_required"})
    raise RuntimeError(f"{phase}: {'; '.join(reasons)}; repair round {count}; repair required until complete")


def repair(args) -> None:
    # A full repair invalidates the old plan and every downstream approval.
    # Missing/edited old plan bytes must not prevent that invalidation; normal
    # rendering and approval paths still require the exact pinned plan hash.
    job = args.job_dir.resolve(); value = state(job, discarding_plan=True)
    for key in ("pending_delivery_directory", "reburn_only", "source_speech_evidence", "plan", "plan_evidence",
                "plan_review", "editing_context", "clean_delivery", "clean_qc", "subtitle_draft", "subtitle_review",
                "packaging_delivery", "packaging_config", "audio_cleanup_authorization", "clean_speech_evidence",
                "completion", "final_evidence", "packaging_technical", "final_review", "reburn_inputs", "reburn_config"):
        value.pop(key, None)
    if value.pop("compact_delivery", False):
        for key in ("source_index", "asset_copy"):
            value.pop(key, None)
    (job / "video_montage_autonomous_completion.json").unlink(missing_ok=True)
    (job / "reports" / "packaging_technical.json").unlink(missing_ok=True)
    PACKAGER.invalidate_delivery_receipts(attempt_dir(value))
    try:
        fail_round(job, value, "requested_repair", [args.reason])
    except RuntimeError:
        pass
    print(json.dumps({"phase": value["phase"], "repair_round": value["repair_round"]}, ensure_ascii=False))


def init(args) -> None:
    order = json.load(sys.stdin) if str(args.work_order) == "-" else read(args.work_order)
    library = TEXT_LIBRARY.checked(read(ROOT / "assets/text-library.json"))
    sources = order.get("sources")
    if order.get("schema") != ORDER_SCHEMA or not isinstance(sources, list) or not sources or int(order.get("requested_outputs", 0)) < 1:
        raise ValueError("autonomous work order required")
    for key, policy in POLICIES.items():
        if order.get(key, policy) != policy:
            raise ValueError(f"jobs require {key} {policy}")
    order["cast_policy"] = CAST.POLICY
    celebrities = CAST.registry(order)
    BATCH.reuse_limits(order)
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
    parent = Path(order.get("output_root") or OUTPUT_ROOT).resolve()
    if parent.exists() and not parent.is_dir():
        raise ValueError(f"output_root must be a directory: {parent}")
    if args.job_dir is not None:
        job = args.job_dir.resolve()
        output = job.parent
        if (output.parent != parent or not PACKAGER.OUTPUT_NAME.fullmatch(output.name)
                or job != PACKAGER.runtime_directory(output)):
            raise ValueError("job-dir must be <output_root>/自动化混剪_xx/临时文件")
    else:
        output = parent / ("自动化混剪_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S"))
        job = PACKAGER.runtime_directory(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing non-empty delivery directory: {output}")
    if job.exists() and any(job.iterdir()):
        raise FileExistsError(f"refusing non-empty runtime directory: {job}")
    job.mkdir(parents=True)
    PACKAGER.ensure_delivery_layout(output)
    library_path = job / "config" / "text_library.json"
    write(library_path, library)
    order.update(POLICIES, celebrities=celebrities, text_library=ref(library_path))
    order_snapshot = job / "work_order.json"
    write(order_snapshot, order)
    save(job, {"schema": STATE_SCHEMA, "review_mode": "agent_asr_pcm", "work_order": ref(order_snapshot),
               "source_hashes": {str(Path(row["path"]).resolve()): sha(Path(row["path"])) for row in sources},
               "asset_root": str(asset), "output_root": str(output), "delivery_directory": str(output),
               "repair_round": 0, **POLICIES, "subtitle_segmentation_policy": READING_POLICY,
               "text_library": order["text_library"], "celebrities": celebrities}, "initialized")
    print(json.dumps({"job_dir": str(job), "delivery_directory": str(output)}, ensure_ascii=False))


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
        if (result.get("source", {}).get("sha256") != sha(path)
                or not isinstance(result.get("asr", {}).get("text"), str)
                or not isinstance(result.get("asr", {}).get("segments"), list)):
            raise ValueError("source ASR invalid")
        source_rows.append({"source": ref(path), "asr": ref(asr_path), "video": spec, **text_library_fields(value)})
    index_path = job / "evidence" / "source_index.json"
    write(index_path, {"schema": "video-montage-autonomous-source-index/v260929", "sources": source_rows,
                       **text_library_fields(value), "speech_review_policy": SPEECH.POLICY,
                       "asset_copy_sources": ref(asset_path),
                       "cast_policy": CAST.POLICY, "celebrities": CAST.registry(order),
                       "planning_policy": EDITING.POLICY, "batch_review_policy": BATCH.POLICY,
                       **({"batch_reuse_authorization": order["batch_reuse_authorization"]}
                          if "batch_reuse_authorization" in order else {})})
    value["source_index"] = ref(index_path)
    value.pop("source_speech_evidence", None)
    save(job, value, "prepared")


def asset_copy(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    index = read(require_ref(value["source_index"], "source index"))
    source_path = require_ref(index["asset_copy_sources"], "asset copy sources")
    originals = read(source_path)["assets"]
    proposed = read(args.copy_text)
    if (proposed.get("schema") != "video-montage-agent-asset-copy/v260929"
            or proposed.get("reviewer_role") != "agent" or proposed.get("sources_sha256") != sha(source_path)):
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


def source_speech_evidence(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"prepared", "copy_indexed", "repair_required", "source_speech_evidenced"} or value.get("plan"):
        raise ValueError("source speech evidence requires preparation before planning; repair first")
    require_ref(value["asset_copy"], "verified asset copy")
    index = read(require_ref(value["source_index"], "source index"))
    TEXT_LIBRARY.require_binding(value.get("text_library"), index, *index["sources"])
    rows = []
    for number, row in enumerate(index["sources"], 1):
        source = require_ref(row["source"], "original speech source")
        require_ref(row["asr"], "immutable source ASR")
        target = job / "evidence" / "source_speech" / f"source_{number:03d}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)])
        rows.append({"source": row["source"], "asr": row["asr"], "video": row["video"], "audio": ref(target)})
        row.pop("reviewed_speech", None)
    target = job / "evidence" / f"source_speech_round_{value['repair_round'] + 1}.json"
    write(target, {"schema": "video-montage-source-speech-evidence/v1", "policy": SPEECH.POLICY,
                   "asset_copy": value["asset_copy"], "sources": rows, **text_library_fields(value)})
    index_path = job / "evidence" / "source_index.json"
    write(index_path, index)
    value.update(source_index=ref(index_path), source_speech_evidence=ref(target))
    save(job, value, "source_speech_evidenced")
    print(json.dumps({"evidence": value["source_speech_evidence"], "review_schema": SPEECH.SCHEMA}, ensure_ascii=False))


def source_speech_review(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"source_speech_evidenced", "repair_required"} or not value.get("source_speech_evidence") or value.get("plan"):
        raise ValueError("source speech evidence required")
    if value.get("speech_review_policy") != SPEECH.POLICY:
        raise ValueError("source speech review policy required")
    evidence = read(require_ref(value["source_speech_evidence"], "source speech evidence"))
    index = read(require_ref(value["source_index"], "source index"))
    require_ref(value["asset_copy"], "speech asset copy")
    review = read(args.review)
    TEXT_LIBRARY.require_binding(value.get("text_library"), evidence, index, *index["sources"])
    SPEECH.header(review, "source", value["source_speech_evidence"], value["asset_copy"], value.get("text_library"))
    if evidence.get("asset_copy") != value["asset_copy"] or evidence.get("policy") != SPEECH.POLICY:
        raise ValueError("source speech evidence policy/copy changed")
    proposed = review.get("results", [])
    findings = {r.get("source_sha256"): r for r in proposed}
    expected = {r["source"]["sha256"] for r in index["sources"]}
    if len(findings) != len(proposed) or set(findings) != expected:
        raise ValueError("source speech review must cover every original exactly once")
    observations = {r["source"]["sha256"]: r for r in evidence["sources"]}
    if set(observations) != expected or len(observations) != len(evidence["sources"]):
        raise ValueError("complete original-source speech evidence required")
    texts = SPEECH.context(index, value["asset_copy"], value.get("text_library"))
    compiled = []
    for row in index["sources"]:
        identity = row["source"]["sha256"]; observation = observations[identity]
        require_ref(row["source"], "reviewed original source")
        if any(observation[k] != row[k] for k in ("source", "asr", "video")):
            raise ValueError("source speech evidence source/ASR/frame bounds changed")
        raw = read(require_ref(row["asr"], "immutable source ASR"))["asr"]
        duration = row["video"]["frames"] * row["video"]["fps_den"] / row["video"]["fps_num"]
        compiled.append(SPEECH.validate(findings[identity], raw, observation["audio"], duration, texts, allow_empty=True))
    review_path = job / "reviews" / f"source_speech_{sha(args.review)}.json"
    write(review_path, review)
    for number, (row, asr) in enumerate(zip(index["sources"], compiled), 1):
        target = job / "manifests" / "reviewed_speech" / f"source_{number:03d}_{sha(review_path)}.json"
        write(target, {"policy": SPEECH.POLICY, "source": row["source"], "raw_asr": row["asr"], "video": row["video"],
                       "evidence": value["source_speech_evidence"], "review": ref(review_path),
                       "asset_copy": value["asset_copy"], "asr": asr, **text_library_fields(value)})
        row["reviewed_speech"] = ref(target)
    index_path = job / "evidence" / "source_index.json"
    write(index_path, index)
    value["source_index"] = ref(index_path)
    save(job, value, "copy_indexed")


def check_plan(plan: dict, source_index: dict, count: int) -> list[str]:
    errors = []
    if plan.get("schema") != PLAN_SCHEMA or len(plan.get("outputs", [])) != count:
        return ["plan schema or complete output scope"]
    for key in ("planning_policy", "batch_review_policy", "speech_review_policy", "cast_policy"):
        if plan.get(key) != POLICIES[key] or source_index.get(key) != POLICIES[key]:
            errors.append(f"{key.upper()}_BINDING_REQUIRED")
    source_map = {row["source"]["sha256"]: row for row in source_index["sources"]}
    reviewed_sources = {}
    try:
        require_speech_policy(text_library_fields(source_index), source_index, plan)
        reviewed_sources = {key: source_speech(row) for key, row in source_map.items()}
    except (ValueError, KeyError, OSError, TypeError) as exc:
        errors.append(f"SOURCE_SPEECH_REVIEW_REQUIRED:{exc}")
    try:
        celebrities = CAST.registry(source_index)
        if plan.get("celebrities") != celebrities:
            errors.append("CELEBRITY_REGISTRY_BINDING_REQUIRED")
        registered = {row["person_id"] for row in celebrities}
        inventories = {source.get("source_sha256"): source.get("candidates", [])
                       for source in plan.get("batch_analysis", {}).get("sources", [])}
        for output in plan.get("outputs", []):
            if output.get("cast_policy") != CAST.POLICY or not CAST.valid_people(output.get("protagonist_ids"), list(registered)):
                errors.append("REGISTERED_SELECTED_CAST_REQUIRED")
            extras = CAST.supporting(output)
            if isinstance(extras, list) and any(person in registered for person in extras):
                errors.append("REGISTERED_CELEBRITY_CANNOT_BE_SUPPORTING_ACTOR")
            for segment in output.get("segments", []):
                matches = [candidate for candidate in inventories.get(segment.get("source_sha256"), [])
                           if (candidate.get("source_in_frame"), candidate.get("source_out_frame_exclusive"), normalized(candidate.get("text", "")))
                           == (segment.get("source_in_frame"), segment.get("source_out_frame_exclusive"), normalized(segment.get("text", "")))]
                if len(matches) != 1 or any(matches[0].get(key) != segment.get(key)
                                            for key in ("speaker_id", "visible_person_ids", "primary_person_ids")):
                    errors.append("SOURCE_CANDIDATE_CAST_AND_SPEAKER_BINDING_REQUIRED")
        games = [normalized(entry["text"]) for entry in TEXT_LIBRARY.load(plan["text_library"])["entries"]
                 if entry["category"] == "game"] if plan.get("text_library") else []
        for output in plan.get("outputs", []):
            if games and not any(any(name in normalized(str(segment.get("text", ""))) for name in games)
                                 for segment in output.get("segments", [])):
                errors.append(f"GAME_NAME_REQUIRED:{output.get('plan_id')}")
        for source in plan.get("batch_analysis", {}).get("sources", []):
            if any(not CAST.person_id(turn.get("speaker_id")) for turn in source.get("spoken_turns", [])):
                errors.append("COMPLETE_SPOKEN_TURN_SPEAKER_REQUIRED")
    except (ValueError, TypeError, OSError) as exc:
        errors.append(f"CELEBRITY_REGISTRY_INVALID:{exc}")
    ids = []
    rejected = REJECTED.entries()
    for output in plan["outputs"]:
        pid = output.get("plan_id")
        ids.append(pid)
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
            errors.append("unsafe plan ID")
        segments = output.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append(f"{pid}: empty segments")
            continue
        try:
            balance_config = BALANCE.for_output(plan, output)
        except ValueError as error:
            errors.append(f"{pid}:{error}")
            balance_config = {}
        frame_errors = FRAME_GATE.validate_plan_value({"segments": segments, "audio_balance": balance_config})
        errors.extend(f"{pid}:{error}" for error in frame_errors)
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
            if source["source"]["sha256"] in reviewed_sources:
                try:
                    if SPEECH.range_text(reviewed_sources[source["source"]["sha256"]], segment) != normalized(segment["text"]):
                        errors.append(f"{pid}: speech differs from reviewed native source range")
                except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                    errors.append(f"{pid}: reviewed source speech range invalid: {exc}")
            if not frame_errors:
                errors.extend(f"{pid}: rejected original source interval: {entry['entry_id']}"
                              for entry in rejected if REJECTED.hits(segment, entry))
    if len(ids) != len({pid.casefold() if isinstance(pid, str) else None for pid in ids}):
        errors.append("duplicate plan ID")
    if not errors:
        for output in plan["outputs"]:
            try:
                errors.extend(f"{output['plan_id']}:{error}" for error in EDITING.audit_output(output))
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
                errors.append(f"{output['plan_id']}: invalid editorial annotations: {error}")
    try:
        transcripts = {source["source"]["sha256"]: normalized(source_speech(source)["text"]) for source in source_index["sources"]}
        for source in plan.get("batch_analysis", {}).get("sources", []):
            for turn in source.get("spoken_turns", []):
                quote = normalized(turn.get("text", "")) if isinstance(turn, dict) else ""
                if not quote or quote not in transcripts.get(source.get("source_sha256"), ""):
                    errors.append("SPOKEN_TURN_NOT_IN_BOUND_SOURCE_ASR:" + str(source.get("source_sha256")))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        errors.append(f"SPOKEN_TURN_SOURCE_ASR_BINDING_INVALID:{exc}")
    try:
        report = BATCH.audit(plan, source_index, count)
        errors.extend(f"{row['code']}:{row['scope']}:{row['detail']}" for row in report["failures"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        errors.append(f"BATCH_REVIEW_INPUT_INVALID:{exc}")
    return errors


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


def audio_plan_errors(value: dict, plan: dict) -> list[str]:
    errors = []
    if plan.get("audio_cleanup_policy") != AUDIO.POLICY:
        errors.append("audio cleanup plan policy missing or changed")
    for output in plan.get("outputs", []):
        previous = None
        for segment in output["segments"]:
            if segment.get("audio_cleanup") or segment.get("audio_mute_ranges"):
                errors.append("audio cleanup must come from bound review, not an editable plan")
            if segment.get("audio_cleanup_policy") != AUDIO.POLICY:
                errors.append("audio cleanup segment policy missing or changed")
            if previous and previous["source_sha256"] == segment["source_sha256"] and segment["source_in_frame"] < previous["source_out_frame_exclusive"]:
                errors.append("audio cleanup segments must not repeat overlapping source frames")
            previous = segment
    return errors


def compile_audio_segments(value: dict, plan: dict, evidence: dict, review: dict) -> list[dict]:
    if (evidence.get("audio_cleanup_policy") != AUDIO.POLICY or review.get("audio_cleanup_policy") != AUDIO.POLICY):
        raise ValueError("audio cleanup policy missing in evidence/review")
    evidence_by_key = {(r["plan_id"], r["segment_index"]): r for r in evidence["results"]}
    findings = review.get("segments", [])
    review_by_key = {(r["plan_id"], r["segment_index"]): r for r in findings}
    expected = {(o["plan_id"], i) for o in plan["outputs"] for i in range(1, len(o["segments"]) + 1)}
    if len(findings) != len(expected) or set(review_by_key) != expected or set(evidence_by_key) != expected:
        raise ValueError("audio cleanup review scope incomplete")
    index = read(require_ref(value["source_index"], "audio cleanup source index"))
    source_asr = {r["source"]["sha256"]: source_speech(r) for r in index["sources"]}
    rows = []
    for output in plan["outputs"]:
        for i, segment in enumerate(output["segments"], 1):
            key = output["plan_id"], i
            observation = evidence_by_key[key].get("audio_cleanup", {})
            if observation.get("source_asr_digest") != AUDIO.digest(source_asr[segment["source_sha256"]]):
                raise ValueError("audio cleanup source transcript changed")
            cleanup = AUDIO.compile_review(observation, review_by_key[key].get("audio_cleanup", {}), segment)
            rows.append({"plan_id": key[0], "segment_index": i, "audio_cleanup": cleanup})
    return rows


def require_audio_approval(value: dict) -> None:
    plan = read(require_ref(value["plan"], "audio cleanup plan"))
    errors = audio_plan_errors(value, plan)
    if errors:
        raise ValueError("; ".join(errors))
    artifact = read(require_ref(value["audio_cleanup_authorization"], "audio cleanup authorization"))
    if artifact.get("policy") != AUDIO.POLICY:
        raise ValueError("audio cleanup authorization policy changed")
    if value.get("reburn_only") or value.get("compact_delivery"):
        if artifact.get("clean_delivery") != value.get("clean_delivery") or artifact.get("plan") != value["plan"]:
            raise ValueError("retained audio cleanup authorization changed")
        return
    evidence_path = require_ref(value["plan_evidence"], "audio cleanup evidence")
    review_path = require_ref(value["plan_review"], "audio cleanup review")
    evidence, review = read(evidence_path), read(review_path)
    if (evidence.get("plan") != value["plan"] or review.get("evidence_sha256") != sha(evidence_path)
            or review.get("plan_sha256") != value["plan"]["sha256"] or review.get("reviewer_role") != "agent"
            or artifact.get("plan") != value["plan"] or artifact.get("plan_review") != value["plan_review"]
            or artifact.get("plan_evidence") != value["plan_evidence"]):
        raise ValueError("audio cleanup approval binding changed")
    if artifact.get("segments") != compile_audio_segments(value, plan, evidence, review):
        raise ValueError("audio cleanup parameters or review changed")


def audio_render_check(value: dict, plan_id: str, rendered: dict) -> None:
    if rendered.get("audio_balance", {}).get("enabled"):
        if (rendered.get("checks", {}).get("audio_balance") is not True or not rendered.get("segments")
                or any(s.get("audio_balance", {}).get("output_verification", {}).get("decision") != "pass"
                       for s in rendered["segments"])):
            raise ValueError(f"{plan_id}: rendered audio balance verification failed")
    authorization = read(require_ref(value["audio_cleanup_authorization"], "audio cleanup authorization"))
    approved = [r["audio_cleanup"] for r in authorization["segments"] if r["plan_id"] == plan_id]
    if [s.get("audio_cleanup") for s in rendered["segments"]] != approved:
        raise ValueError("render did not use the approved audio cleanup intervals")


def require_candidate_speech(value: dict, evidence: dict, review: dict) -> None:
    if evidence.get("speech_review_policy") != SPEECH.POLICY or review.get("speech_review_policy") != SPEECH.POLICY:
        raise ValueError("candidate speech review policy binding required")
    TEXT_LIBRARY.require_binding(value.get("text_library"), evidence)
    SPEECH.library_header(review, value.get("text_library"))
    index = read(require_ref(value["source_index"], "reviewed speech sources"))
    plan = read(require_ref(value["plan"], "reviewed speech plan"))
    require_speech_policy(value, index, plan)
    texts = SPEECH.context(index, value["asset_copy"], value.get("text_library"))
    findings = {(r.get("plan_id"), r.get("segment_index")): r for r in review.get("segments", [])}
    segments = {(o["plan_id"], i): s for o in plan["outputs"] for i, s in enumerate(o["segments"], 1)}
    rows = {(r["plan_id"], r["segment_index"]): r for r in evidence["results"]}
    if (set(rows) != set(segments) or set(findings) != set(segments)
            or len(findings) != len(review.get("segments", [])) or len(rows) != len(evidence["results"])):
        raise ValueError("complete candidate speech reviews required")
    for key, segment in segments.items():
        row = rows[key]
        if row.get("expected_text") != segment["text"] or row.get("source_text_match") is not True:
            raise ValueError("candidate speech is not bound to reviewed source range")
        duration = (segment["source_out_frame_exclusive"] - segment["source_in_frame"]) * segment["source_fps_den"] / segment["source_fps_num"]
        SPEECH.validate(findings[key].get("speech_review"), row["asr"], row["pcm"], duration,
                        texts, expected=segment["text"])


def require_editing_approval(value: dict) -> None:
    """Recheck editorial approvals before rendering and publishing new edits."""
    require_audio_approval(value)
    plan_path = require_ref(value["plan"], "plan")
    plan = read(plan_path)
    index = read(require_ref(value["source_index"], "source index"))
    require_speech_policy(value, index, plan)
    require_cast_binding(value, plan)
    errors = check_plan(plan, index, int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]))
    if errors:
        raise ValueError("plan rejected: " + "; ".join(errors))
    if value.get("reburn_only"):
        context = read(require_ref(value["editing_context"], "retained clean editing authorization"))
        if (context.get("planning_policy") != value["planning_policy"] or context.get("decision") != "pass"
                or context.get("plan") != value["plan"] or context.get("clean_delivery") != value["clean_delivery"]
                or context.get("outputs") != [EDITING.summary(output) for output in plan["outputs"]]
                or context.get("batch_review_policy") != value["batch_review_policy"]
                or context.get("batch_report_digest") != BATCH.digest(batch_fields(value)["batch_review_report"])):
            raise ValueError("retained clean editing authorization changed")
        return
    evidence_path = require_ref(value["plan_evidence"], "plan evidence")
    evidence = read(evidence_path)
    review = read(require_ref(value["plan_review"], "plan review"))
    require_cast_binding(value, plan, evidence, review)
    if (evidence.get("planning_policy") != value["planning_policy"] or evidence.get("plan") != value["plan"]
            or evidence.get("source_index") != value["source_index"]
            or review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "plan"
            or review.get("reviewer_role") != "agent" or review.get("planning_policy") != value["planning_policy"]
            or review.get("evidence_sha256") != sha(evidence_path) or review.get("plan_sha256") != sha(plan_path)):
        raise ValueError("editorial approval binding invalid")
    errors = EDITING.plan_review_errors(plan, evidence, review)
    errors.extend(batch_review_errors(value, evidence, review))
    require_candidate_speech(value, evidence, review)
    segments = {(output["plan_id"], index): segment for output in plan["outputs"]
                for index, segment in enumerate(output["segments"], 1)}
    verified_sources = {}
    verified_visuals = set()
    for row in evidence["results"]:
        segment = segments[(row["plan_id"], row["segment_index"])]
        if row.get("decision") != "pending_speech_review":
            errors.append("candidate evidence failed")
        source_path = str(Path(segment["source_path"]).resolve())
        if source_path not in verified_sources:
            verified_sources[source_path] = ref(Path(source_path))
        if row.get("source") != verified_sources[source_path]:
            errors.append("candidate source does not describe the selected source")
        visual_key = (row["continuous_visual"]["path"], row["continuous_visual"]["sha256"],
                      row["source"]["path"], row["source"]["sha256"],
                      segment["source_in_frame"], segment["source_out_frame_exclusive"])
        if visual_key not in verified_visuals:
            verify_continuous_visual(row["continuous_visual"], row["source"],
                                     segment["source_in_frame"], segment["source_out_frame_exclusive"])
            verified_visuals.add(visual_key)
    if errors:
        raise ValueError("editorial approval rejected: " + "; ".join(errors))


def require_cast_binding(value: dict, plan: dict, *documents: dict) -> dict:
    celebrities = CAST.registry(read(require_ref(value["work_order"], "cast work order")))
    index = read(require_ref(value["source_index"], "cast source index"))
    if any(document.get("cast_policy") != CAST.POLICY or document.get("celebrities") != celebrities
           for document in (value, index, plan, *documents)):
        raise ValueError("selected celebrity registry or review binding changed")
    return {"cast_policy": CAST.POLICY, "celebrities": celebrities}


def batch_fields(value: dict, plan: dict | None = None, index: dict | None = None) -> dict:
    source_index = index or read(require_ref(value["source_index"], "batch sources"))
    order = read(require_ref(value["work_order"], "batch work order"))
    if source_index.get("batch_reuse_authorization") != order.get("batch_reuse_authorization"):
        raise ValueError("batch reuse authorization must match the bound user work order")
    plan = plan or read(require_ref(value["plan"], "batch plan"))
    cast_fields = require_cast_binding(value, plan)
    if plan.get("batch_review_policy") != BATCH.POLICY or source_index.get("batch_review_policy") != BATCH.POLICY:
        raise ValueError("batch review policy binding changed")
    report = BATCH.audit(plan, source_index, int(order["requested_outputs"]))
    if report["decision"] != "pass":
        raise ValueError("whole-batch plan rejected: " + "; ".join(row["code"] for row in report["failures"]))
    return {"batch_review_policy": BATCH.POLICY, "batch_review_report": report, **cast_fields}


def batch_review_errors(value: dict, evidence: dict, review: dict) -> list[str]:
    fields = batch_fields(value)
    if any(evidence.get(key) != item for key, item in fields.items()):
        return ["WHOLE_BATCH_EVIDENCE_CHANGED"]
    return BATCH.review_errors(fields["batch_review_report"], review)


def clean_batch_evidence(job: Path, value: dict, clean: dict) -> dict:
    def fingerprint(item: dict) -> dict:
        path = require_ref(item["output"], "batch clean output")
        count = read(require_ref(item["render_evidence"], "batch render evidence"))["actual_output_frames"]
        payload = run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path), "-an",
                       "-vf", "scale=240:-2:flags=area,format=yuv420p", "-fps_mode", "passthrough",
                       "-f", "framemd5", "-"])
        digest = BATCH.frame_digest(payload, count)
        proof = job / "manifests" / "batch_frames" / f"{item['plan_id']}.framemd5"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text(payload, encoding="utf-8")
        return {"plan_id": item["plan_id"], "output": item["output"], "frames": count,
                "decoded_visual_digest": digest, "proof": ref(proof)}

    with background(len(clean["results"])) as pool:
        rows = list(pool.map(fingerprint, clean["results"]))
    failures = BATCH.clean_errors(rows)
    return {"schema": BATCH.POLICY, "clean_delivery": value["clean_delivery"],
            "plan_sha256": value["plan"]["sha256"], "generator": ref(FFMPEG), "width": 240,
            "results": rows, "decision": "reject" if failures else "pass", "failures": failures}


def require_clean_batch(value: dict, clean: dict, qc: dict) -> None:
    report = qc.get("batch_clean", {})
    expected = {row["plan_id"]: row for row in clean["results"]}
    rows = report.get("results", [])
    if (qc.get("batch_review_policy") != BATCH.POLICY or report.get("schema") != BATCH.POLICY
            or report.get("clean_delivery") != value["clean_delivery"]
            or report.get("plan_sha256") != value["plan"]["sha256"] or report.get("width") != 240
            or report.get("generator") != ref(FFMPEG) or report.get("decision") != "pass"
            or len(rows) != len(expected) or {row.get("plan_id") for row in rows} != set(expected)):
        raise ValueError("hash-bound whole-batch clean QC required")
    for row in rows:
        current = expected[row["plan_id"]]
        if (row.get("output") != current["output"]
                or row.get("frames") != read(require_ref(current["render_evidence"], "batch timeline"))["actual_output_frames"]
                or BATCH.frame_digest(require_ref(row["proof"], "decoded clean-frame fingerprints").read_text(encoding="utf-8"), row["frames"])
                   != row.get("decoded_visual_digest")):
            raise ValueError("whole-batch clean fingerprint binding changed")
    if BATCH.clean_errors(rows) or report.get("failures"):
        raise ValueError("duplicate rendered clean videos cannot be delivered")


def final_editing_evidence(job: Path, value: dict, output: dict, path: Path, count: int) -> dict:
    errors = EDITING.frame_errors(count, final=True)
    if errors:
        raise ValueError("; ".join(errors))
    summary = EDITING.summary(output)
    summary["final_frames"] = count
    for shot in summary["visual_shots"]:
        shot["final_in_frame"] = (shot["output_in_frame"] * 5 + 5) // 6
        shot["final_out_frame_exclusive"] = (shot["output_out_frame_exclusive"] * 5 + 5) // 6
    return {"editing": summary, "continuous_visual": final_continuous_visual(job, value, path, count)}


def final_continuous_visual(job: Path, value: dict, path: Path, count: int) -> dict:
    # Repairing a measurement must not decode identical final frames again.
    source = ref(path)
    for cached in sorted(job.glob(f"attempt-*/evidence/final_continuous/{source['sha256']}/000000000_{count:09d}/frames.json")):
        reference = ref(cached)
        verify_continuous_visual(reference, source, 0, count)
        return reference
    return continuous_visual(path, 0, count, job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "final_continuous")


def warm_final_visuals(pool, job: Path, value: dict, outputs: list[tuple[str, Path]]) -> dict:
    """Decode each final output's full frame evidence while ASR runs; keyed by output hash."""
    def warm(path: Path) -> None:
        count = PACKAGER.video_spec(path)["frames"]
        if not EDITING.frame_errors(count, final=True):
            final_continuous_visual(job, value, path, count)
    warming = {}
    for digest, path in outputs:
        if digest not in warming and path.is_file():
            warming[digest] = pool.submit(quietly, warm, path)
    return warming


def visual_boundary_metrics(visual: list[dict], start: int, end: int) -> dict:
    """Compare the first/last 13 frames and scan a half-second around each edge.

    A transition may leave the first few frames looking like the previous shot
    or arrive just before the last 13 frames. Preserve both sets of per-frame
    scores so the agent can inspect position, scale, and shot continuity.
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
    require_speech_policy(value, source_index, plan)
    for key in ("plan_review", "final_review", "completion", "clean_delivery", "clean_qc", "subtitle_review",
                "packaging_delivery", "final_evidence", "editing_context", "audio_cleanup_authorization"):
        value.pop(key, None)
    errors = check_plan(plan, source_index, int(order["requested_outputs"]))
    try:
        write(job / "reports" / f"batch_review_round_{value['repair_round'] + 1}.json",
              BATCH.audit(plan, source_index, int(order["requested_outputs"])))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass  # Invalid annotations are already reported by check_plan.
    errors.extend(audio_plan_errors(value, plan))
    if errors:
        fail_round(job, value, "plan", errors)
    model = load_model()
    speech = {row["source"]["sha256"]: source_speech(row) for row in source_index["sources"]}
    source_text = {key: normalized(asr["text"]) for key, asr in speech.items()}
    result = []
    candidate_cache = {}
    visual_root = job / "evidence" / "source_continuous"
    ranges = sorted({(str(Path(segment["source_path"]).resolve()), segment["source_in_frame"],
                      segment["source_out_frame_exclusive"])
                     for output in plan["outputs"] for segment in output["segments"]})
    with background(len(ranges)) as pool:
        warming = {key: pool.submit(quietly, continuous_visual, Path(key[0]), key[1], key[2], visual_root)
                   for key in ranges}
        for output in plan["outputs"]:
            pid = output["plan_id"]
            for index, segment in enumerate(output["segments"], 1):
                cache_key = (segment["source_sha256"], segment["source_in_frame"],
                             segment["source_out_frame_exclusive"], segment["source_fps_num"],
                             segment["source_fps_den"], float(segment.get("audio_gain_db", 0.0)), segment["text"],
                             AUDIO.digest(speech[segment["source_sha256"]]))
                if cache_key in candidate_cache:
                    result.append({**candidate_cache[cache_key], "plan_id": pid, "segment_index": index,
                                   "candidate_id": segment.get("candidate_id")})
                    continue
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
                asr = transcribe(model, target)
                cleanup_evidence = AUDIO.analyze(FFMPEG, source, segment, speech[segment["source_sha256"]],
                                                 target.parent / "audio_cleanup")
                source_text_match = normalized(segment["text"]) in source_text[segment["source_sha256"]]
                numbers = []
                source_frames = video(source)["frames"]
                for boundary in (segment["source_in_frame"], segment["source_out_frame_exclusive"]):
                    half = max(30, round(float(fps) * .5))  # visual_boundary_metrics scans up to 30 native frames
                    numbers.extend(range(max(0, boundary - half), min(source_frames, boundary + half)))
                visual = frames(source, numbers, target.parent / "frames")
                warming[(str(source.resolve()), segment["source_in_frame"], segment["source_out_frame_exclusive"])].result()
                continuous = continuous_visual(source, segment["source_in_frame"], segment["source_out_frame_exclusive"],
                                               visual_root)
                visual_boundary = visual_boundary_metrics(
                    visual, segment["source_in_frame"], segment["source_out_frame_exclusive"])
                row = {"plan_id": pid, "segment_index": index, "candidate_id": segment.get("candidate_id"),
                       "source": ref(source), "pcm": ref(target), "frames": visual, "metrics": metrics,
                       "continuous_visual": continuous, "asr": asr, "expected_text": segment["text"],
                       "source_text_match": source_text_match, "isolated_transients": isolated_transients(samples),
                       "clean_gap": metrics["head_40ms_dbfs"] <= -36 and metrics["tail_40ms_dbfs"] <= -36,
                       "visual_boundary": visual_boundary, "audio_cleanup": cleanup_evidence,
                       "decision": "pending_speech_review" if source_text_match and visual_boundary["decision"] == "pass" else "reject"}
                result.append(row)
                candidate_cache[cache_key] = row
    evidence_path = job / "evidence" / f"plan_evidence_round_{value['repair_round'] + 1}.json"
    write(evidence_path, {"schema": "video-montage-plan-evidence/v260929", "plan": ref(args.plan),
                          **text_library_fields(value), "audio_cleanup_policy": AUDIO.POLICY,
                          "source_index": value["source_index"], "planning_policy": EDITING.POLICY,
                          "speech_review_policy": SPEECH.POLICY,
                          "outputs": [EDITING.summary(output) for output in plan["outputs"]],
                          **batch_fields(value, plan, source_index),
                          "results": result})
    value["plan"] = ref(args.plan); value["plan_evidence"] = ref(evidence_path)
    failures = [f"{row['plan_id']}:{row['segment_index']}:source speech/visual boundary inconclusive" for row in result
                if row["decision"] != "pending_speech_review"]
    if failures:
        fail_round(job, value, "candidate_evidence", failures)
    save(job, value, "plan_evidenced")


def approve_plan(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"plan_evidenced", "repair_required"} or not value.get("plan_evidence"):
        raise ValueError("plan evidence required")
    evidence_path = require_ref(value["plan_evidence"], "plan evidence")
    evidence = read(evidence_path); review = read(args.review)
    if (review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "plan"
            or review.get("reviewer_role") != "agent" or review.get("evidence_sha256") != sha(evidence_path)
            or review.get("plan_sha256") != value["plan"]["sha256"]
            or evidence.get("planning_policy") != EDITING.POLICY or review.get("planning_policy") != EDITING.POLICY):
        raise ValueError("agent plan review binding invalid")
    plan = read(require_ref(value["plan"], "plan"))
    require_cast_binding(value, plan, evidence, review)
    errors = check_plan(plan, read(require_ref(value["source_index"], "source index")),
                        int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]))
    if errors:
        fail_round(job, value, "agent_whole_edit_review", errors)
    errors = EDITING.plan_review_errors(plan, evidence, review) + batch_review_errors(value, evidence, review)
    if errors:
        fail_round(job, value, "agent_whole_edit_review", errors)
    findings = {(row.get("plan_id"), row.get("segment_index")): row for row in review.get("segments", [])}
    expected = {(row["plan_id"], row["segment_index"]) for row in evidence["results"]}
    if (len(findings) != len(review.get("segments", [])) or set(findings) != expected
            or any(row.get(key) is not True for row in findings.values()
                   for key in ("semantic_pass", "visual_pass", "entry_visual_pass", "exit_visual_pass"))
            or any(not row.get("reason") or not row.get("boundary_reason") for row in findings.values())):
        fail_round(job, value, "agent_plan_review", ["incomplete or failed visual/semantic or first/last frame findings"])
    require_candidate_speech(value, evidence, review)
    value["plan_review"] = ref(args.review)
    try:
        audio_segments = compile_audio_segments(value, plan, evidence, review)
    except ValueError as error:
        fail_round(job, value, "audio_cleanup_review", [str(error)])
    target = job / "reviews" / f"audio_cleanup_round_{value['repair_round'] + 1}.json"
    write(target, {"policy": AUDIO.POLICY, "plan": value["plan"], "plan_evidence": value["plan_evidence"],
                   "plan_review": value["plan_review"], "segments": audio_segments})
    value["audio_cleanup_authorization"] = ref(target)
    require_editing_approval(value)
    save(job, value, "plan_approved")


def render_clean(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "plan_approved":
        raise ValueError("agent plan approval required")
    require_editing_approval(value)
    plan = read(require_ref(value["plan"], "plan"))
    audio_by_key = {(r["plan_id"], r["segment_index"]): r["audio_cleanup"] for r in
                    read(require_ref(value["audio_cleanup_authorization"], "audio cleanup authorization"))["segments"]}
    start_pending_delivery(job, value)
    attempt = job / f"attempt-{value['repair_round'] + 1:02d}"
    PACKAGER.ensure_delivery_layout(processing_dir(value))

    def render_output(output: dict) -> tuple[Path, Path, dict]:
        pid = output["plan_id"]
        unit = attempt / "plans" / f"{pid}.json"
        segments = [{**s, "audio_cleanup": audio_by_key[(pid, i)]} for i, s in enumerate(output["segments"], 1)]
        write(unit, {"segments": segments, "transitions": output.get("transitions", []),
                     "audio_balance": BALANCE.for_output(plan, output)})
        target = PACKAGER.delivery_category(processing_dir(value), "clean") / f"{pid}.mp4"
        evidence_path = attempt / "evidence" / "render" / f"{pid}.json"
        return target, evidence_path, RENDERER.render(unit, target, evidence_path, overwrite=True)

    with background(len(plan["outputs"])) as pool:
        renders = list(pool.map(render_output, plan["outputs"]))
    rows = []
    for output, (target, evidence_path, rendered) in zip(plan["outputs"], renders):
        pid = output["plan_id"]
        audio_render_check(value, pid, rendered)
        errors = EDITING.render_errors(output, rendered, rendered["actual_output_frames"])
        if errors:
            fail_round(job, value, "clean_render_constraints", [f"{pid}:{error}" for error in errors])
        rows.append({"plan_id": pid, "output": ref(target), "render_evidence": ref(evidence_path),
                     "expected_text": "".join(segment["text"] for segment in output["segments"])})
    clean = processing_dir(value) / "临时文件" / "manifests" / "clean_delivery.json"
    write(clean, {"schema": "video-montage-autonomous-clean/v260929", "decision": "pending_qc",
                  "plan": value["plan"], "plan_review": value["plan_review"], "results": rows,
                  "planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"]})
    value["clean_delivery"] = ref(clean)
    save(job, value, "clean_rendered")


def rendered_shot_asr(model, output: Path, rendered: dict, planned: dict, directory: Path) -> dict:
    """Recognize each rendered shot separately and anchor its word times to that shot.

    Whole-video ASR may assign a new shot's first word to the previous shot's
    silence; per-shot observations feed the agent's clean speech review.
    """
    planned_segments = rendered_plan_segments(planned, rendered)
    directory.mkdir(parents=True, exist_ok=True)
    cursor = 0
    observations = []
    for index, (shot, original) in enumerate(zip(rendered["segments"], planned_segments)):
        end = cursor + shot["expected_output_frames"]
        target = directory / f"shot_{index + 1:03d}.wav"
        run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(output),
             "-af", f"atrim=start={cursor / 60:.9f}:end={end / 60:.9f},asetpts=PTS-STARTPTS",
             "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)])
        observations.append({"shot_index": index + 1, "audio": ref(target), "asr": transcribe(model, target),
                             "output_in_frame": cursor, "output_out_frame_exclusive": end,
                             "expected_text": original["text"]})
        cursor = end
    return {"shot_observations": observations, "timing_basis": "verified_rendered_shot_asr", "output": ref(output)}


def reviewed_shot_timing(observations: list[dict], findings: list[dict], texts: dict) -> dict:
    by_id = {r.get("shot_index"): r for r in findings}
    if len(by_id) != len(findings) or set(by_id) != {r["shot_index"] for r in observations}:
        raise ValueError("speech review must cover every rendered shot exactly once")
    segments = []; reviewed_observations = []; cursor = 0
    for row in observations:
        if row["output_in_frame"] != cursor or row["output_out_frame_exclusive"] <= cursor:
            raise ValueError("speech observations do not cover consecutive output frames")
        duration = (row["output_out_frame_exclusive"] - cursor) / 60
        asr = SPEECH.validate(by_id[row["shot_index"]], row["asr"], row["audio"], duration,
                              texts, expected=row["expected_text"])
        reviewed_observations.append({**row, "reviewed_asr": asr})
        for segment in asr["segments"]:
            shifted = {**segment, "start": segment["start"] + cursor / 60, "end": segment["end"] + cursor / 60,
                       "words": [{**w, "start": w["start"] + cursor / 60, "end": w["end"] + cursor / 60}
                                 for w in segment["words"]]}
            segments.append(shifted)
        cursor = row["output_out_frame_exclusive"]
    return {"text": " ".join(r["reviewed_asr"]["text"] for r in reviewed_observations),
            "segments": segments, "shot_observations": reviewed_observations}


def verify_reviewed_shots(timing: dict, output_ref: dict, planned: dict, rendered: dict) -> bool:
    context = timing.get("review_context", {})
    evidence = read(require_ref(context.get("evidence"), "clean speech review evidence"))
    review = read(require_ref(context.get("review"), "clean speech review"))
    library_ref = evidence.get("text_library")
    TEXT_LIBRARY.require_binding(library_ref, timing)
    SPEECH.header(review, "clean", context["evidence"], evidence["asset_copy"], library_ref)
    if evidence.get("policy") != SPEECH.POLICY:
        raise ValueError("clean speech review evidence policy changed")
    index = read(require_ref(evidence["source_index"], "clean speech sources"))
    plan = read(require_ref(evidence["plan"], "clean speech plan"))
    require_speech_policy({"speech_review_policy": SPEECH.POLICY, **text_library_fields(evidence)}, index, plan)
    if next((o for o in plan["outputs"] if o["plan_id"] == planned["plan_id"]), None) != planned:
        raise ValueError("clean speech review describes another plan")
    rows = {r["plan_id"]: r for r in evidence["results"]}
    findings = {r["plan_id"]: r for r in review.get("results", [])}
    if len(findings) != len(review.get("results", [])) or set(findings) != set(rows):
        raise ValueError("complete clean speech review scope required")
    row = rows[planned["plan_id"]]
    if row["output_sha256"] != output_ref["sha256"] or row["actual_output_frames"] != rendered["actual_output_frames"]:
        raise ValueError("clean speech review output hash/frame range changed")
    result = reviewed_shot_timing(row["observations"], findings[planned["plan_id"]].get("shots", []),
                                 SPEECH.context(index, evidence["asset_copy"], library_ref))
    originals = rendered_plan_segments(planned, rendered)
    observations = result["shot_observations"]
    if len(observations) != len(originals):
        return False
    cursor = 0
    for number, (shot, original, observed) in enumerate(zip(rendered["segments"], originals, observations), 1):
        end = cursor + shot["expected_output_frames"]
        if (observed["shot_index"] != number or observed["expected_text"] != original["text"]
                or observed["output_in_frame"] != cursor or observed["output_out_frame_exclusive"] != end):
            return False
        cursor = end
    return cursor == rendered["actual_output_frames"] and all(timing.get(k) == v for k, v in result.items())


def verified_shot_speech(timing: dict, output_ref: dict, planned: dict, rendered: dict) -> bool:
    return (timing.get("output") == output_ref and timing.get("timing_basis") == "verified_rendered_shot_asr"
            and timing.get("review_basis") == SPEECH.POLICY and verify_reviewed_shots(timing, output_ref, planned, rendered))


def clean_qc(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_rendered", "repair_required"}:
        raise ValueError("clean rendering required")
    require_editing_approval(value)
    clean_path = require_ref(value["clean_delivery"], "clean delivery"); clean = read(clean_path)
    model = load_model(); rows = []; failures = []
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
    for row in clean["results"]:
        output = require_ref(row["output"], "clean output")
        rendered = read(require_ref(row["render_evidence"], "render evidence"))
        audio_render_check(value, row["plan_id"], rendered)
        cleanup_proof = AUDIO.verify_output(AUDIO.decode(FFMPEG, output), rendered)
        if cleanup_proof["decision"] != "pass":
            failures.append(f"{row['plan_id']}: audio cleanup residual/boundary failure")
        spec = video(output)
        errors = EDITING.render_errors(plan_by_id[row["plan_id"]], rendered, spec["frames"])
        if (spec["fps_num"], spec["fps_den"]) != (60, 1):
            errors.append("CLEAN_REQUIRES_60_FPS")
        if errors:
            failures.extend(f"{row['plan_id']}:{error}" for error in errors)
            continue
        samples = pcm(output)
        metrics = audio_metrics(samples)
        cut_metrics = cut_pcm_metrics(samples, render_cut_frames(rendered), clean=True)
        timing_asr = rendered_shot_asr(model, output, rendered, plan_by_id[row["plan_id"]],
                                       job / "evidence" / "clean_shot_asr" / row["plan_id"])
        if metrics["clipped_samples"] or any(cut["decision"] != "pass" for cut in cut_metrics):
            failures.append(f"{row['plan_id']}: clean clipping/cut PCM failure")
        rows.append({"plan_id": row["plan_id"], "output": row["output"], "audio_cleanup": cleanup_proof,
                     **({"audio_balance": {**rendered["audio_balance"], "segments": [
                         s.get("audio_balance") for s in rendered["segments"]]}}
                        if "audio_balance" in rendered else {}),
                     "timing_asr": timing_asr, "metrics": metrics, "cut_pcm": cut_metrics,
                     "expected_text": row["expected_text"], "text_match": False,
                     "text_match_basis": "pending_agent_shot_review"})
    report_path = job / "reports" / "clean_qc.json"
    batch_clean = {}
    if not failures:
        batch_clean = clean_batch_evidence(job, value, clean)
        failures.extend(batch_clean["failures"])
    write(report_path, {"schema": "video-montage-autonomous-clean-qc/v260929",
                        "clean_delivery": ref(clean_path), "results": rows,
                        "planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"],
                        "batch_review_policy": BATCH.POLICY, "batch_clean": batch_clean,
                        "decision": "pending_speech_review" if not failures else "reject",
                        "failures": failures})
    value["clean_qc"] = ref(report_path)
    if failures:
        fail_round(job, value, "clean_qc", failures)
    evidence_path = job / "evidence" / f"clean_speech_round_{value['repair_round'] + 1}.json"
    write(evidence_path, {"policy": SPEECH.POLICY, "plan": value["plan"], "source_index": value["source_index"],
                          **text_library_fields(value),
                          "asset_copy": value["asset_copy"], "results": [{"plan_id": r["plan_id"],
                              "output_sha256": r["output"]["sha256"],
                              "actual_output_frames": read(require_ref(c["render_evidence"], "clean render"))["actual_output_frames"],
                              "observations": r["timing_asr"]["shot_observations"]}
                              for r, c in zip(rows, clean["results"])]})
    value["clean_speech_evidence"] = ref(evidence_path)
    save(job, value, "clean_speech_pending")
    print(json.dumps({"evidence": value["clean_speech_evidence"], "review_schema": SPEECH.SCHEMA}, ensure_ascii=False))


def clean_speech_review(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_speech_pending", "repair_required"} or not value.get("clean_speech_evidence"):
        raise ValueError("clean speech evidence and explicit agent review required")
    require_editing_approval(value)
    evidence = read(require_ref(value["clean_speech_evidence"], "clean speech evidence"))
    if any(evidence.get(k) != value[k] for k in ("plan", "source_index", "asset_copy")):
        raise ValueError("clean speech input bindings changed")
    review = read(args.review)
    TEXT_LIBRARY.require_binding(value.get("text_library"), evidence)
    SPEECH.header(review, "clean", value["clean_speech_evidence"], value["asset_copy"], value.get("text_library"))
    review_path = job / "reviews" / f"clean_speech_{sha(args.review)}.json"
    write(review_path, review)
    qc = read(require_ref(value["clean_qc"], "pending clean QC"))
    if qc.get("decision") != "pending_speech_review" or qc.get("failures"):
        raise ValueError("clean speech review cannot override failed technical QC")
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    by_id = {r["plan_id"]: r for r in clean["results"]}
    plans = {o["plan_id"]: o for o in read(require_ref(value["plan"], "plan"))["outputs"]}
    findings = {r["plan_id"]: r for r in review.get("results", [])}
    inputs = {r["plan_id"]: r for r in evidence["results"]}
    if (set(findings) != set(by_id) or set(inputs) != set(by_id) or len(findings) != len(review.get("results", []))
            or len(inputs) != len(evidence["results"]) or len(qc["results"]) != len(by_id)):
        raise ValueError("clean speech review complete batch scope required")
    texts = SPEECH.context(read(require_ref(value["source_index"], "speech sources")), value["asset_copy"], value.get("text_library"))
    for row in qc["results"]:
        pid = row["plan_id"]; timing = row["timing_asr"]
        if (row["output"] != by_id[pid]["output"] or timing["shot_observations"] != inputs[pid]["observations"]):
            raise ValueError("clean speech observations changed")
        require_ref(row["output"], "reviewed clean output")
        timing.update(reviewed_shot_timing(inputs[pid]["observations"], findings[pid].get("shots", []), texts))
        timing.update(review_basis=SPEECH.POLICY, review_context={"evidence": value["clean_speech_evidence"], "review": ref(review_path)},
                      **text_library_fields(value))
        if not verified_shot_speech(timing, row["output"], plans[pid],
                                    read(require_ref(by_id[pid]["render_evidence"], "reviewed clean render"))):
            raise ValueError("incomplete reviewed shot speech proof")
        row.update(text_match=True, text_match_basis="agent_reviewed_rendered_shots")
    qc.update(decision="pass", speech_review_policy=SPEECH.POLICY, speech_review=ref(review_path),
              speech_review_evidence=value["clean_speech_evidence"])
    target = job / "reports" / f"clean_qc_reviewed_round_{value['repair_round'] + 1}.json"
    write(target, qc)
    value["clean_qc"] = ref(target)
    save(job, value, "clean_validated")


def subtitle_draft(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"clean_validated", "subtitle_drafted", "repair_required"} or not value.get("asset_copy") or not value.get("clean_qc"):
        raise ValueError("clean QC and agent asset copy index required")
    if read(require_ref(value["clean_qc"], "clean QC")).get("decision") != "pass":
        raise ValueError("passing clean QC required")
    require_clean_qc(value)
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    output = processing_dir(value)
    rows = []
    timing_by_id = {row["plan_id"]: row.get("timing_asr")
                    for row in read(require_ref(value["clean_qc"], "clean QC"))["results"]}
    subtitle_dir = getattr(args, "subtitle_dir", None)
    imported = {}
    if subtitle_dir is not None:
        # Validate the complete batch before writing any new snapshot. A revised
        # draft may change cue count, but cannot change the verified speech.
        for row in clean["results"]:
            source = subtitle_dir.resolve() / PACKAGER.subtitle_filename(row["plan_id"])
            duration = round(PACKAGER.video_spec(require_ref(row["output"], "clean output"))["duration"] * 1000)
            cues = PACKAGER.parse_srt(source, duration)
            if normalized("".join(cue["text"] for cue in cues)) != normalized(row["expected_text"]):
                raise ValueError(f"{row['plan_id']}: imported subtitles differ from verified speech")
            TEXT_LIBRARY.require_complete_terms(cues, TEXT_LIBRARY.protected_terms(value.get("text_library")),
                                                normalized, expected=row["expected_text"])
            imported[row["plan_id"]] = source
    for row in clean["results"]:
        if imported:
            draft = {"plan_id": row["plan_id"], "input_path": row["output"]["path"],
                     "subtitle_txt_path": str(imported[row["plan_id"]]), "asr_device": "reviewed_resegmentation"}
        else:
            draft = PACKAGER.draft_one(require_ref(row["output"], "clean output"), row["plan_id"], output,
                                      overwrite=True, verified_asr=timing_by_id.get(row["plan_id"]))
        source = Path(draft["subtitle_txt_path"])
        snapshot = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "subtitle_drafts" / source.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source.read_bytes())
        draft["subtitle_txt_path"] = str(snapshot.resolve())
        draft["subtitle_txt_sha256"] = sha(snapshot)
        rows.append(draft)
    report = PACKAGER.delivery_category(output, "reports") / "subtitle_draft.json"
    write(report, {"schema": "video-montage-autonomous-subtitle-draft/v260929",
                   "clean_delivery": value["clean_delivery"], "asset_copy": value["asset_copy"], "results": rows,
                   **text_library_fields(value)})
    value["subtitle_draft"] = ref(report)
    save(job, value, "subtitle_drafted")


def subtitle_review(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] != "subtitle_drafted":
        raise ValueError("subtitle draft required")
    draft_path = require_ref(value["subtitle_draft"], "subtitle draft")
    draft = read(draft_path); review = read(args.review)
    TEXT_LIBRARY.require_binding(value.get("text_library"), draft)
    if (review.get("schema") != "video-montage-agent-subtitle-review/v260929"
            or review.get("draft_sha256") != sha(draft_path)
            or review.get("asset_copy_sha256") != value["asset_copy"]["sha256"]):
        raise ValueError("subtitle review binding invalid")
    asset_copy_value = read(require_ref(value["asset_copy"], "agent asset copy"))
    source_index = read(require_ref(value["source_index"], "source index"))
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    clean_qc_value = read(require_ref(value["clean_qc"], "clean QC"))
    asr_by_id = {row["plan_id"]: row["timing_asr"] for row in clean_qc_value["results"]}
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
        duration = round(PACKAGER.video_spec(Path(row["input_path"]))["duration"] * 1000)
        before = PACKAGER.parse_srt(draft_sub, duration)
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
            PACKAGER.subtitle_segmentation.require_line_limit(after)
            length = len(normalized(after))
            if (not isinstance(start, int) or not isinstance(end, int) or start < previous_end
                    or end <= start or cursor + length > len(timings)):
                raise ValueError(f"{pid}: invalid corrected subtitle timing")
            validate_subtitle_timing(start, end, timings[cursor],
                                     timings[cursor + length - 1], shot_bounds[index - 1])
            blocks.append(f"{index}\n{PACKAGER.format_timestamp(start)} --> {PACKAGER.format_timestamp(end)}\n{after}")
            previous_end = end
            cursor += length
        if cursor != len(timings):
            raise ValueError(f"{pid}: corrected cue words do not cover output ASR")
        if normalized("".join(cue["after"] for cue in cues)) != expected[pid]:
            raise ValueError(f"{pid}: subtitle words differ from exact source speech")
        TEXT_LIBRARY.require_complete_terms([{"text": cue["after"]} for cue in cues],
                                            TEXT_LIBRARY.protected_terms(value.get("text_library")),
                                            normalized, expected=expected[pid])
        reading = PACKAGER.subtitle_segmentation.reading_issues(
            [{"start_ms": cue["start_ms"], "end_ms": cue["end_ms"], "text": cue["after"]} for cue in cues])
        for issue in reading:
            correction = cues[issue["index"] - 1]
            if issue["code"] == "dependent_head":
                if not subtitle_head_word_supported(correction, asset_copy_value, source_index, value["plan"]):
                    raise ValueError(f"{pid}: dependent subtitle head; revise segmentation or evidence the complete head_word")
                continue
            reason = correction.get("short_cue_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{pid}: short final subtitle requires a concrete short_cue_reason")
        corrected = PACKAGER.subtitle_output(processing_dir(value), pid)
        corrected.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        PACKAGER.parse_srt(corrected, duration)
        revised.append({"plan_id": pid, "subtitle": ref(corrected), "draft": ref(draft_sub),
                        "reading_issues": reading})
    report = job / "reports" / "subtitle_review.json"
    write(report, {"schema": "video-montage-autonomous-subtitle-review/v260929",
                   "draft": ref(draft_path), "agent_review": ref(args.review), "results": revised,
                   **text_library_fields(value),
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
            cues = PACKAGER.parse_srt(Path(subtitle["path"]), round(PACKAGER.video_spec(Path(row["input_path"]))["duration"] * 1000))
            row["subtitle_design"] = PACKAGER.packaging_design.refresh(row["subtitle_design"], cues, subtitle["sha256"])
        write(config_path, config)
        value["reburn_config"] = ref(config_path)
    save(job, value, "subtitle_approved")


def package(args) -> None:
    job = args.job_dir.resolve(); value = state(job)
    if value["phase"] not in {"subtitle_approved", "repair_required"}:
        raise ValueError("context-reviewed subtitles required")
    require_editing_approval(value)
    require_clean_qc(value)
    review = read(require_ref(value["subtitle_review"], "subtitle review"))
    config = read(args.config)
    if config.get("text_pin_policy") != TEXT_PIN_POLICY:
        raise ValueError("packaging cannot omit the required text pin policy")
    by_id = {row["plan_id"]: row for row in review["results"]}
    if {row.get("plan_id") for row in config.get("outputs", [])} != set(by_id):
        raise ValueError("packaging config scope mismatch")
    for row in config["outputs"]:
        subtitle = require_ref(by_id[row["plan_id"]]["subtitle"], "corrected subtitle")
        if not isinstance(row.get("subtitle_design"), dict):
            raise ValueError("every packaging output requires a agent-authored subtitle_design")
        if row["subtitle_design"].get("reading_policy") != READING_POLICY:
            raise ValueError("subtitle design must bind the reviewed reading policy")
        supplied = Path(row.get("subtitle_txt", ""))
        if not supplied.is_absolute():
            supplied = args.config.parent / supplied
        if supplied.resolve() != subtitle or row.get("subtitle_sha256") != sha(subtitle):
            raise ValueError("packaging must use reviewed subtitle hash")
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    asset_copy = read(require_ref(value["asset_copy"], "verified asset copy"))
    order = read(require_ref(value["work_order"], "work order"))
    plan = read(require_ref(value["plan"], "cast packaging plan"))
    plan_by_id = {output["plan_id"]: output for output in plan["outputs"]}
    require_cast_binding(value, plan)
    for prepared in PACKAGER.prepared_rows(args.config.resolve()):
        output = plan_by_id[prepared["plan_id"]]
        CAST.verify_nameplates(output, EDITING.summary(output)["visual_shots"], prepared["nameplates"], value["celebrities"])
        PACKAGER.packaging_design.verify_sources(prepared["subtitle_design"], prepared["cues"], asset_copy,
            clean_by_id[prepared["plan_id"]]["expected_text"], json.dumps(order.get("packaging_request", ""), ensure_ascii=False))
    manifest = PACKAGER.delivery_category(processing_dir(value), "manifests") / "packaging_manifest.json"
    PACKAGER.render(args.config.resolve(), processing_dir(value), manifest,
                    clean_delivery=require_ref(value["clean_delivery"], "clean delivery"),
                    clean_qc=require_ref(value["clean_qc"], "clean QC"), overwrite=True,
                    resume=value["phase"] == "repair_required" and bool(value.get("pending_delivery_directory")))
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
    require_clean_qc(value)
    manifest_path = require_ref(value["packaging_delivery"], "packaging delivery")
    manifest = read(manifest_path)
    clean = read(require_ref(value["clean_delivery"], "clean delivery"))
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
    clean_by_id = {row["plan_id"]: row for row in clean["results"]}
    clean_qc_by_id = {row["plan_id"]: row for row in read(require_ref(value["clean_qc"], "clean QC"))["results"]}
    model = load_model(); rows = []; failures = []
    with background(len(manifest["results"])) as pool:
        warming = warm_final_visuals(pool, job, value, [(row["output_sha256"], Path(row["output_path"]))
                                                        for row in manifest["results"]])
        for row in manifest["results"]:
            pid = row["plan_id"]; output = Path(row["output_path"])
            if row.get("text_pin_policy") != TEXT_PIN_POLICY:
                raise ValueError("final output lost its required text pin policy")
            pin_coverage = PACKAGER.text_pin_coverage(row.get("text_pins", []), row["input_frames"])
            CAST.verify_nameplates(plan_by_id[pid], EDITING.summary(plan_by_id[pid])["visual_shots"],
                                   row.get("nameplates", []), value["celebrities"])
            if not output.is_file() or sha(output) != row["output_sha256"]:
                failures.append(f"{pid}: packaged output changed"); continue
            if row.get("input", {}).get("sha256") != clean_by_id[pid]["output"]["sha256"]:
                failures.append(f"{pid}: clean input binding mismatch"); continue
            spec = PACKAGER.video_spec(output)
            expected_frames = final_frames(video(require_ref(clean_by_id[pid]["output"], "clean input"))["frames"])
            if row.get("final_speed") != FINAL_SPEED or spec["frames"] != expected_frames:
                failures.append(f"{pid}: final speed/frame count changed"); continue
            errors = EDITING.frame_errors(spec["frames"], final=True)
            if errors:
                failures.extend(f"{pid}:{error}" for error in errors); continue
            if row["output_sha256"] in warming:
                warming[row["output_sha256"]].result()
            editing = final_editing_evidence(job, value, plan_by_id[pid], output, spec["frames"])
            asr = transcribe(model, output)
            output_audio = pcm(output)
            metrics = audio_metrics(output_audio)
            asr_match = normalized(asr["text"]) == normalized(clean_by_id[pid]["expected_text"])
            bgm_db = None
            if row.get("bgm"):
                bgm_db = voice_over_music_db(pcm(require_ref(clean_by_id[pid]["output"], "clean output")),
                                             pcm(Path(row["bgm"]["path"])), float(row["bgm"]["gain_db"]))
            render_value = read(require_ref(clean_by_id[pid]["render_evidence"], "render evidence"))
            audio_render_check(value, pid, render_value)
            mix_evidence = delivery_mix_evidence(output_audio, row, render_value)
            speech_preserved = mix_evidence["decision"] == "pass" and clean_qc_by_id[pid]["text_match"]
            source_cues = PACKAGER.parse_srt(Path(row["subtitles"]["path"]), round(row["input_frames"] / 60 * 1000))
            cues = PACKAGER.delivery_cues(row)
            speed = delivery_speed(row)
            output_frames = row["output_spec"]["frames"]
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
            for layer in [*row.get("nameplates", []), *row.get("text_pins", [])]:
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
            frame_numbers += PACKAGER.packaging_design.evidence_frames(row["design"], speed, output_frames)
            frame_numbers += [0, output_frames - 1]
            visual = frames(output, frame_numbers, job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "packaged_frames" / pid)
            decision = ((asr_match or speech_preserved) and subtitle_timing_pass and metrics["clipped_samples"] == 0
                        and all(cut["decision"] == "pass" for cut in cut_metrics)
                        and mix_evidence["audio_cleanup"]["decision"] == "pass"
                        and (bgm_db is None or bgm_db >= 6))
            if not decision:
                failures.append(f"{pid}: ASR/subtitle timing/clipping/BGM masking failure")
            result = {"plan_id": pid, "output": ref(output), "asr": asr, **editing, "asr_match": asr_match,
                      "speech_preserved": speech_preserved, "mix_evidence": mix_evidence,
                      "metrics": metrics, "cut_pcm": cut_metrics, "voice_over_bgm_db": bgm_db, "frames": visual,
                      "subtitle_cue_count": len(cues), "subtitle_timing_pass": subtitle_timing_pass,
                      "text_pin_coverage": pin_coverage, "design": row["design"],
                      "decision": "pass" if decision else "reject"}
            result["transitions"] = EDITING.final_transition_evidence(plan_by_id[pid], result)
            rows.append(result)
    report = job / "reports" / "final_evidence.json"
    write(report, {"schema": "video-montage-autonomous-final-evidence/v260929", "manifest": ref(manifest_path),
                   "text_pin_policy": TEXT_PIN_POLICY, "subtitle_review": value["subtitle_review"], "results": rows,
                   "planning_policy": EDITING.POLICY, "plan": value["plan"], **batch_fields(value),
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
            or len(by_id) != len(qc.get("results", [])) or set(by_id) != set(ids)
            or clean.get("plan_sha256") != value["plan"]["sha256"] or qc.get("plan_sha256") != value["plan"]["sha256"]
            or qc.get("speech_review_policy") != SPEECH.POLICY):
        raise ValueError("complete hash-bound, agent speech-reviewed clean QC required")
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}
    require_clean_batch(value, clean, qc)
    for row in clean["results"]:
        measured = by_id[row["plan_id"]]
        require_ref(row["output"], "clean output")
        render_value = read(require_ref(row["render_evidence"], "clean render evidence"))
        audio_render_check(value, row["plan_id"], render_value)
        if (measured.get("audio_cleanup", {}).get("decision") != "pass"
                or measured["audio_cleanup"].get("ranges") != AUDIO.verify_output(
                    AUDIO.decode(FFMPEG, Path(row["output"]["path"])), render_value)["ranges"]):
            raise ValueError("clean audio cleanup QC changed or missing")
        spec = video(Path(row["output"]["path"]))
        errors = EDITING.render_errors(plan_by_id[row["plan_id"]], render_value, spec["frames"])
        if errors or (spec["fps_num"], spec["fps_den"]) != (60, 1):
            raise ValueError("clean render constraints failed: " + "; ".join(errors))
        if (measured.get("output") != row["output"] or measured.get("text_match") is not True
                or measured.get("text_match_basis") != "agent_reviewed_rendered_shots"
                or measured.get("metrics", {}).get("clipped_samples") != 0
                or not isinstance(measured.get("cut_pcm"), list)
                or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])):
            raise ValueError("clean QC does not authorize this output")
        if not verified_shot_speech(measured.get("timing_asr", {}), row["output"], plan_by_id[row["plan_id"]], render_value):
            raise ValueError("clean QC rendered shot speech proof changed")
    return clean, qc


def clean_final_evidence(job: Path, value: dict) -> None:
    if value["phase"] != "clean_validated":
        raise ValueError("clean final evidence requires passing clean QC")
    require_editing_approval(value)
    clean, qc = require_clean_qc(value)
    rows = []; failures = []
    model = load_model()
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "plan"))["outputs"]}

    def accelerate(row: dict) -> dict:
        source = require_ref(row["output"], "clean output")
        delivery = PACKAGER.speed_clean(source, processing_dir(value) / "临时文件" / "final_clean" / f"{row['plan_id']}.mp4")
        delivery["plan_id"] = row["plan_id"]
        return delivery

    with background(len(clean["results"])) as pool:
        deliveries = list(pool.map(accelerate, clean["results"]))
        warming = warm_final_visuals(pool, job, value, [(delivery["output_sha256"], Path(delivery["output_path"]))
                                                        for delivery in deliveries])
        for row, delivery in zip(clean["results"], deliveries):
            output = Path(delivery["output_path"])
            render = read(require_ref(row["render_evidence"], "render evidence"))
            count = delivery["output_frames"]
            measured = PACKAGER.video_spec(output)["frames"]
            errors = EDITING.frame_errors(measured, final=True)
            if measured != count:
                errors.append("FINAL_DECODED_FRAME_COUNT_CHANGED")
            if errors:
                failures.extend(f"{row['plan_id']}:{error}" for error in errors); continue
            if delivery["output_sha256"] in warming:
                warming[delivery["output_sha256"]].result()
            editing = final_editing_evidence(job, value, plan_by_id[row["plan_id"]], output, measured)
            cuts = [math.ceil(cut / FINAL_SPEED) for cut in render_cut_frames(render)]
            numbers = {0, count - 1}
            for cut in [0, *cuts, count]:
                numbers.update(range(max(0, cut - 72), min(count, cut + 72)))
            visual = frames(output, sorted(numbers), job / "evidence" / "clean_final_frames" / row["plan_id"])
            audio = pcm(output); metrics = audio_metrics(audio)
            cut_metrics = cut_pcm_metrics(audio, cuts, clean=True)
            asr = transcribe(model, output)
            asr_match = normalized(asr["text"]) == normalized(row["expected_text"])
            audio_render_check(value, row["plan_id"], render)
            proof = delivery_mix_evidence(audio, delivery, render)
            passed = ((asr_match or proof["decision"] == "pass") and metrics["clipped_samples"] == 0
                      and proof["audio_cleanup"]["decision"] == "pass"
                      and all(cut["decision"] == "pass" for cut in cut_metrics))
            if not passed:
                failures.append(f"{row['plan_id']}: accelerated clean audio QC")
            result = {"plan_id": row["plan_id"], "output": ref(output), "frames": visual, **editing,
                      "asr": asr, "asr_match": asr_match, "mix_evidence": proof,
                      "metrics": metrics, "cut_pcm": cut_metrics, "decision": "pass" if passed else "reject"}
            result["transitions"] = EDITING.final_transition_evidence(plan_by_id[row["plan_id"]], result)
            rows.append(result)
    final_path = job / "manifests" / "final_clean_delivery.json"
    write(final_path, {"schema": "video-montage-final-clean/v1", "clean_delivery": value["clean_delivery"],
                       "final_speed": FINAL_SPEED, "results": deliveries})
    value["final_clean_delivery"] = ref(final_path)
    path = job / "reports" / "final_evidence.json"
    write(path, {"schema": "video-montage-autonomous-final-evidence/v260929", "delivery_mode": "clean",
                 "clean_delivery": value["clean_delivery"], "clean_qc": value["clean_qc"],
                 "final_clean_delivery": value["final_clean_delivery"],
                 "planning_policy": EDITING.POLICY, "plan": value["plan"], **batch_fields(value),
                 "results": rows, "decision": "reject" if failures else "pass", "failures": failures})
    value["final_evidence"] = ref(path)
    value["delivery_mode"] = "clean"
    if failures:
        fail_round(job, value, "final_evidence", failures)
    save(job, value, "final_evidenced")


def reburn(args) -> None:
    """Start a fresh subtitle review from the retained, verified clean inputs."""
    job = args.job_dir.resolve(); value = state(job)
    retry = value.get("reburn_only") and value["phase"] in {"subtitle_drafted", "subtitle_approved", "repair_required"}
    if (value["phase"] != "complete" and not retry) or not value.get("reburn_inputs"):
        raise ValueError("completed delivery with retained reburn inputs required")
    inputs = value["reburn_inputs"]
    for key in ("plan", "source_index", "asset_copy", "clean_delivery", "clean_qc", "packaging_delivery",
                "audio_cleanup_authorization", "editing_context"):
        require_ref(inputs[key], f"reburn {key}")
        value[key] = inputs[key]
    clean, qc = require_clean_qc(value)
    previous = read(require_ref(value["packaging_delivery"], "previous packaging"))
    subtitle_dir = getattr(args, "subtitle_dir", None)
    plan_id = getattr(args, "plan_id", None)
    subtitle_txt = getattr(args, "subtitle_txt", None)
    edits = {}
    if subtitle_dir is not None:
        if plan_id is not None or subtitle_txt is not None:
            raise ValueError("choose subtitle-dir or plan-id with subtitle-txt")
        for row in previous['results']:
            target = subtitle_dir.resolve() / PACKAGER.subtitle_filename(row['plan_id'])
            if not target.is_file(): raise FileNotFoundError(target)
            edits[row['plan_id']] = target
    else:
        if plan_id not in {row["plan_id"] for row in previous["results"]} or subtitle_txt is None:
            raise ValueError("known reburn plan-id and subtitle-txt required")
        edited = subtitle_txt.resolve()
        if not edited.is_file(): raise FileNotFoundError(edited)
        edits[plan_id] = edited
    value["repair_round"] = int(value.get("repair_round", 0)) + 1
    value.pop("compact_delivery", None)
    value.pop("completion", None)
    value["reburn_only"] = True
    require_editing_approval(value)
    value["delivery_mode"] = "packaged"
    pending = start_pending_delivery(job, value)
    PACKAGER.ensure_delivery_layout(pending)
    by_id = {row["plan_id"]: row for row in clean["results"]}
    drafts = []
    plan_by_id = {row["plan_id"]: row for row in read(require_ref(value["plan"], "reburn cast plan"))["outputs"]}
    for row in previous["results"]:
        pid = row["plan_id"]
        CAST.verify_nameplates(plan_by_id[pid], EDITING.summary(plan_by_id[pid])["visual_shots"],
                               row.get("nameplates", []), value["celebrities"])
        source = edits[pid] if pid in edits else require_ref(row["subtitle_snapshot"], "unchanged subtitle")
        duration = round(PACKAGER.video_spec(require_ref(by_id[pid]["output"], "clean output"))["duration"] * 1000)
        cues = PACKAGER.parse_srt(source, duration)
        TEXT_LIBRARY.require_complete_terms(cues, TEXT_LIBRARY.protected_terms(value["text_library"]),
                                            normalized, expected=by_id[pid]["expected_text"])
        snapshot = job / f"attempt-{value['repair_round'] + 1:02d}" / "evidence" / "subtitle_drafts" / PACKAGER.subtitle_filename(pid)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, snapshot)
        drafts.append({"plan_id": pid, "input_path": by_id[pid]["output"]["path"],
                       "subtitle_txt_path": str(snapshot), "subtitle_txt_sha256": sha(snapshot)})
    draft_path = job / "reports" / "subtitle_draft.json"
    write(draft_path, {"schema": "video-montage-autonomous-subtitle-draft/v260929",
                       "clean_delivery": value["clean_delivery"], "asset_copy": value["asset_copy"], "results": drafts,
                       **text_library_fields(value)})
    value["subtitle_draft"] = ref(draft_path)
    config = read(Path(previous["config_snapshot_path"]))
    PACKAGER.require_design_config(config)
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
    clean, qc = require_clean_qc(value)
    staging = job / f"attempt-{value['repair_round'] + 1:02d}" / "publication"
    staging.mkdir(parents=True, exist_ok=True)
    target_manifests = delivery / "临时文件" / "manifests"
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
                       "planning_policy": EDITING.POLICY, "plan_sha256": value["plan"]["sha256"]}
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
    if qc.get("batch_clean"):
        qc["batch_clean"]["clean_delivery"] = clean_ref
        for measured in qc["batch_clean"]["results"]:
            measured["output"] = next(row["output"] for row in published_clean["results"] if row["plan_id"] == measured["plan_id"])
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
            target = PACKAGER.delivery_category(delivery, "clean") / f"{row['plan_id']}.mp4"
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
                     clean_qc_path=str(qc_target), clean_qc_sha256=qc_ref["sha256"])
        package_path = staging / "packaging_manifest.json"
        package_target = target_manifests / package_path.name
        write(package_path, final)
        copies.append((package_path, package_target))
        published = {"path": str(package_target), "sha256": sha(package_path)}
        outputs = [{"path": row["output_path"], "sha256": row["output_sha256"]} for row in final["results"]]
    PACKAGER.publish_files(copies, job)
    value["published_delivery"] = published
    value["published_clean"] = clean_ref
    value["published_clean_qc"] = qc_ref
    for row in outputs:
        require_ref(row, "published authorized output")
    return outputs


def audit_original_sources(index: dict, plan: dict, *, reburn_only: bool = False) -> dict:
    """An overlay reburn needs used originals; retain unavailable unused inventory."""
    selected = {item.get("source_sha256") for output in plan.get("outputs", [])
                for key in ("segments", "visual_shots") for item in output.get(key, [])}
    unavailable = []
    verified = 0
    for row in index["sources"]:
        source = row["source"]
        if reburn_only and source["sha256"] not in selected and not Path(source["path"]).exists():
            unavailable.append(source)
        else:
            require_ref(source, "original source")
            verified += 1
        # Historical inventory remains immutable, even for an unused missing file.
        require_ref(row["asr"], "original source ASR")
        if row.get("reviewed_speech"):
            source_speech(row)
    return {"reburn_only": reburn_only, "inventory_source_count": len(index["sources"]),
            "verified_source_count": verified, "unavailable_unused_sources": unavailable}


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
    value["source_provenance_audit"] = audit_original_sources(
        index, read(require_ref(value["plan"], "source dependency plan")),
        reburn_only=bool(value.get("reburn_only")))
    if value.get("reburn_only"):
        require_clean_qc(value)
        errors = check_plan(read(require_ref(value["plan"], "reburn plan")), index,
                            int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]))
        if errors:
            raise ValueError(f"reburn source plan rejected: {errors}")
    else:
        for row in read(Path(value["plan_evidence"]["path"]))["results"]:
            require_ref(row["source"], "candidate source")
            require_ref(row["pcm"], "candidate PCM")
            for frame in row["frames"]:
                require_ref(frame, "candidate frame")
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
    if value["phase"] not in {"final_evidenced", "repair_required"} or not value.get("final_evidence"):
        raise ValueError("final evidence required")
    evidence_path = require_ref(value["final_evidence"], "final evidence")
    evidence = read(evidence_path); review = read(args.review)
    require_cast_binding(value, read(require_ref(value["plan"], "final cast plan")), evidence, review)
    if (review.get("schema") != REVIEW_SCHEMA or review.get("stage") != "final"
            or review.get("reviewer_role") != "agent" or review.get("evidence_sha256") != sha(evidence_path)):
        raise ValueError("agent final visual review binding invalid")
    if evidence.get("decision", "pass") != "pass":
        raise ValueError("passing final evidence required")
    if value["phase"] == "repair_required" and value.get("delivery_mode") != "clean" and evidence.get("manifest") != value.get("packaging_delivery"):
        raise ValueError("retry completion requires evidence for the unchanged packaged delivery")
    if value.get("delivery_mode") != "clean" and (evidence.get("text_pin_policy") != TEXT_PIN_POLICY
                                                  or review.get("text_pin_policy") != TEXT_PIN_POLICY):
        raise ValueError("final text pin policy review binding invalid")
    batch_errors = batch_review_errors(value, evidence, review)
    if batch_errors:
        fail_round(job, value, "agent_final_batch_review", batch_errors)
    if (evidence.get("planning_policy") != EDITING.POLICY or evidence.get("plan") != value["plan"]
            or review.get("planning_policy") != EDITING.POLICY):
        raise ValueError("final review binding invalid")
    plan = read(require_ref(value["plan"], "plan"))
    plan_by_id = {row["plan_id"]: row for row in plan["outputs"]}
    require_clean_qc(value)
    transition_errors = EDITING.transition_review_errors(plan, review, evidence)
    if transition_errors:
        fail_round(job, value, "agent_final_transition_review", transition_errors)
    audit_provenance(value)
    by_id = {row.get("plan_id"): row for row in review.get("outputs", [])}
    if set(by_id) != {row["plan_id"] for row in evidence["results"]} or len(by_id) != len(review.get("outputs", [])):
        raise ValueError("final review scope mismatch")
    for row in evidence["results"]:
        finding = by_id[row["plan_id"]]
        if value.get("delivery_mode") != "clean":
            if (row.get("text_pin_coverage", {}).get("decision") != "pass"
                    or finding.get("text_pins_pass") is not True
                    or not EDITING.specific_reason(finding.get("text_pin_reason"))):
                fail_round(job, value, "final_text_pin_review", [f"{row['plan_id']}: actual full-duration text pin review required"])
        if value.get("delivery_mode") != "clean" and (finding.get("nameplates_pass") is not True
                                        or not EDITING.specific_reason(finding.get("nameplate_reason"))):
            fail_round(job, value, "final_nameplate_review", [f"{row['plan_id']}: first-appearance nameplate review required"])
        if row.get("mix_evidence", {}).get("audio_cleanup", {}).get("decision") != "pass":
            fail_round(job, value, "final_audio_cleanup", [f"{row['plan_id']}: final audio cleanup proof missing or failed"])
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
            fail_round(job, value, "agent_final_editing_review", errors)
        required_passes = ("visual_pass",) if value.get("delivery_mode") == "clean" else ("visual_pass", "subtitle_pass", "overlay_pass")
        if (finding.get("output_sha256") != row["output"]["sha256"]
                or any(finding.get(key) is not True for key in required_passes) or not finding.get("reason")
                or (value.get("delivery_mode") != "clean" and not PACKAGER.packaging_design.review_ok(row.get("design"), finding))):
            fail_round(job, value, "agent_final_review", [f"{row['plan_id']}: incomplete visual finding"])
        require_ref(row["output"], "packaged output")
        for frame in row["frames"]:
            require_ref(frame, "reviewed packaged frame")
    technical_path = job / "reports" / "packaging_technical.json"
    if value.get("delivery_mode") == "clean":
        clean, _ = require_clean_qc(value)
        if evidence.get("clean_delivery") != value["clean_delivery"] or evidence.get("clean_qc") != value["clean_qc"]:
            raise ValueError("clean final evidence binding changed")
        final_path = require_ref(value["final_clean_delivery"], "accelerated clean delivery")
        final = read(final_path)
        if (evidence.get("final_clean_delivery") != ref(final_path)
                or final.get("clean_delivery") != value["clean_delivery"]
                or final.get("final_speed") != FINAL_SPEED
                or len(final.get("results", [])) != len(clean["results"])
                or {row["plan_id"] for row in final["results"]} != {row["plan_id"] for row in clean["results"]}):
            raise ValueError("accelerated clean binding changed")
        by_clean = {row["plan_id"]: row for row in clean["results"]}
        by_evidence = {row["plan_id"]: row for row in evidence["results"]}
        failures = []
        for row in final["results"]:
            output = require_ref({"path": row["output_path"], "sha256": row["output_sha256"]}, "accelerated clean output")
            spec = PACKAGER.video_spec(output)
            original = by_clean[row["plan_id"]]
            render = read(require_ref(original["render_evidence"], "render evidence"))
            measured = by_evidence[row["plan_id"]]
            if (row["input"] != original["output"] or row.get("final_speed") != FINAL_SPEED
                    or row.get("input_frames") != render["actual_output_frames"]
                    or measured.get("output") != ref(output) or measured.get("decision") != "pass"
                    or measured.get("metrics", {}).get("clipped_samples") != 0
                    or not isinstance(measured.get("cut_pcm"), list)
                    or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])
                    or (measured.get("asr_match") is not True and measured.get("mix_evidence", {}).get("decision") != "pass")):
                failures.append(f"{row['plan_id']}: accelerated clean evidence")
            if ((spec["width"], spec["height"]) != (1440, 2560)
                    or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac"
                    or not PACKAGER.packaged_streams_ok(spec) or spec["frames"] != final_frames(render["actual_output_frames"])):
                failures.append(f"{row['plan_id']}: clean specification")
            run([str(FFMPEG), "-v", "error", "-i", str(output), "-f", "null", "NUL"])
        technical = {"decision": "reject" if failures else "pass", "failures": failures, "delivery_mode": "clean"}
        write(technical_path, technical)
    else:
        package_path = require_ref(value["packaging_delivery"], "packaging delivery")
        packaged_rows = read(package_path).get("results", [])
        if not packaged_rows or any(row.get("final_speed") != FINAL_SPEED for row in packaged_rows):
            raise ValueError("final 1.2x packaging required; rerender and obtain new final evidence")
        technical = PACKAGER.validate(package_path, technical_path,
                                      autonomous_evidence=evidence_path, autonomous_review=args.review)
    if technical["decision"] != "pass":
        fail_round(job, value, "packaging_technical", technical["failures"])
    published_outputs = publish_delivery(job, value)
    receipt = job / "video_montage_autonomous_completion.json"
    write(receipt, {"schema": "video-montage-autonomous-completion/v260929", "decision": "pass",
                    **POLICIES, "celebrities": value["celebrities"],
                    "repair_round_count": value.get("repair_round", 0),
                    "review_mode": "agent_asr_pcm", "forced_alignment_claimed": False,
                    "human_listening_claimed": False, "work_order": value["work_order"],
                    "source_index": value["source_index"], "asset_copy": value["asset_copy"],
                    "plan": value["plan"], "delivery_mode": value.get("delivery_mode", "packaged"),
                    "reburn_only": value.get("reburn_only", False),
                    **({"source_provenance_audit": value["source_provenance_audit"]} if value.get("source_provenance_audit") else {}),
                    **{key: value[key] for key in ("plan_evidence", "plan_review", "subtitle_review", "packaging_delivery", "published_delivery") if value.get(key)},
                    "clean_delivery": value["clean_delivery"],
                    "clean_qc": value["clean_qc"], "packaging_technical": ref(technical_path),
                    "final_evidence": value["final_evidence"], "final_review": ref(args.review),
                    "outputs": published_outputs})
    value["completion"] = ref(receipt)
    save(job, value, "complete")
    PACKAGER.ensure_delivery_layout(attempt_dir(value))
    compact_delivery(job, value)
    print(str(job / "autonomous_state.json"))


def compact_delivery(job: Path, value: dict) -> None:
    """Retain reburn inputs and the signed completion record with final findings."""
    root = job.resolve()
    keep = {root / "autonomous_state.json", require_ref(value["work_order"], "work order")}
    if value.get("completion"):
        completion_path = require_ref(value["completion"], "completion receipt")
        keep.add(completion_path.resolve())
        completion = read(completion_path)
        for key in ("final_evidence", "final_review", "packaging_technical"):
            if completion.get(key):
                keep.add(require_ref(completion[key], key).resolve())
    for folder in (root / "config", root / "manifests"):
        if folder.exists():
            keep.update(path.resolve() for path in folder.rglob("*") if path.is_file())
    # Reburn's render gate still needs the bound clean manifest and its QC receipt.
    package_reference = value.get("published_delivery") or value.get("packaging_delivery")
    packaging_path = require_ref(package_reference, "published delivery")
    packaging = read(packaging_path)
    if packaging.get("clean_qc_path"):
        previous_qc = Path(packaging["clean_qc_path"]).resolve()
        reusable_qc = root / "manifests" / "clean_validation.json"
        reusable_qc.parent.mkdir(parents=True, exist_ok=True)
        if previous_qc != reusable_qc:
            shutil.copyfile(previous_qc, reusable_qc)
        packaging["clean_qc_path"] = str(reusable_qc)
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

    for key in ("clean_delivery_path", "clean_qc_path"):
        if packaging.get(key):
            keep.add(Path(packaging[key]).resolve())
    retained = {key: value[key] for key in (
        "schema", "review_mode", "work_order", "source_hashes", "asset_root", "output_root", "delivery_directory",
        "repair_round", *POLICIES, "celebrities", "subtitle_segmentation_policy", "text_library", "completion") if key in value}
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
                    if row.get("reviewed_speech"):
                        # Raw observations and their canonical review have immutable references.
                        SPEECH.retain_refs(row["reviewed_speech"], root, keep)
                        keep.add(asr.resolve())
                        continue
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
                                  ("clean_qc", "published_clean_qc", "clean_qc_path")):
        if value.get(preferred):
            inputs[key] = value[preferred]
        elif packaging.get(field):
            inputs[key] = ref(Path(packaging[field]))
    context_path = root / "manifests" / "editing_context.json"
    write(context_path, {"planning_policy": EDITING.POLICY, "decision": "pass", "basis": "validated_clean_input",
                         "plan": inputs["plan"], "clean_delivery": inputs["clean_delivery"],
                         "outputs": [EDITING.summary(output) for output in read(Path(inputs["plan"]["path"]))["outputs"]],
                         "batch_review_policy": BATCH.POLICY,
                         "batch_report_digest": BATCH.digest(batch_fields(value)["batch_review_report"])})
    inputs["editing_context"] = ref(context_path)
    keep.add(context_path)
    original = read(require_ref(value["audio_cleanup_authorization"], "audio cleanup authorization"))
    target = root / "manifests" / "audio_cleanup_authorization.json"
    write(target, {"policy": AUDIO.POLICY, "plan": inputs["plan"], "clean_delivery": inputs["clean_delivery"],
                   "segments": original["segments"], "original_authorization_digest": AUDIO.digest(original)})
    inputs["audio_cleanup_authorization"] = ref(target)
    # A completed compact job can validate its retained clean audio directly.
    retained.update(audio_cleanup_authorization=inputs["audio_cleanup_authorization"], plan=inputs["plan"],
                    clean_delivery=inputs["clean_delivery"])
    keep.add(target)
    # Shot timing approvals bind actual WAV bytes; these are required for reburn.
    if inputs.get("clean_qc"):
        for row in read(require_ref(inputs["clean_qc"], "retained clean QC")).get("results", []):
            if row.get("timing_asr", {}).get("review_basis") == SPEECH.POLICY:
                SPEECH.retain_refs(row["timing_asr"]["review_context"], root, keep)
            for observation in row.get("timing_asr", {}).get("shot_observations", []):
                keep.add(require_ref(observation["audio"], "retained rendered shot audio").resolve())
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


def batch_check(args) -> None:
    """Diagnose a plan against the batch limits without approving anything (exit 2 = rejected)."""
    job = args.job_dir.resolve(); value = state(job)
    report = BATCH.audit(read(args.plan), read(require_ref(value["source_index"], "source index")),
                         int(read(require_ref(value["work_order"], "work order"))["requested_outputs"]))
    target = job / "reports" / "batch_check.json"
    write(target, report)
    print(json.dumps({"decision": report["decision"], "failures": len(report["failures"]), "report": str(target)}))
    if report["decision"] != "pass":
        raise SystemExit(2)


def review_digest(args) -> None:
    """Print the batch_review.report_digest of a plan or final evidence file."""
    print(BATCH.digest(read(args.evidence)["batch_review_report"]))


def speech_diff(args) -> None:
    """Print the correction spans a speech review must list for observed -> reviewed text."""
    print(json.dumps(SPEECH.changes(args.observed, args.reviewed, normalized)))


def remaster_audio(args) -> None:
    """Rebuild the packaged audio of a finished delivery at the current loudness policy (picture copied)."""
    record = REMASTER.remaster(args.delivery_dir)
    print(json.dumps({"decision": record["decision"], "outputs": len(record["results"]),
                      "report": str(args.delivery_dir.resolve() / "临时文件" / "reports" / "audio_remaster.json")}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "prepare", "asset-copy", "source-speech-evidence", "source-speech-review", "plan-evidence",
                 "approve-plan", "render-clean", "clean-qc", "clean-speech-review", "subtitle-draft", "subtitle-review",
                 "package", "final-evidence", "complete", "repair", "reburn", "status", "batch-check"):
        action = sub.add_parser(name)
        action.add_argument("--job-dir", type=Path, required=name != "init")
        if name == "init":
            action.add_argument("--work-order", type=Path, required=True, help="work-order JSON path; use - to read JSON from stdin")
        if name == "asset-copy":
            action.add_argument("--copy-text", type=Path, required=True)
        if name in {"plan-evidence", "batch-check"}:
            action.add_argument("--plan", type=Path, required=True)
        if name in {"approve-plan", "source-speech-review", "clean-speech-review", "subtitle-review", "complete"}:
            action.add_argument("--review", type=Path, required=True)
        if name == "package":
            action.add_argument("--config", type=Path, required=True)
        if name == "final-evidence":
            action.add_argument("--clean", action="store_true")
        if name == "subtitle-draft":
            action.add_argument("--subtitle-dir", type=Path, help="complete reviewed resegmentation batch; refresh draft snapshots before review")
        if name == "reburn":
            action.add_argument("--plan-id")
            action.add_argument("--subtitle-txt", type=Path)
            action.add_argument("--subtitle-dir", type=Path, help="complete batch of subtitle-<plan_id>.txt files")
        if name == "repair":
            action.add_argument("--reason", required=True)
    digest_parser = sub.add_parser("review-digest", help="print batch_review.report_digest of an evidence file")
    digest_parser.add_argument("--evidence", type=Path, required=True)
    diff_parser = sub.add_parser("speech-diff", help="print the correction spans for observed -> reviewed text")
    diff_parser.add_argument("--observed", required=True)
    diff_parser.add_argument("--reviewed", required=True)
    remaster_parser = sub.add_parser("remaster-audio", help="rebuild the audio of a finished packaged delivery")
    remaster_parser.add_argument("--delivery-dir", type=Path, required=True)
    args = parser.parse_args()
    helpers = {"review-digest": review_digest, "speech-diff": speech_diff, "batch-check": batch_check,
               "remaster-audio": remaster_audio}
    if args.command in helpers:
        helpers[args.command](args)
        return
    actions = {"init": init, "prepare": prepare, "asset-copy": asset_copy,
               "source-speech-evidence": source_speech_evidence, "source-speech-review": source_speech_review,
               "plan-evidence": plan_evidence,
               "approve-plan": approve_plan, "render-clean": render_clean, "clean-qc": clean_qc, "clean-speech-review": clean_speech_review,
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
