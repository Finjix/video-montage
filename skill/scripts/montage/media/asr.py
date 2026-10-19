"""Bundled Qwen3-ASR-1.7B speech recognition with Qwen3-ForcedAligner-0.6B word timing (GPU when CUDA works)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from montage.common import FFMPEG

ASR_MODEL = "Qwen3-ASR-1.7B"
ALIGNER_MODEL = "Qwen3-ForcedAligner-0.6B"
SAMPLE_RATE = 16000
STRONG_PUNCTUATION = "。！？!?…"
WEAK_PUNCTUATION = "，、；：,;:"
PAUSE_SPLIT_SECONDS = 0.8     # a silence this long between two words always ends the segment
MAX_SEGMENT_SECONDS = 30.0    # longer sentences are cut at their next comma
MIN_WORD_SECONDS = 0.04


def cuda_runtime_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def build_model(model_root: Path, device: str = "auto", max_batch: int = 8):
    """Return the ASR model with its aligner; "auto" falls back to CPU when CUDA is unusable."""
    import torch
    from qwen_asr import Qwen3ASRModel

    def load(target: str):
        dtype = torch.bfloat16 if target.startswith("cuda") else torch.float32
        kwargs = {"dtype": dtype, "device_map": target}
        return Qwen3ASRModel.from_pretrained(
            str(model_root / ASR_MODEL), forced_aligner=str(model_root / ALIGNER_MODEL),
            forced_aligner_kwargs=dict(kwargs), max_inference_batch_size=max_batch, max_new_tokens=1024, **kwargs)
    if device == "cpu" or (device == "auto" and not cuda_runtime_available()):
        return load("cpu")
    if device == "cuda":
        return load("cuda:0")
    try:
        return load("cuda:0")
    except Exception:
        torch.cuda.empty_cache()
        return load("cpu")


def decode(source_path: str) -> np.ndarray:
    """Mono 16 kHz float32 samples of the first audio stream."""
    result = subprocess.run([str(FFMPEG), "-v", "error", "-nostdin", "-i", source_path, "-map", "0:a:0", "-vn",
                             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"], capture_output=True)
    if result.returncode:
        raise RuntimeError(f"cannot decode audio: {result.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def attach_punctuation(text: str, items) -> list[dict]:
    """Aligned words keep the punctuation that follows them and the spaces that precede them; subtitle breaking reads both."""
    words, cursor, lead = [], 0, ""
    for item in items:
        token = item.text
        found = text.find(token, cursor)
        if found < 0:
            raise ValueError(f"aligned token {token!r} is not in the recognized text")
        skipped = text[cursor:found]
        kept = skipped.rstrip()
        if words:
            words[-1]["word"] += kept
        else:
            lead = kept
        lead += skipped[len(kept):]
        start, end = float(item.start_time), float(item.end_time)
        words.append({"start": start, "end": max(start, end), "word": lead + token})
        lead, cursor = "", found + len(token)
    if words:
        words[-1]["word"] += text[cursor:]
    return words


def regularize(words: list[dict], duration: float) -> list[dict]:
    """The aligner can emit zero-length or overlapping words; give every word a positive, ordered span.

    A too-short word first borrows time from the silence before it, then extends forward.
    """
    cursor = 0.0
    for word in words:
        start = min(max(word["start"], cursor), duration - MIN_WORD_SECONDS)
        end = min(max(word["end"], start), duration)
        if end - start < MIN_WORD_SECONDS:
            start -= min(MIN_WORD_SECONDS - (end - start), start - cursor)
            end = min(max(end, start + MIN_WORD_SECONDS), duration)
        word["start"], word["end"] = start, end
        cursor = end
    return words


def build_segments(words: list[dict]) -> list[dict]:
    """Sentence-level segments: end at strong punctuation or a long pause; over-long ones end at a comma."""
    segments, current = [], []

    def close():
        if current:
            segments.append({"start": round(current[0]["start"], 3), "end": round(current[-1]["end"], 3),
                             "text": "".join(w["word"] for w in current),
                             "words": [{"start": round(w["start"], 3), "end": round(w["end"], 3), "word": w["word"]}
                                       for w in current]})
            current.clear()
    for index, word in enumerate(words):
        current.append(word)
        tail = word["word"].rstrip()
        following = words[index + 1] if index + 1 < len(words) else None
        if (following is None or tail.endswith(tuple(STRONG_PUNCTUATION))
                or following["start"] - word["end"] >= PAUSE_SPLIT_SECONDS
                or (tail.endswith(tuple(WEAK_PUNCTUATION)) and word["end"] - current[0]["start"] >= MAX_SEGMENT_SECONDS)):
            close()
    return segments


def transcribe(model, source_path: str, language: str = "zh", context: str = "") -> dict:
    """One neutral recognition of the whole audio; words and segments carry source-time seconds."""
    if language != "zh":
        raise ValueError("only Chinese speech is supported")
    samples = decode(source_path)
    if not len(samples):
        return {"language": language, "engine": ASR_MODEL, "text": "", "segments": []}
    result = model.transcribe((samples, SAMPLE_RATE), context=context, language="Chinese", return_time_stamps=True)[0]
    text = result.text.strip()
    words = attach_punctuation(text, result.time_stamps.items) if text and result.time_stamps is not None else []
    segments = build_segments(regularize(words, len(samples) / SAMPLE_RATE))
    return {"language": language, "engine": ASR_MODEL, "text": " ".join(row["text"] for row in segments),
            "segments": segments}
