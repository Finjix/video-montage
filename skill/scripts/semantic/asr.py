"""Bundled faster-whisper speech recognition (GPU when the bundled CUDA runtime loads)."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_CUDA_DLL_DIRECTORY_HANDLES = []


def cuda_runtime_available() -> bool:
    if sys.platform != "win32":
        return True
    bundled_cuda_bin = Path(__file__).resolve().parents[2] / "assets" / "dependencies" / "cuda" / "bin"
    if bundled_cuda_bin.is_dir():
        cuda_path = str(bundled_cuda_bin)
        if not any(entry.casefold() == cuda_path.casefold() for entry in os.environ.get("PATH", "").split(os.pathsep)):
            os.environ["PATH"] = cuda_path + os.pathsep + os.environ.get("PATH", "")
        try:
            _CUDA_DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(cuda_path))
        except (AttributeError, OSError):
            pass
    try:
        ctypes.WinDLL("cublas64_12.dll")
    except OSError:
        return False
    return True


def build_model(model_name: str, model_root: Path, device: str, cpu_compute: str, gpu_compute: str):
    """Return (model, device, compute type); "auto" falls back to CPU when CUDA is unusable."""
    from faster_whisper import WhisperModel

    def load(target: str, compute: str):
        return WhisperModel(model_name, device=target, compute_type=compute, download_root=str(model_root),
                            local_files_only=True), target, compute
    if device == "cpu" or (device == "auto" and not cuda_runtime_available()):
        return load("cpu", cpu_compute)
    if device == "cuda":
        return load("cuda", gpu_compute)
    try:
        return load("cuda", gpu_compute)
    except Exception:
        return load("cpu", cpu_compute)


def transcribe(model, source_path: str, language: str, initial_prompt: str | None = None) -> tuple[dict, object]:
    segments, info = model.transcribe(source_path, language=language, beam_size=5, temperature=0.0, word_timestamps=True,
                                      condition_on_previous_text=False, vad_filter=True, initial_prompt=initial_prompt)
    rows = [{"start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text,
             "words": [{"start": round(word.start, 3), "end": round(word.end, 3), "word": word.word}
                       for word in (segment.words or [])]} for segment in segments]
    return {"language": info.language, "language_probability": info.language_probability,
            "text": " ".join(row["text"] for row in rows), "segments": rows}, info
