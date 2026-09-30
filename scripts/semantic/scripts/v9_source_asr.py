#!/usr/bin/env python3
"""V9-compatible chunked faster-whisper ASR reused by V10 continuous orchestration."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".partial")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temp.replace(path)


def valid_existing(path: Path, source: dict) -> bool:
    if not path.is_file():
        return False
    try:
        value = load_json(path)
    except Exception:
        return False
    return value.get("schema") == "semantic-source-asr/v260928" and value.get("source_id") == source.get("source_id") and value.get("source_sha256", "").lower() == source.get("source_sha256", "").lower() and bool(value.get("asr", {}).get("segments"))


_CUDA_DLL_DIRECTORY_HANDLES = []


def cuda_runtime_available() -> bool:
    if sys.platform != "win32":
        return True
    suite_root = Path(__file__).resolve().parents[3]
    bundled_cuda_bin = suite_root / "assets" / "dependencies" / "cuda" / "bin"
    if bundled_cuda_bin.is_dir():
        cuda_path = str(bundled_cuda_bin)
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if not any(entry.casefold() == cuda_path.casefold() for entry in path_entries):
            os.environ["PATH"] = cuda_path + os.pathsep + os.environ.get("PATH", "")
        try:
            _CUDA_DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(cuda_path))
        except (AttributeError, OSError):
            pass
    for library in ("cublas64_12.dll",):
        try:
            ctypes.WinDLL(library)
        except OSError:
            return False
    return True


def build_model(model_name: str, model_root: Path, device: str, cpu_compute: str, gpu_compute: str):
    from faster_whisper import WhisperModel
    if device == "cpu":
        return WhisperModel(model_name, device="cpu", compute_type=cpu_compute, download_root=str(model_root), local_files_only=True), "cpu", cpu_compute
    if device == "cuda":
        return WhisperModel(model_name, device="cuda", compute_type=gpu_compute, download_root=str(model_root), local_files_only=True), "cuda", gpu_compute
    if not cuda_runtime_available():
        return WhisperModel(model_name, device="cpu", compute_type=cpu_compute, download_root=str(model_root), local_files_only=True), "cpu", cpu_compute
    try:
        return WhisperModel(model_name, device="cuda", compute_type=gpu_compute, download_root=str(model_root), local_files_only=True), "cuda", gpu_compute
    except Exception:
        return WhisperModel(model_name, device="cpu", compute_type=cpu_compute, download_root=str(model_root), local_files_only=True), "cpu", cpu_compute


def transcribe(model, source_path: str, language: str) -> tuple[dict, object]:
    segments, info = model.transcribe(source_path, language=language, beam_size=5, temperature=0.0, word_timestamps=True, condition_on_previous_text=False, vad_filter=True)
    rows = []
    for segment in segments:
        rows.append({"start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text, "words": [{"start": round(word.start, 3), "end": round(word.end, 3), "word": word.word} for word in (segment.words or [])]})
    return {"language": info.language, "language_probability": info.language_probability, "text": " ".join(row["text"] for row in rows), "segments": rows}, info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--module-path", action="append", default=[])
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--cpu-compute", default="int8")
    parser.add_argument("--gpu-compute", default="float16")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--max-items-per-run", type=int, default=8)
    args = parser.parse_args(argv)
    for path in reversed(args.module_path):
        sys.path.insert(0, str(Path(path).resolve()))
    manifest = load_json(args.manifest)
    sources = manifest.get("sources", [])
    if not isinstance(sources, list) or not sources or len({row.get("source_sha256") for row in sources}) != len(sources):
        raise ValueError("manifest requires non-empty unique source hashes")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results, pending = [], []
    for position, source in enumerate(sources, 1):
        destination = args.output_dir / f"{source['source_id']}.json"
        if valid_existing(destination, source):
            results.append({"position": position, "source_id": source["source_id"], "status": "reused_completed", "path": str(destination.resolve()), "sha256": sha_file(destination)})
        else:
            pending.append((position, source, destination))
    model = None
    active_device = None
    compute_type = None
    pending = pending[:max(1, args.max_items_per_run)]
    if pending:
        model, active_device, compute_type = build_model(args.model, args.model_root, args.device, args.cpu_compute, args.gpu_compute)
    completed = len(results)
    failures = 0
    atomic_json(args.progress, {"schema": "semantic-source-asr-progress/v260928", "total": len(sources), "completed": completed, "remaining": len(sources) - completed, "failed": failures, "device": active_device, "updated_at": now_iso()})
    for position, source, destination in pending:
        error = None
        try:
            asr, _ = transcribe(model, source["source_path"], args.language)
        except Exception as exc:
            error = exc
            text = f"{type(exc).__name__}: {exc}".lower()
            if active_device == "cuda" and any(token in text for token in ("out of memory", "memoryerror", "cuda")):
                model, active_device, compute_type = build_model(args.model, args.model_root, "cpu", args.cpu_compute, args.gpu_compute)
                try:
                    asr, _ = transcribe(model, source["source_path"], args.language)
                    error = None
                except Exception as cpu_exc:
                    error = cpu_exc
        if error is None:
            record = {"schema": "semantic-source-asr/v260928", "source_id": source["source_id"], "source_path": source["source_path"], "source_sha256": source["source_sha256"], "processing_stage": source["processing_stage"], "asr_model": args.model, "device": active_device, "compute_type": compute_type, "created_at": now_iso(), "asr": asr}
            atomic_json(destination, record)
            row = {"position": position, "source_id": source["source_id"], "status": "completed", "path": str(destination.resolve()), "sha256": sha_file(destination)}
            completed += 1
        else:
            row = {"position": position, "source_id": source["source_id"], "status": "failed", "error": f"{type(error).__name__}: {error}"}
            failures += 1
        results.append(row)
        print(json.dumps({**row, "total": len(sources)}, ensure_ascii=False), flush=True)
        atomic_json(args.progress, {"schema": "semantic-source-asr-progress/v260928", "total": len(sources), "completed": completed, "remaining": len(sources) - completed, "failed": failures, "device": active_device, "last_source_id": source["source_id"], "updated_at": now_iso()})
    if failures:
        return 2
    if completed < len(sources):
        return 75
    order = {source["source_id"]: index for index, source in enumerate(sources)}
    results.sort(key=lambda row: order[row["source_id"]])
    index = {"schema": "semantic-source-asr-index/v260928", "batch_id": manifest.get("batch_id"), "source_manifest_path": str(args.manifest.resolve()), "source_manifest_sha256": sha_file(args.manifest), "completed_at": now_iso(), "decision": "pass", "results": results}
    atomic_json(args.index, index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
