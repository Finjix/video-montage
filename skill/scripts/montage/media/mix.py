"""Decoded-audio checks and the sample-level proof of the final 1.2x mix."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

from montage.common import FFMPEG, require_ref, run
from montage.media import audio_cleanup as AUDIO

FINAL_SPEED = 1.2


def final_frames(input_frames: int) -> int:
    # Round up to a complete output frame so the last source frame is retained.
    return (input_frames * 5 + 5) // 6


def delivery_speed(row: dict) -> float:
    speed = row.get("final_speed", 1.0)  # Existing manifests remain readable.
    if speed not in (1.0, FINAL_SPEED):
        raise ValueError("invalid final delivery speed")
    return speed


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
            max(before_db, after_db) <= -30 if clean else
            max(before_db, after_db) <= -30 or abs(before_db - after_db) <= 18)
        results.append({"frame": frame, "pre_40ms_dbfs": round(before_db, 2),
                        "post_40ms_dbfs": round(after_db, 2), "isolated_transients": impact,
                        "clipped_samples": clipped, "decision": "pass" if passed else "reject"})
    return results


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


def delivery_mix_evidence(output_audio: np.ndarray, row: dict, cleanup_render: dict) -> dict:
    """Rebuild the same full mix and tempo transform from authorized inputs."""
    speed = delivery_speed(row)
    require_ref(row["input"], "delivery audio input")
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", row["input"]["path"]]
    if row.get("bgm"):
        require_ref(row["bgm"], "delivery BGM")
        command += ["-stream_loop", "-1", "-i", row["bgm"]["path"]]
        filters = (f"[1:a]volume={row['bgm']['gain_db']}dB[m];"
                   "[0:a][m]amix=inputs=2:duration=first:normalize=0,")
    else:
        filters = "[0:a]"
    filters += f"asetpts=PTS-STARTPTS,atempo={speed},apad[a]"
    # Match encoder sample rate and channel negotiation before decoding to mono.
    with tempfile.TemporaryDirectory(prefix="montage-tempo-proof-") as directory:
        expected_path = Path(directory) / "expected.m4a"
        command += ["-filter_complex", filters, "-map", "[a]", "-vn", "-c:a", "aac",
                    "-b:a", "192k", "-ar", "48000", "-t",
                    f"{row.get('output_frames', round(row['input_frames'] / speed)) / 60:.6f}", str(expected_path)]
        run(command)
        expected = pcm(expected_path)
        cleanup_proof = AUDIO.verify_output(AUDIO.decode(FFMPEG, Path(row["output_path"])), cleanup_render,
                                            speed, AUDIO.decode(FFMPEG, expected_path))
    proof = packaged_mix_evidence(output_audio, expected, None)
    proof["basis"] = f"hash_bound_clean_mix_then_atempo_{speed}"
    proof["audio_cleanup"] = cleanup_proof
    if cleanup_proof["decision"] != "pass":
        proof["decision"] = "reject"
    return proof
