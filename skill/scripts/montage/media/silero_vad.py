"""Silero VAD v6 on ONNX Runtime (CPU); frame-level speech probabilities for 16 kHz mono audio.

Adapted from snakers4/silero-vad (MIT) as shipped in faster-whisper; the inference code is reproduced exactly.
"""
from __future__ import annotations

import functools

import numpy as np

from montage.common import DEPENDENCIES

MODEL_PATH = DEPENDENCIES / "models" / "silero-vad" / "silero_vad_v6.onnx"


class SileroVADModel:
    def __init__(self, path):
        import onnxruntime
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)

    def __call__(self, audio: np.ndarray, num_samples: int = 512, context_size_samples: int = 64):
        assert audio.ndim == 1, "Input should be a 1D array"
        assert audio.shape[0] % num_samples == 0, "Input size should be a multiple of num_samples"
        h = np.zeros((1, 1, 128), dtype="float32")
        c = np.zeros((1, 1, 128), dtype="float32")
        batched_audio = audio.reshape(-1, num_samples)
        context = batched_audio[..., -context_size_samples:]
        context[-1] = 0
        context = np.roll(context, 1, 0)
        batched_audio = np.concatenate([context, batched_audio], 1)
        batched_audio = batched_audio.reshape(-1, num_samples + context_size_samples)
        encoder_batch_size = 10000
        outputs = []
        for i in range(0, batched_audio.shape[0], encoder_batch_size):
            output, h, c = self.session.run(None, {"input": batched_audio[i:i + encoder_batch_size], "h": h, "c": c})
            outputs.append(output)
        return np.concatenate(outputs, axis=0)


@functools.lru_cache
def get_vad_model() -> SileroVADModel:
    return SileroVADModel(MODEL_PATH)
