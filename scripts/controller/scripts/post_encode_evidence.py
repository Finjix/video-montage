from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

BIN = Path(__file__).resolve().parents[3] / "assets" / "dependencies" / "ffmpeg" / "bin"
FFMPEG = str(BIN / "ffmpeg.exe")
SAMPLE_RATE = 48000
OUTPUT_FPS = 60
FRAME_GUARD = 72
PCM_GUARD = SAMPLE_RATE // 2


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_set_sha(frames: list[dict]) -> str:
    payload = json.dumps([{"output_frame_number": item["output_frame_number"], "sha256": item["sha256"]} for item in frames], separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def verify_boundary_files(cut: dict) -> list[str]:
    failures = []
    frames = cut.get("frames")
    if not isinstance(frames, list) or not frames:
        return ["FRAME_SET_INVALID"]
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("output_frame_number"), int) or not isinstance(frame.get("sha256"), str):
            failures.append("FRAME_SET_INVALID")
            continue
        path = Path(str(frame.get("path") or ""))
        if not path.is_file() or sha(path) != str(frame.get("sha256") or "").lower():
            failures.append("FRAME_HASH_MISMATCH")
    if "FRAME_SET_INVALID" not in failures and frame_set_sha(frames) != cut.get("frame_set_sha256"):
        failures.append("FRAME_SET_HASH_MISMATCH")
    pcm = Path(str(cut.get("pcm_path") or ""))
    if not pcm.is_file() or sha(pcm) != str(cut.get("pcm_sha256") or "").lower():
        failures.append("PCM_HASH_MISMATCH")
    return sorted(set(failures))


def validate_plan_scope(delivery: dict, index: dict) -> list[str]:
    delivery_ids = [str(item.get("plan_id") or "") for item in delivery.get("results", [])]
    index_ids = [str(item.get("plan_id") or "") for item in index.get("plans", [])]
    failures = []
    if delivery.get("output_count") != len(delivery_ids) or len(set(delivery_ids)) != len(delivery_ids) or any(not item for item in delivery_ids):
        failures.append("DELIVERY_SCOPE_INVALID")
    if len(set(index_ids)) != len(index_ids) or any(not item for item in index_ids):
        failures.append("LOCKED_INDEX_SCOPE_INVALID")
    if set(delivery_ids) != set(index_ids):
        failures.append("PARTIAL_BATCH_OR_DEPENDENCY_SCOPE")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", plan_id) for plan_id in delivery_ids):
        failures.append("UNSAFE_PLAN_ID")
    return failures


def extract_boundary(video: Path, pid: str, cut_index: int, kind: str, boundary: int, total_frames: int, output_dir: Path) -> dict:
    if kind == "output_start":
        start, end = 0, min(total_frames - 1, FRAME_GUARD * 2 - 1)
    elif kind == "output_end":
        start, end = max(0, total_frames - FRAME_GUARD * 2), total_frames - 1
    else:
        start, end = max(0, boundary - FRAME_GUARD), min(total_frames - 1, boundary + FRAME_GUARD - 1)
    root = output_dir / pid / f"boundary_{cut_index:02d}_{kind}"
    frames_dir = root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "%06d.jpg"
    vf = f"select='between(n\\,{start}\\,{end})'"
    subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vf", vf, "-fps_mode", "passthrough", "-q:v", "3", "-y", str(pattern)], check=True)
    files = sorted(frames_dir.glob("*.jpg"))
    expected = end - start + 1
    if len(files) != expected:
        raise RuntimeError(f"post frame extraction mismatch {pid}/{kind}/{cut_index}: {len(files)} != {expected}")
    frame_rows = [{"output_frame_number": start + position, "path": str(path), "sha256": sha(path)} for position, path in enumerate(files)]
    total_samples = total_frames * (SAMPLE_RATE // OUTPUT_FPS)
    center_sample = boundary * (SAMPLE_RATE // OUTPUT_FPS)
    if kind == "output_start":
        pcm_start, pcm_end = 0, min(total_samples, PCM_GUARD * 2)
    elif kind == "output_end":
        pcm_start, pcm_end = max(0, total_samples - PCM_GUARD * 2), total_samples
    else:
        pcm_start, pcm_end = max(0, center_sample - PCM_GUARD), min(total_samples, center_sample + PCM_GUARD)
    pcm = root / "window.wav"
    af = f"aresample={SAMPLE_RATE},atrim=start_sample={pcm_start}:end_sample={pcm_end},asetpts=PTS-STARTPTS"
    subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-map", "0:a:0", "-af", af, "-ac", "1", "-c:a", "pcm_s16le", "-y", str(pcm)], check=True)
    return {
        "plan_id": pid, "cut_index": cut_index, "boundary_kind": kind,
        "boundary_frame_60fps": boundary, "cut_frame_60fps": boundary,
        "cut_time_seconds": boundary / OUTPUT_FPS,
        "cut_sample_48k": center_sample, "pcm_start_sample_48k": pcm_start,
        "cut_sample_index_in_pcm": center_sample - pcm_start,
        "frames_before_boundary": boundary - start, "frames_before_cut": boundary - start,
        "frame_start": start, "frame_end_inclusive": end,
        "expected_frame_count": expected, "actual_frame_count": len(files),
        "stable_run_required_frames": 30,
        "frames": frame_rows, "frame_set_sha256": frame_set_sha(frame_rows),
        "pcm_path": str(pcm), "pcm_sha256": sha(pcm),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-manifest", type=Path, required=True)
    parser.add_argument("--locked-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    delivery, index = read(args.delivery_manifest), read(args.locked_index)
    scope_failures = validate_plan_scope(delivery, index)
    if scope_failures:
        raise RuntimeError(";".join(scope_failures))
    by_plan = {item["plan_id"]: item for item in index["plans"]}
    rows = []
    for item in delivery["results"]:
        pid = item["plan_id"]
        plan_item = by_plan[pid]
        plan_path = Path(plan_item["path"])
        if sha(plan_path) != str(plan_item.get("sha256", "")).lower():
            raise RuntimeError(f"locked plan hash mismatch: {pid}")
        render_path = Path(str(item.get("render_evidence_path", "")))
        if not render_path.is_file() or sha(render_path) != str(item.get("render_evidence_sha256", "")).lower():
            raise RuntimeError(f"render evidence required: {pid}")
        render = read(render_path)
        if render.get("render_mode") != "source_frame_ranges/v1" or render.get("plan_sha256") != sha(plan_path):
            raise RuntimeError(f"render evidence binding mismatch: {pid}")
        video = Path(item["output_path"])
        segments = render.get("segments", [])
        total_frames = int(render.get("actual_output_frames", 0) or 0)
        if not segments or total_frames <= 0 or total_frames != sum(int(segment["expected_output_frames"]) for segment in segments):
            raise RuntimeError(f"integer render-frame evidence required: {pid}")
        speed = item.get("final_speed", 1.0)
        if speed not in (1.0, 1.2):
            raise RuntimeError(f"invalid final speed: {pid}")
        total_frames = (total_frames * 5 + 5) // 6 if speed == 1.2 else total_frames
        start_row = extract_boundary(video, pid, 0, "output_start", 0, total_frames, args.output_dir)
        start_row["right_source_candidate_ids"] = segments[0].get("source_candidate_ids", [])
        rows.append(start_row)
        cumulative = 0
        for cut_index, segment in enumerate(segments[:-1], 1):
            cumulative += int(segment["expected_output_frames"])
            row = extract_boundary(video, pid, cut_index, "concat_cut", math.ceil(cumulative / speed), total_frames, args.output_dir)
            row["left_source_candidate_ids"] = segment.get("source_candidate_ids", [])
            row["right_source_candidate_ids"] = segments[cut_index].get("source_candidate_ids", [])
            rows.append(row)
        end_row = extract_boundary(video, pid, len(segments), "output_end", total_frames, total_frames, args.output_dir)
        end_row["left_source_candidate_ids"] = segments[-1].get("source_candidate_ids", [])
        rows.append(end_row)
    report = {
        "schema": "ffmpeg-post-encode-evidence/v260928", "decision": "pending_independent_review",
        "delivery_manifest_path": str(args.delivery_manifest.resolve()), "delivery_manifest_sha256": sha(args.delivery_manifest),
        "locked_index_path": str(args.locked_index.resolve()), "locked_index_sha256": sha(args.locked_index),
        "stable_run_required_frames": 30, "complete_plan_scope": True,
        "boundary_count": len(rows), "cut_count": len(rows), "cuts": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "boundaries": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
