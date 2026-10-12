"""Scan/review a retained real segment and make non-publishing cleanup previews."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sample_cleanup_auto", ROOT / "scripts/autonomous/scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)


def packet_hash(path: Path) -> str:
    return auto.run([str(auto.FFMPEG), "-v", "error", "-i", str(path), "-map", "0:v:0",
                     "-c", "copy", "-f", "hash", "-hash", "sha256", "pipe:1"]).strip()


def check(job: Path, plan_id: str, segment_index: int, review_path: Path | None) -> dict:
    if not job.is_dir() or job.name != "临时文件":
        raise ValueError("existing batch temporary directory required")
    plan_path = job / "manifests/reburn_plan.json"
    plan = auto.read(plan_path)
    output = next(r for r in plan["outputs"] if r["plan_id"] == plan_id)
    if not 1 <= segment_index <= len(output["segments"]):
        raise ValueError("segment-index outside selected plan")
    segment = output["segments"][segment_index - 1]
    directory = job / "evidence" / f"audio_cleanup_{plan_id}"
    source = Path(segment["source_path"])
    if auto.sha(source) != segment["source_sha256"]:
        raise ValueError("original source changed")
    asr = None
    for path in (job / "manifests/source_asr").glob("*.json"):
        value = auto.read(path)
        if value.get("source", {}).get("sha256") == segment["source_sha256"]:
            asr = value["asr"]
            break
    if asr is None:
        raise ValueError("retained original source ASR required")
    evidence = auto.AUDIO.analyze(auto.FFMPEG, source, segment, asr, directory)
    scan_path = directory / "scan.json"
    auto.write(scan_path, evidence)
    if review_path is None:
        return {"decision": "pending_review", "scan": str(scan_path), "preview": evidence["waveform_preview"]}
    cleanup = auto.AUDIO.compile_review(evidence, auto.read(review_path), segment)
    clip_plan = directory / "clip_plan.json"
    auto.write(clip_plan, {"segments": [{**segment, "audio_cleanup_policy": auto.AUDIO.POLICY, "audio_cleanup": cleanup}]})
    clip = directory / "cleaned_clip.mp4"
    renderer = auto.module("sample_cleanup_renderer", "scripts/semantic/scripts/portable_frame_renderer.py")
    rendered_clip = renderer.render(clip_plan, clip, directory / "clip_render.json", 1440, 2560, 60, "libx264", overwrite=True)
    clip_proof = auto.AUDIO.verify_output(auto.AUDIO.decode(auto.FFMPEG, clip), rendered_clip)
    if clip_proof["decision"] != "pass":
        raise ValueError(f"cleaned real clip failed: {clip_proof}")
    # Preview copies retain every original video packet, including burned subtitles.
    original_clean = job.parent / "混剪（无包装）" / f"{plan_id}.mp4"
    original_final = job.parent / "成片" / f"{plan_id}.mp4"
    originals = {str(p): auto.sha(p) for p in (original_clean, original_final)}
    rendered = auto.read(job / "manifests" / f"render-{plan_id}.json")
    before_frames = sum(s["expected_output_frames"] for s in rendered["segments"][:segment_index - 1])
    after_frames = before_frames + rendered_clip["actual_output_frames"]
    cleaned = directory / "cleaned_full.mp4"
    filters = (f"[0:a]asplit=2[l][r];[l]atrim=end={before_frames / 60},asetpts=PTS-STARTPTS[before];"
               f"[1:a]atrim=duration={rendered_clip['actual_output_frames'] / 60},asetpts=PTS-STARTPTS[clip];"
               f"[r]atrim=start={after_frames / 60},asetpts=PTS-STARTPTS[after];"
               "[before][clip][after]concat=n=3:v=0:a=1[a]")
    auto.run([str(auto.FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(original_clean), "-i", str(clip),
              "-filter_complex", filters, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
              "-b:a", "192k", "-ar", "48000", "-t", str(rendered["actual_output_frames"] / 60), str(cleaned)])
    rendered = copy.deepcopy(rendered)
    rendered["segments"][segment_index - 1]["audio_cleanup"] = cleanup
    clean_proof = auto.AUDIO.verify_output(auto.AUDIO.decode(auto.FFMPEG, cleaned), rendered)
    manifest = auto.read(job / "manifests/packaging_manifest.json")
    row = copy.deepcopy(next(r for r in manifest["results"] if r["plan_id"] == plan_id))
    row["input"] = auto.ref(cleaned)
    preview = directory / "test-28-cleaned-preview.mp4" if plan_id == "test-28" else directory / f"{plan_id}-cleaned-preview.mp4"
    command = [str(auto.FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(original_final), "-i", str(cleaned)]
    if row.get("bgm"):
        bgm = auto.require_ref(row["bgm"], "sample BGM")
        command += ["-stream_loop", "-1", "-i", str(bgm)]
        filters = f"[2:a]volume={row['bgm']['gain_db']}dB[m];[1:a][m]amix=inputs=2:duration=first:normalize=0,"
    else:
        filters = "[1:a]"
    filters += f"asetpts=PTS-STARTPTS,atempo={row['final_speed']},apad[a]"
    command += ["-filter_complex", filters, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                "-b:a", "192k", "-ar", "48000", "-t", str(row["output_frames"] / 60), str(preview)]
    auto.run(command)
    row["output_path"] = str(preview)
    row["output_sha256"] = auto.sha(preview)
    mix = auto.delivery_mix_evidence(auto.pcm(preview), row, rendered)
    model = auto.load_model()
    before_asr = auto.transcribe(model, original_clean)
    clip_asr = auto.transcribe(model, clip)
    final_asr = auto.transcribe(model, preview)
    expected = auto.normalized(segment["text"])
    speech_preserved = auto.normalized(clip_asr["text"]) == expected
    packet_equal = packet_hash(original_clean) == packet_hash(cleaned) and packet_hash(original_final) == packet_hash(preview)
    frames_equal = auto.video(cleaned)["frames"] == rendered["actual_output_frames"] and auto.video(preview)["frames"] == row["output_frames"]
    untouched = all(auto.sha(Path(p)) == identity for p, identity in originals.items())
    before_audio, after_audio = auto.AUDIO.decode(auto.FFMPEG, original_clean), auto.AUDIO.decode(auto.FFMPEG, cleaned)
    changes = []
    for region in auto.AUDIO.output_ranges(rendered):
        a, b = region["output_start_sample"] + 1024, region["output_end_sample"] - 1024
        db = lambda x: float(20 * np.log10(max(np.sqrt(np.max(np.mean(x * x, axis=0))), 1e-9)))
        changes.append({"before_dbfs": round(db(before_audio[a:b]), 2), "after_dbfs": round(db(after_audio[a:b]), 2)})
    result = {"policy": auto.AUDIO.POLICY, "plan_id": plan_id, "segment_index": segment_index,
              "clip_proof": clip_proof, "clean_proof": clean_proof, "final_mix_proof": mix,
              "speech_preserved": speech_preserved, "cleaned_clip_asr": clip_asr,
              "original_full_asr": before_asr, "final_asr": final_asr, "video_packets_identical": packet_equal,
              "frames_identical": frames_equal, "original_deliveries_unchanged": untouched,
              "attenuation": changes, "preview": auto.ref(preview), "clean_preview": auto.ref(cleaned)}
    result["decision"] = "pass" if all((clip_proof["decision"] == "pass", clean_proof["decision"] == "pass",
        mix["decision"] == "pass", speech_preserved, packet_equal, frames_equal, untouched)) else "reject"
    report = job / "reports" / f"audio_cleanup_{plan_id}.json"
    auto.write(report, result)
    return {"decision": result["decision"], "report": str(report), "preview": str(preview), "attenuation": changes,
            "speech_preserved": speech_preserved, "original_deliveries_unchanged": untouched}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--segment-index", type=int, required=True)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    result = check(args.job_dir.resolve(), args.plan_id, args.segment_index, args.review)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(2 if result["decision"] == "reject" else 0)
