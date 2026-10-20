"""Qwen3-ASR-1.7B recognition and Qwen3-ForcedAligner-0.6B word timing (GPU when CUDA works)."""
from __future__ import annotations

import numpy as np

from montage.common import MODEL_ROOT, quiet_ml

SAMPLE_RATE = 16000
MIN_WORD_SECONDS = 0.04


def batch_size(target: str) -> int:
    """Largest batch that stays inside GPU memory (spilling into shared memory makes Windows GPUs crawl)."""
    if not target.startswith("cuda"):
        return 4
    import torch
    gigabytes = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    return 8 if gigabytes >= 12 else 4 if gigabytes >= 7.5 else 2


def build():
    """Load the recognizer with its aligner; falls back to CPU when CUDA is unusable."""
    quiet_ml()
    import torch
    from transformers.utils import logging as hf_logging
    from qwen_asr import Qwen3ASRModel
    hf_logging.set_verbosity_error()

    def load(target: str):
        dtype = torch.bfloat16 if target.startswith("cuda") else torch.float32
        kwargs = {"dtype": dtype, "device_map": target}
        model = Qwen3ASRModel.from_pretrained(
            str(MODEL_ROOT / "Qwen3-ASR-1.7B"), forced_aligner=str(MODEL_ROOT / "Qwen3-ForcedAligner-0.6B"),
            forced_aligner_kwargs=dict(kwargs), max_inference_batch_size=batch_size(target), max_new_tokens=1024, **kwargs)
        generation = getattr(getattr(model, "model", None), "generation_config", None)
        if generation is not None and generation.pad_token_id is None:
            generation.pad_token_id = generation.eos_token_id
        return model
    try:
        if torch.cuda.is_available():
            return load("cuda:0")
    except Exception:
        torch.cuda.empty_cache()
    return load("cpu")


def attach_punctuation(text: str, items) -> list[dict]:
    """Aligned tokens keep the punctuation that follows them (subtitles and sentence splits read it)."""
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
        words.append({"word": lead + token, "start": start, "end": max(start, end)})
        lead, cursor = "", found + len(token)
    if words:
        words[-1]["word"] += text[cursor:]
    return words


def regularize(words: list[dict], duration: float, origin: float = 0.0) -> list[dict]:
    """Give every word a positive, ordered span; the aligner can emit zero-length or overlapping words."""
    cursor = origin
    for word in words:
        start = min(max(word["start"], cursor), origin + duration - MIN_WORD_SECONDS)
        end = min(max(word["end"], start), origin + duration)
        if end - start < MIN_WORD_SECONDS:
            start -= min(MIN_WORD_SECONDS - (end - start), start - cursor)
            end = min(max(end, start + MIN_WORD_SECONDS), origin + duration)
        word["start"], word["end"] = round(start, 3), round(end, 3)
        cursor = end
    return words


def recognize(model, audios: list[np.ndarray], context: str = "") -> list[list[dict]]:
    """One batched recognition; returns word lists (with punctuation) in source seconds."""
    present = [i for i, audio in enumerate(audios) if len(audio)]
    results: list[list[dict]] = [[] for _ in audios]
    if not present:
        return results
    output = model.transcribe([(audios[i], SAMPLE_RATE) for i in present], context=context,
                              language="Chinese", return_time_stamps=True)
    for i, row in zip(present, output):
        text = row.text.strip()
        if text and row.time_stamps is not None:
            results[i] = regularize(attach_punctuation(text, row.time_stamps.items), len(audios[i]) / SAMPLE_RATE)
    return results


def align(model, audio: np.ndarray, text: str, origin: float) -> list[dict]:
    """Word times of a corrected transcript inside one audio slice that starts at `origin` seconds."""
    if not text.strip():
        return []
    result = model.forced_aligner.align(audio=[(audio, SAMPLE_RATE)], text=[text], language=["Chinese"])[0]
    words = attach_punctuation(text, result.items)
    for word in words:
        word["start"] += origin
        word["end"] += origin
    return regularize(words, len(audio) / SAMPLE_RATE, origin)
