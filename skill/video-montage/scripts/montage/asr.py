"""Qwen3-ASR-1.7B recognition and Qwen3-ForcedAligner-0.6B word timing (GPU when CUDA works)."""
from __future__ import annotations

import unicodedata

import numpy as np

from montage.common import quiet_ml
from montage import runtime

SAMPLE_RATE = 16000
MIN_WORD_SECONDS = 0.04


def batch_size(target: str) -> int:
    """Largest batch that stays inside GPU memory (spilling into shared memory makes Windows GPUs crawl)."""
    if not target.startswith("cuda"):
        return 4
    import torch
    gigabytes = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    return 8 if gigabytes >= 12 else 4 if gigabytes >= 7.5 else 2


def _dtype(torch, target: str):
    if not target.startswith("cuda"):
        return torch.float32
    return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16


def _on_device(load):
    """Load on CUDA when it works, otherwise on the CPU."""
    import torch
    try:
        if torch.cuda.is_available():
            return load("cuda:0")
    except (RuntimeError, OSError) as exc:
        runtime.note(f"CUDA unavailable, falling back to CPU: {exc}")
        free_gpu()
    return load("cpu")


def free_gpu() -> None:
    try:
        import torch
        torch.cuda.empty_cache()
    except (ImportError, RuntimeError, OSError):
        pass


def out_of_memory(error: BaseException) -> bool:
    return "out of memory" in str(error).lower() or type(error).__name__ == "OutOfMemoryError"


def build(target: str | None = None):
    """Load the recognizer with its aligner; falls back to CPU when CUDA is unusable."""
    model_root = runtime.models()
    quiet_ml()
    import torch
    from transformers.utils import logging as hf_logging
    from qwen_asr import Qwen3ASRModel
    hf_logging.set_verbosity_error()

    def load(target: str):
        kwargs = {"dtype": _dtype(torch, target), "device_map": target}
        model = Qwen3ASRModel.from_pretrained(
            str(model_root / "Qwen3-ASR-1.7B"), forced_aligner=str(model_root / "Qwen3-ForcedAligner-0.6B"),
            forced_aligner_kwargs=dict(kwargs), max_inference_batch_size=batch_size(target), max_new_tokens=1024, **kwargs)
        generation = getattr(getattr(model, "model", None), "generation_config", None)
        if generation is not None and generation.pad_token_id is None:
            generation.pad_token_id = generation.eos_token_id
        return model
    return load(target) if target else _on_device(load)


def build_aligner(target: str | None = None):
    """Only the forced aligner (re-timing corrected text needs no recognizer)."""
    model_root = runtime.models()
    quiet_ml()
    import torch
    from transformers.utils import logging as hf_logging
    from qwen_asr import Qwen3ForcedAligner
    hf_logging.set_verbosity_error()

    def load(target: str):
        return Qwen3ForcedAligner.from_pretrained(str(model_root / "Qwen3-ForcedAligner-0.6B"),
                                                  dtype=_dtype(torch, target), device_map=target)
    return load(target) if target else _on_device(load)


def _folded(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).lower() if ch.isalnum())


def _locate(text: str, token: str, cursor: int) -> tuple[int, int] | None:
    """Span of `token` in `text` from `cursor`: exact, else the next characters that fold to it (全角 digits, case)."""
    found = text.find(token, cursor)
    if found >= 0:
        return found, found + len(token)
    wanted = _folded(token)
    start = cursor
    while start < len(text) and not _folded(text[start]):
        start += 1
    end, folded = start, ""
    while end < len(text) and len(folded) < len(wanted):
        folded += _folded(text[end])
        end += 1
    return (start, end) if folded and folded == wanted else None


def attach_punctuation(text: str, items) -> list[dict]:
    """Aligned tokens keep the punctuation that follows them (subtitles and sentence splits read it).

    Words carry the recognized text's own characters; a token that cannot be found at all is skipped (its time
    goes to the neighbours) instead of failing the run.
    """
    words, cursor, lead = [], 0, ""
    for item in items:
        token = item.text
        span = _locate(text, token, cursor) if token else None
        if span is None:
            continue
        found, stop = span
        skipped = text[cursor:found]
        kept = skipped.rstrip()
        if words:
            words[-1]["word"] += kept
        else:
            lead = kept
        lead += skipped[len(kept):]
        start, end = float(item.start_time), float(item.end_time)
        words.append({"word": lead + text[found:stop], "start": start, "end": max(start, end)})
        lead, cursor = "", stop
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
    """Batched recognition; returns word lists (with punctuation) in source seconds.

    GPU memory exhaustion retries one source at a time, then on the CPU, so a long batch never fails outright.
    """
    present = [i for i, audio in enumerate(audios) if len(audio)]
    results: list[list[dict]] = [[] for _ in audios]
    if not present:
        return results

    def transcribe(active, indices):
        return active.transcribe([(audios[i], SAMPLE_RATE) for i in indices], context=context,
                                 language="Chinese", return_time_stamps=True)
    try:
        output = transcribe(model, present)
    except RuntimeError as error:
        if not out_of_memory(error):
            raise
        free_gpu()
        runtime.note("GPU memory exhausted; recognizing one source at a time")
        output = []
        try:
            for i in present:
                output += transcribe(model, [i])
        except RuntimeError as again:
            if not out_of_memory(again):
                raise
            free_gpu()
            runtime.note("GPU memory still exhausted; recognizing on the CPU")
            cpu = build("cpu")
            output += [row for i in present[len(output):] for row in transcribe(cpu, [i])]
    for i, row in zip(present, output):
        text = row.text.strip()
        if text and row.time_stamps is not None:
            results[i] = regularize(attach_punctuation(text, row.time_stamps.items), len(audios[i]) / SAMPLE_RATE)
    return results


def _aligner(model):
    return getattr(model, "forced_aligner", None) or model


def align(model, audio: np.ndarray, text: str, origin: float) -> list[dict]:
    """Word times of a corrected transcript inside one audio slice that starts at `origin` seconds."""
    return align_many(model, [(audio, text, origin)])[0]


def align_many(model, requests: list[tuple[np.ndarray, str, float]], batch: int = 8) -> list[list[dict]]:
    """Word times for many (audio slice, corrected text, origin) requests, batched on the aligner."""
    aligner = _aligner(model)
    results: list[list[dict]] = [[] for _ in requests]
    todo = [i for i, (_, text, _) in enumerate(requests) if text.strip()]
    on_cpu = False
    while todo:
        chunk, todo = todo[:batch], todo[batch:]
        try:
            rows = aligner.align(audio=[(requests[i][0], SAMPLE_RATE) for i in chunk],
                                 text=[requests[i][1] for i in chunk], language=["Chinese"] * len(chunk))
        except RuntimeError as error:
            if not out_of_memory(error) or on_cpu:
                raise
            free_gpu()
            if batch == 1:
                runtime.note("GPU memory exhausted; aligning on the CPU")
                aligner, on_cpu = build_aligner("cpu"), True
            todo, batch = chunk + todo, 1
            continue
        for i, row in zip(chunk, rows):
            audio, text, origin = requests[i]
            words = attach_punctuation(text, row.items)
            for word in words:
                word["start"] += origin
                word["end"] += origin
            results[i] = regularize(words, len(audio) / SAMPLE_RATE, origin)
    return results
