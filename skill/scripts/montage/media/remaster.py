"""Rebuild the audio of a finished packaged delivery at the current loudness policy.

The packaged picture is stream-copied. The voice is the delivery's own unaccelerated clean video,
scaled by one static gain to the delivery loudness, mixed with the BGM at its automatic gain, then
accelerated and encoded exactly like packaging does. Nothing is published unless every output passes.
"""

from __future__ import annotations

import math
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from montage.common import FFMPEG, read, require_ref, run, sha, workers, write
from montage.media import audio_balance
from montage.media.mix import FINAL_SPEED, MIN_VOICE_OVER_MUSIC_DB, auto_bgm_gain_db, pcm, voice_over_music_db
from montage.packaging.delivery_files import publish_files
from montage.packaging.packager import packaged_streams_ok, video_spec

POLICY = "audio-remaster/v1"
TARGET_LUFS = audio_balance.DEFAULTS["target_lufs"]
TOLERANCE_LU = 1.0
SAMPLE_PEAK_CEILING_DB = -1.0
MIN_ENVELOPE_CORRELATION = 0.95  # 50 ms energy envelope vs the previous audio: same words, same timing
ENVELOPE_WINDOW = 800  # samples at 16 kHz


def envelope(audio: np.ndarray) -> np.ndarray:
    # atempo picks splice points from the signal, so the waveform is not sample-aligned across
    # different inputs; the energy envelope is.
    count = len(audio) // ENVELOPE_WINDOW
    return np.sqrt((audio[:count * ENVELOPE_WINDOW].reshape(count, ENVELOPE_WINDOW) ** 2).mean(axis=1))


def video_packets_md5(path: Path) -> str:
    out = run([FFMPEG, "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"])
    return out.strip()


def remaster_one(row: dict, stage: Path) -> dict:
    plan_id = row["plan_id"]
    if not row.get("bgm"):
        raise ValueError(f"{plan_id}: unpackaged outputs have no BGM mix to rebuild")
    clean = require_ref(row["input"], f"{plan_id} clean input")
    old = Path(row["output_path"]).resolve()
    if not old.is_file():
        raise ValueError(f"{plan_id}: packaged output missing")
    music_path = require_ref(row["bgm"], f"{plan_id} BGM")
    frames = int(row["output_frames"])

    measured = audio_balance.measure(FFMPEG, clean, "aformat=channel_layouts=stereo")
    if measured["integrated_lufs"] is None or measured["true_peak_db"] is None:
        raise ValueError(f"{plan_id}: clean voice loudness is not measurable")
    # One static gain keeps the per-segment balance already baked into the clean render.
    voice_gain = round(TARGET_LUFS - measured["integrated_lufs"], 2)
    music = pcm(music_path)
    clean_audio = pcm(clean)
    target = stage / f"{plan_id}.mp4"
    # The finished mix (after 1.2x, BGM and AAC) can sit a few tenths off the clean measurement, so
    # measure it and correct the voice gain once.
    for attempt in range(2):
        voice_gain = min(voice_gain, round(-3.0 - measured["true_peak_db"], 2))
        voice = clean_audio * 10 ** (voice_gain / 20)
        bgm_gain = auto_bgm_gain_db(voice, music)
        filters = (f"[1:a]volume={voice_gain}dB[v];[2:a]volume={bgm_gain}dB[m];"
                   "[v][m]amix=inputs=2:duration=first:normalize=0,"
                   f"asetpts=PTS-STARTPTS,atempo={FINAL_SPEED},apad[a]")
        run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", old, "-i", clean, "-stream_loop", "-1", "-i", music_path,
             "-filter_complex", filters, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{frames / 60:.6f}", "-movflags", "+faststart", target])
        new_loudness = audio_balance.measure(FFMPEG, target, "anull")
        offset = TARGET_LUFS - new_loudness["integrated_lufs"] if new_loudness["integrated_lufs"] is not None else 0.0
        if attempt or abs(offset) <= TOLERANCE_LU / 2:
            break
        voice_gain = round(voice_gain + offset, 2)

    spec = video_spec(target)
    gate = voice_over_music_db(voice, music, bgm_gain)
    old_audio, new_audio = pcm(old), pcm(target)
    old_envelope, new_envelope = envelope(old_audio), envelope(new_audio)
    length = min(len(old_envelope), len(new_envelope))
    correlation = float(np.corrcoef(old_envelope[:length], new_envelope[:length])[0, 1]) if length > 1 else float("nan")
    checks = {
        "video_stream_unchanged": video_packets_md5(target) == video_packets_md5(old),
        "specification": spec["frames"] == frames == row["output_spec"]["frames"] and packaged_streams_ok(spec)
                         and spec["audio_codec"] == "aac",
        "audio_length": abs(len(old_audio) - len(new_audio)) <= 16000 // 30,
        "loudness": new_loudness["integrated_lufs"] is not None
                    and abs(new_loudness["integrated_lufs"] - TARGET_LUFS) <= TOLERANCE_LU,
        "true_peak": new_loudness["true_peak_db"] is not None and new_loudness["true_peak_db"] <= -1.0,
        "voice_over_music": gate >= MIN_VOICE_OVER_MUSIC_DB,
        "same_speech_and_timing": math.isfinite(correlation) and correlation >= MIN_ENVELOPE_CORRELATION,
    }
    return {"plan_id": plan_id, "decision": "pass" if all(checks.values()) else "reject", "checks": checks,
            "previous": {"path": str(old), "sha256": sha(old), "packaged_sha256": row["output_sha256"],
                         "bgm_gain_db": row["bgm"]["gain_db"], "clean_lufs": measured["integrated_lufs"]},
            "voice_gain_db": voice_gain, "bgm_gain_db": bgm_gain, "voice_over_music_db": gate,
            "output_lufs": new_loudness["integrated_lufs"], "output_true_peak_db": new_loudness["true_peak_db"],
            "envelope_correlation_with_previous_audio": round(correlation, 5),
            "staged": {"path": str(target), "sha256": sha(target)}}


def remaster(delivery: Path) -> dict:
    delivery = delivery.resolve()
    job = delivery / "临时文件"
    manifest_path = job / "manifests" / "packaging_manifest.json"
    manifest = read(manifest_path)
    rows = manifest["results"]
    targets = [Path(row["output_path"]).resolve() for row in rows]
    if not rows or any(path.parent != (delivery / "成片").resolve() for path in targets):
        raise ValueError("packaging manifest does not describe this delivery's 成片 directory")
    report = job / "reports" / "audio_remaster.json"
    stage = job / "remaster"
    stage.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stage-", dir=stage) as directory:
        directory = Path(directory)
        with ThreadPoolExecutor(workers(len(rows))) as pool:
            results = list(pool.map(lambda row: remaster_one(row, directory), rows))
        failed = [row["plan_id"] for row in results if row["decision"] != "pass"]
        record = {"schema": "video-montage-audio-remaster/v1", "policy": POLICY, "target_lufs": TARGET_LUFS,
                  "tolerance_lu": TOLERANCE_LU, "packaging_manifest": {"path": str(manifest_path), "sha256": sha(manifest_path)},
                  "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                  "decision": "pass" if not failed else "reject", "failed": failed, "results": results}
        if failed:
            # Keep the last published report intact.
            write(report.with_name("audio_remaster_rejected.json"), record)
            raise RuntimeError(f"audio remaster rejected {failed}; nothing was published; see {report.with_name("audio_remaster_rejected.json")}")
        publish_files([(Path(row["staged"]["path"]), target) for row, target in zip(results, targets)], stage)
        for row, target in zip(results, targets):
            if sha(target) != row["staged"]["sha256"]:
                raise RuntimeError(f"published file differs from the verified one: {target}")
    write(report, record)
    return record
