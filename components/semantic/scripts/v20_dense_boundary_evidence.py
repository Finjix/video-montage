from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[3]
BIN = SUITE_ROOT / "dependencies" / "ffmpeg" / "bin"
FFMPEG = str(BIN / "ffmpeg.exe")
FFPROBE = str(BIN / "ffprobe.exe")
SAMPLE_RATE = 48000
WINDOW_SECONDS = 1.2
STABLE_RUN_REQUIRED_FRAMES = 60


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(source: Path) -> tuple[int, int, int]:
    run = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=avg_frame_rate,nb_read_frames,nb_frames", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(run.stdout)["streams"][0]
    num, den = map(int, stream["avg_frame_rate"].split("/"))
    total = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    if num <= 0 or den <= 0 or total <= 0:
        raise ValueError("source frame count/fps unavailable")
    return num, den, total


def frame_to_sample(frame: int, fps_num: int, fps_den: int) -> int:
    return round(Fraction(frame * fps_den * SAMPLE_RATE, fps_num))


def build_window(source: Path, boundary: int, side: str, root: Path, fps_num: int, fps_den: int, total_frames: int) -> dict:
    fps = fps_num / fps_den
    guard = max(STABLE_RUN_REQUIRED_FRAMES + 1, round(fps * WINDOW_SECONDS))
    start = max(0, boundary - guard)
    end = min(total_frames - 1, boundary + guard - 1)
    frame_dir = root / side / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    pattern = frame_dir / "%06d.png"
    vf = f"select='between(n\\,{start}\\,{end})'"
    subprocess.run([FFMPEG, "-v", "error", "-i", str(source), "-vf", vf, "-fps_mode", "passthrough", "-y", str(pattern)], check=True)
    files = sorted(frame_dir.glob("*.png"))
    expected = end - start + 1
    if len(files) != expected:
        raise RuntimeError(f"exact frame extraction mismatch {side}: {len(files)} != {expected}")
    center_sample = frame_to_sample(boundary, fps_num, fps_den)
    pcm_start = max(0, center_sample - SAMPLE_RATE // 2)
    pcm_end = center_sample + SAMPLE_RATE // 2
    pcm = root / side / "window.wav"
    af = f"aresample={SAMPLE_RATE},atrim=start_sample={pcm_start}:end_sample={pcm_end},asetpts=PTS-STARTPTS"
    subprocess.run([FFMPEG, "-v", "error", "-i", str(source), "-map", "0:a:0", "-af", af, "-ac", "1", "-c:a", "pcm_s16le", "-y", str(pcm)], check=True)
    return {
        "window_seconds_each_side": WINDOW_SECONDS,
        "stable_run_required_frames": STABLE_RUN_REQUIRED_FRAMES,
        "native_fps_num": fps_num,
        "native_fps_den": fps_den,
        "boundary_frame": boundary,
        "boundary_index": boundary - start,
        "window_start_frame": start,
        "window_end_frame_inclusive": end,
        "expected_frame_count": expected,
        "actual_frame_count": len(files),
        "frames": [
            {"source_frame_number": start + index, "path": str(path), "sha256": sha(path)}
            for index, path in enumerate(files)
        ],
        "pcm_sample_rate": SAMPLE_RATE,
        "boundary_sample": center_sample,
        "pcm_start_sample": pcm_start,
        "pcm_end_sample_exclusive": pcm_end,
        "pcm_path": str(pcm),
        "pcm_sha256": sha(pcm),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    candidate = read(args.candidate)
    required = ("candidate_id", "source_path", "source_sha256", "source_content_fingerprint", "source_in_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den")
    missing = [name for name in required if candidate.get(name) in (None, "")]
    if missing:
        raise ValueError(f"frame-native candidate fields missing: {missing}")
    source = Path(candidate["source_path"]).resolve()
    if sha(source) != str(candidate["source_sha256"]).lower():
        raise ValueError("source sha mismatch")
    fps_num, fps_den, total_frames = probe(source)
    if (fps_num, fps_den) != (int(candidate["source_fps_num"]), int(candidate["source_fps_den"])):
        raise ValueError("candidate/source fps mismatch")
    source_in = int(candidate["source_in_frame"])
    source_out = int(candidate["source_out_frame_exclusive"])
    if not 0 <= source_in < source_out <= total_frames:
        raise ValueError("invalid frame range")
    cid = str(candidate["candidate_id"])
    root = (args.output_dir / cid).resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": "candidate-boundary-evidence/v260928",
        "task_id": args.task_id,
        "candidate_id": cid,
        "source_path": str(source),
        "source_sha256": sha(source),
        "source_content_fingerprint": candidate["source_content_fingerprint"],
        "source_in_frame": source_in,
        "source_out_frame_exclusive": source_out,
        "source_fps_num": fps_num,
        "source_fps_den": fps_den,
        "generator": {"id": "video-montage-dense-boundary-evidence/v260928", "authority": "evidence_only"},
        "generator_decision": "pending_review",
        "decision": "pending_review",
        "reviewer": None,
        "dense_windows": {
            "in": build_window(source, source_in, "in", root, fps_num, fps_den, total_frames),
            "out": build_window(source, source_out, "out", root, fps_num, fps_den, total_frames),
        },
        "audio_alignment": {"decision": "pending_review"},
    }
    output = root / "bundle.json"
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": "pending_review", "bundle": str(output), "sha256": sha(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
