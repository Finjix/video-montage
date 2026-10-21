import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from montage import runtime


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Run with the managed media environment")
class InferenceFallback(unittest.TestCase):
    def modules(self, fail_gpu=True):
        cuda = Mock()
        cuda.is_available.return_value = True
        cuda.is_bf16_supported.return_value = False
        cuda.get_device_properties.return_value = SimpleNamespace(total_memory=8 * 1024**3)
        torch = SimpleNamespace(cuda=cuda, bfloat16="bf16", float16="fp16", float32="fp32")
        loaded = []
        model = SimpleNamespace(model=SimpleNamespace(generation_config=SimpleNamespace(pad_token_id=None, eos_token_id=42)))

        def load(*args, **kwargs):
            loaded.append(kwargs)
            if fail_gpu and kwargs["device_map"].startswith("cuda"):
                raise RuntimeError("CUDA driver failure")
            return model

        return {"torch": torch, "qwen_asr": SimpleNamespace(Qwen3ASRModel=SimpleNamespace(from_pretrained=load)),
                "transformers.utils": SimpleNamespace(logging=Mock())}, loaded, model

    def test_cuda_failure_falls_back_even_if_empty_cache_fails(self):
        from montage import asr
        modules, loads, expected = self.modules()
        modules["torch"].cuda.empty_cache.side_effect = RuntimeError("no driver")
        with patch.dict(sys.modules, modules), patch.object(runtime, "models", return_value=Path("models")), patch.object(asr, "quiet_ml"):
            result = asr.build()
        self.assertIs(result, expected)
        self.assertEqual([load["device_map"] for load in loads], ["cuda:0", "cpu"])
        self.assertEqual(loads[1]["dtype"], "fp32")

    def test_cuda_uses_float16_without_bfloat16_support(self):
        from montage import asr
        modules, loads, _ = self.modules(fail_gpu=False)
        with patch.dict(sys.modules, modules), patch.object(runtime, "models", return_value=Path("models")), patch.object(asr, "quiet_ml"):
            asr.build()
        self.assertEqual(loads[0]["dtype"], "fp16")
        self.assertEqual(len(loads), 1)

    def test_download_failure_is_not_hidden_by_cpu_fallback(self):
        from montage import asr
        modules, loads, _ = self.modules()
        with patch.dict(sys.modules, modules), patch.object(runtime, "models", side_effect=runtime.RuntimeFailure("download failed")):
            with self.assertRaisesRegex(runtime.RuntimeFailure, "download failed"):
                asr.build()
        self.assertEqual(loads, [])
