"""Evidence-led spoken-audio cleanup. Detection never authorizes a mute."""
from __future__ import annotations

import math
import re
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from montage.common import digest, ref as file_ref, require_ref

POLICY = "spoken-audio-cleanup/v1"
RATE = 48000
FADE_SAMPLES = 240  # 5 ms; fades are inside the approved range.
CLASSES = {"previous_next_speech", "non_speech_noise", "natural_expression", "valid_speech", "uncertain"}
MUTABLE = {"previous_next_speech", "non_speech_noise"}


def normalized(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def bounds(segment: dict) -> tuple[int, int]:
    scale = Fraction(RATE * segment["source_fps_den"], segment["source_fps_num"])
    return round(segment["source_in_frame"] * scale), round(segment["source_out_frame_exclusive"] * scale)


def decode(ffmpeg: Path, path: Path, start: int = 0, end: int | None = None) -> np.ndarray:
    # WAV preserves all source channels; averaging stereo could hide antiphase noise.
    filters = f"aresample={RATE},atrim=start_sample={start}"
    if end is not None:
        filters += f":end_sample={end}"
    result = subprocess.run([str(ffmpeg), "-v", "error", "-nostdin", "-i", str(path),
        "-af", filters + ",asetpts=PTS-STARTPTS", "-vn", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
        capture_output=True, check=True)
    import io
    with wave.open(io.BytesIO(result.stdout), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").reshape(-1, handle.getnchannels()).astype(np.float32) / 32768


def write_pcm(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(samples.shape[1]); handle.setsampwidth(2); handle.setframerate(RATE)
        handle.writeframes(np.clip(samples * 32768, -32768, 32767).astype("<i2").tobytes())


def source_words(asr: dict, segment: dict) -> tuple[list[dict], list[dict], str]:
    words, text = [], ""
    for turn in asr.get("segments", []):
        for word in turn.get("words", []):
            token = normalized(word["word"])
            if not token:
                continue
            words.append({"text": word["word"], "start_sample": round(word["start"] * RATE),
                          "end_sample": round(word["end"] * RATE), "char_start": len(text),
                          "char_end": len(text) + len(token)})
            text += token
    expected = normalized(segment["text"])
    matches, pos = [], 0
    while expected and (pos := text.find(expected, pos)) >= 0:
        selected = [w for w in words if w["char_end"] > pos and w["char_start"] < pos + len(expected)]
        lo, hi = bounds(segment)
        if selected and selected[0]["start_sample"] >= lo - RATE and selected[-1]["end_sample"] <= hi + RATE:
            distance = abs((selected[0]["start_sample"] + selected[-1]["end_sample"]) / 2 - (lo + hi) / 2)
            matches.append((distance, pos, selected))
        pos += 1
    if not matches:
        return words, [], "unresolved"
    _, pos, selected = min(matches, key=lambda item: item[0])
    if selected[0]["char_start"] != pos or selected[-1]["char_end"] != pos + len(expected):
        return words, [], "partial_word"
    target = [{**w, "word_index": i} for i, w in enumerate(selected)]
    return words, target, "matched"


def scan(samples: np.ndarray, origin: int, segment: dict, asr: dict, probabilities: np.ndarray) -> dict:
    """20-ms energy / 10-ms steps plus 32-ms VAD. All events are proposals."""
    lo, hi = bounds(segment)
    words, target, alignment = source_words(asr, segment)
    frames = []
    for start in range(0, len(samples), 480):
        chunk = samples[start:start + 960]
        power = float(np.max(np.mean(chunk * chunk, axis=0)))
        frames.append({"start_sample": origin + start, "end_sample": origin + start + len(chunk),
                       "rms_dbfs": round(10 * math.log10(max(power, 1e-12)), 2),
                       "peak": round(float(np.max(np.abs(chunk))), 6),
                       "speech_probability": round(float(probabilities[min(start // 1536, len(probabilities) - 1)]), 4)})
    floor = float(np.percentile([f["rms_dbfs"] for f in frames], 15)) if frames else -90
    threshold = max(-55.0, min(-35.0, floor + 12))
    active = [f for f in frames if f["rms_dbfs"] >= threshold and f["end_sample"] > lo and f["start_sample"] < hi]
    groups = []
    for frame in active:
        if groups and frame["start_sample"] <= groups[-1][-1]["end_sample"] + 960:
            groups[-1].append(frame)
        else:
            groups.append([frame])
    candidates = []
    target_chars = {(w["char_start"], w["char_end"]) for w in target}
    for group in groups:
        a, b = max(lo, group[0]["start_sample"]), min(hi, group[-1]["end_sample"])
        overlapping = [w for w in words if w["start_sample"] < b and w["end_sample"] > a]
        retained = [w for w in overlapping if (w["char_start"], w["char_end"]) in target_chars]
        external = [w for w in overlapping if w not in retained]
        speech = max(f["speech_probability"] for f in group)
        # Normal spoken words are protected, while short isolated impulses remain inspectable.
        impulse = b - a <= 1920 and max(f["peak"] for f in group) >= .15
        if retained and not external and speech >= .5 and not impulse:
            continue
        suggested = "previous_next_speech" if external else ("valid_speech" if retained else "uncertain")
        candidates.append({"candidate_id": f"event-{len(candidates) + 1:03d}", "start_sample": a,
                           "end_sample": b, "suggested_class": suggested, "timestamp_conflict": bool(retained and external),
                           "overlapping_words": overlapping, "max_speech_probability": speech,
                           "max_rms_dbfs": max(f["rms_dbfs"] for f in group), "automatic_mute": False})
    return {"policy": POLICY, "sample_rate": RATE, "source_sha256": segment["source_sha256"],
            "segment_start_sample": lo, "segment_end_sample": hi, "expected_text": segment["text"],
            "alignment_status": alignment, "target_words": target,
            "context_words": [w for w in words if w["end_sample"] > origin and w["start_sample"] < origin + len(samples)],
            "energy_threshold_dbfs": threshold, "waveform": frames, "candidates": candidates}


def analyze(ffmpeg: Path, source: Path, segment: dict, asr: dict, directory: Path) -> dict:
    from montage.media import silero_vad
    lo, hi = bounds(segment)
    origin = max(0, lo - RATE)
    samples = decode(ffmpeg, source, origin, hi + RATE)
    if len(samples) < hi - origin:
        raise ValueError("audio cleanup source shorter than selected interval")
    # Use the strongest source channel rather than cancelling opposite-phase channels.
    channel = int(np.argmax(np.mean(samples * samples, axis=0)))
    mono = samples[::3, channel].copy()
    probabilities = silero_vad.get_vad_model()(np.pad(mono, (0, 512 - len(mono) % 512)))
    evidence = scan(samples, origin, segment, asr, probabilities)
    pcm_path = directory / "context.wav"
    write_pcm(pcm_path, samples)
    model_path = silero_vad.MODEL_PATH
    evidence.update(context_pcm=file_ref(pcm_path), vad_model_sha256=file_ref(model_path)["sha256"],
                    source_asr_digest=digest(asr), parameters={"window_ms": 20, "step_ms": 10,
                    "context_seconds": 1, "vad_window_ms": 32, "fade_samples": FADE_SAMPLES})
    preview = directory / "waveform.png"
    draw_waveform(evidence, preview)
    evidence["waveform_preview"] = file_ref(preview)
    evidence["evidence_digest"] = digest(evidence)
    return evidence


def draw_waveform(evidence: dict, path: Path) -> None:
    image = Image.new("RGB", (1600, 280), "white")
    draw = ImageDraw.Draw(image)
    frames = evidence["waveform"]
    first, last = frames[0]["start_sample"], frames[-1]["end_sample"]
    x = lambda sample: 40 + int((sample - first) / max(1, last - first) * 1520)
    for word in evidence["target_words"]:
        draw.rectangle((x(word["start_sample"]), 35, x(word["end_sample"]), 240), fill="#e1f0e4")
    for row in evidence["candidates"]:
        draw.rectangle((x(row["start_sample"]), 35, x(row["end_sample"]), 240), fill="#ffd9bf")
    for frame in frames:
        y = 235 - int((max(-90, frame["rms_dbfs"]) + 90) / 90 * 180)
        draw.line((x(frame["start_sample"]), 235, x(frame["start_sample"]), y), fill="#174f87")
    for second in range(math.ceil(first / RATE), math.floor(last / RATE) + 1):
        draw.text((x(second * RATE), 245), f"{second}s", fill="black")
    draw.text((40, 10), "Source timeline | energy dBFS | green: selected words | orange: review candidates", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def verify_evidence(evidence: dict, segment: dict) -> None:
    expected = {k: v for k, v in evidence.items() if k != "evidence_digest"}
    if (evidence.get("policy") != POLICY or evidence.get("sample_rate") != RATE
            or evidence.get("evidence_digest") != digest(expected)
            or evidence.get("source_sha256") != segment["source_sha256"]
            or (evidence.get("segment_start_sample"), evidence.get("segment_end_sample")) != bounds(segment)
            or evidence.get("expected_text") != segment["text"]):
        raise ValueError("audio cleanup evidence binding invalid")
    require_ref(evidence["context_pcm"], "audio cleanup context")
    require_ref(evidence["waveform_preview"], "audio cleanup waveform")


def _range(row: dict, lo: int, hi: int) -> tuple[int, int]:
    a, b = row.get("start_sample"), row.get("end_sample")
    if type(a) is not int or type(b) is not int or not lo <= a < b <= hi:
        raise ValueError("audio cleanup range outside selected source samples")
    return a, b


def compile_review(evidence: dict, review: dict, segment: dict) -> dict:
    verify_evidence(evidence, segment)
    if (review.get("policy") != POLICY or review.get("evidence_digest") != evidence["evidence_digest"]
            or review.get("natural_expression_preserved") is not True):
        raise ValueError("audio cleanup review missing or stale")
    lo, hi = bounds(segment)
    protected = review.get("protected_speech_ranges", [])
    target = evidence["target_words"]
    if evidence["alignment_status"] != "matched" or len(protected) != len(target):
        raise ValueError("audio cleanup spoken word alignment unresolved")
    for word, row in zip(target, protected):
        a, b = _range(row, lo, hi)
        if row.get("word_index") != word["word_index"] or row.get("text") != word["text"]:
            raise ValueError("audio cleanup protected words changed")
        original = max(lo, word["start_sample"]), min(hi, word["end_sample"])
        if (a, b) != original:
            # Timing corrections require a local signal valley and a reason, not ASR alone.
            if (not row.get("boundary_reason") or max(abs(a - original[0]), abs(b - original[1])) > RATE * .75
                    or not a < original[1] or not b > original[0]):
                raise ValueError("audio cleanup unsupported word boundary correction")
            moved = [(min(a, original[0]), max(a, original[0])), (min(b, original[1]), max(b, original[1]))]
            for left, right in moved:
                if right - left > 480 and not any(f["start_sample"] >= left and f["end_sample"] <= right
                        and f["rms_dbfs"] < evidence["energy_threshold_dbfs"] for f in evidence["waveform"]):
                    raise ValueError("audio cleanup word correction lacks waveform valley")
    candidates = {r["candidate_id"]: r for r in evidence["candidates"]}
    decisions = review.get("decisions", [])
    if len(decisions) != len(candidates) or {r.get("candidate_id") for r in decisions} != set(candidates):
        raise ValueError("audio cleanup requires one decision for every candidate")
    mutes = []
    for row in decisions:
        kind, action = row.get("classification"), row.get("action")
        if kind not in CLASSES or kind == "uncertain" or action not in {"retain", "mute"} or not row.get("reason"):
            raise ValueError("audio cleanup uncertain or incomplete candidate decision")
        if action == "retain":
            continue
        if kind not in MUTABLE:
            raise ValueError("audio cleanup cannot mute natural expression or valid speech")
        a, b = _range(row, lo, hi)
        candidate = candidates[row["candidate_id"]]
        if a < candidate["start_sample"] or b > candidate["end_sample"] or b - a <= FADE_SAMPLES * 2:
            raise ValueError("audio cleanup mute exceeds reviewed event or is too short")
        if any(a < p["end_sample"] and b > p["start_sample"] for p in protected):
            raise ValueError("audio cleanup mute intersects protected spoken samples")
        mutes.append({"start_sample": a, "end_sample": b, "fade_samples": FADE_SAMPLES,
                      "classification": kind, "reason": row["reason"]})
    mutes.sort(key=lambda r: r["start_sample"])
    if any(a["end_sample"] > b["start_sample"] for a, b in zip(mutes, mutes[1:])):
        raise ValueError("audio cleanup overlapping mute ranges")
    result = {"policy": POLICY, "sample_rate": RATE, "source_sha256": segment["source_sha256"],
              "segment_start_sample": lo, "segment_end_sample": hi, "expected_text": segment["text"],
              "protected_speech_ranges": protected, "mute_ranges": mutes,
              "evidence_digest": evidence["evidence_digest"], "review_digest": digest(review)}
    result["authorization_digest"] = digest(result)
    return result


def validate_cleanup(cleanup: dict, segment: dict) -> None:
    if (cleanup.get("policy") != POLICY or cleanup.get("sample_rate") != RATE
            or cleanup.get("source_sha256") != segment["source_sha256"]
            or (cleanup.get("segment_start_sample"), cleanup.get("segment_end_sample")) != bounds(segment)
            or cleanup.get("expected_text") != segment.get("text")
            or cleanup.get("authorization_digest") != digest({k: v for k, v in cleanup.items() if k != "authorization_digest"})):
        raise ValueError("audio cleanup render authorization changed")
    lo, hi = bounds(segment)
    protected = cleanup.get("protected_speech_ranges", [])
    if normalized("".join(p.get("text", "") for p in protected)) != normalized(segment["text"]):
        raise ValueError("audio cleanup protected text incomplete")
    for p in protected:
        _range(p, lo, hi)
    previous_end = lo
    for row in cleanup["mute_ranges"]:
        a, b = _range(row, lo, hi)
        if (row.get("fade_samples") != FADE_SAMPLES or a < previous_end or b - a <= FADE_SAMPLES * 2
                or row.get("classification") not in MUTABLE
                or any(a < p["end_sample"] and b > p["start_sample"] for p in protected)):
            raise ValueError("audio cleanup invalid mute envelope")
        previous_end = b


def filter_chain(cleanup: dict, segment: dict) -> str:
    validate_cleanup(cleanup, segment)
    lo, hi = bounds(segment)
    factors = []
    for row in cleanup["mute_ranges"]:
        a, b, fade = row["start_sample"] - lo, row["end_sample"] - lo, row["fade_samples"]
        factors.append(f"if(lt(n,{a}),1,if(lt(n,{a + fade}),({a + fade}-n)/{fade},"
                       f"if(lt(n,{b - fade}),0,if(lt(n,{b}),(n-{b - fade})/{fade},1))))")
    expression = "*".join(factors) or "1"
    return f"aresample={RATE},atrim=start_sample={lo}:end_sample={hi},asetpts=PTS-STARTPTS,aeval=exprs='val(ch)*({expression})':c=same"


def output_ranges(rendered: dict, final_speed: float = 1.0) -> list[dict]:
    rows, cursor = [], 0.0
    for segment in rendered["segments"]:
        cleanup = segment.get("audio_cleanup")
        if cleanup:
            lo = cleanup["segment_start_sample"]
            scale = float(segment.get("speed", 1.0)) * final_speed
            for mute in cleanup["mute_ranges"]:
                rows.append({**mute, "output_start_sample": round((cursor + (mute["start_sample"] - lo + FADE_SAMPLES) / RATE / float(segment.get("speed", 1.0))) * RATE / final_speed),
                             "output_end_sample": round((cursor + (mute["end_sample"] - lo - FADE_SAMPLES) / RATE / float(segment.get("speed", 1.0))) * RATE / final_speed),
                             "boundary_margin_samples": math.ceil(1024 / scale)})
        cursor += segment["expected_output_frames"] / 60
    return rows


def verify_output(samples: np.ndarray, rendered: dict, final_speed: float = 1.0,
                  expected_mix: np.ndarray | None = None) -> dict:
    if expected_mix is not None and samples.shape[1] != expected_mix.shape[1]:
        raise ValueError("audio cleanup mix channel layout changed")
    results = []
    for row in output_ranges(rendered, final_speed):
        # AAC/atempo smear a few samples near ramps; inspect the interior and all channels.
        available = row["output_end_sample"] - row["output_start_sample"]
        margin = min(row["boundary_margin_samples"], max(0, (available - 480) // 4))
        a, b = row["output_start_sample"] + margin, row["output_end_sample"] - margin
        if a >= b or b > len(samples):
            results.append({**row, "decision": "reject", "reason": "mute interior too short or output missing"})
            continue
        actual = samples[a:b]
        if expected_mix is not None:
            if b > len(expected_mix):
                raise ValueError("audio cleanup expected mix shorter than output")
            actual = actual - expected_mix[a:b]
        rms = math.sqrt(float(np.max(np.mean(actual * actual, axis=0))))
        db = 20 * math.log10(max(rms, 1e-9))
        jump = max(float(np.max(np.abs(np.diff(samples[max(0, edge - 480):min(len(samples), edge + 480)], axis=0))))
                   for edge in (row["output_start_sample"], row["output_end_sample"]))
        results.append({**row, "residual_dbfs": round(db, 2), "max_boundary_delta": round(jump, 6),
                        "decision": "pass" if db <= (-45 if expected_mix is not None else -60) and jump < .25 else "reject"})
    return {"policy": POLICY, "sample_rate": RATE, "final_speed": final_speed, "ranges": results,
            "decision": "pass" if all(r["decision"] == "pass" for r in results) else "reject"}
