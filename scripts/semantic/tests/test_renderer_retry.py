from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/portable_frame_renderer.py"
SPEC = importlib.util.spec_from_file_location("frame_renderer_retry", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(renderer)


class RendererRetryTests(unittest.TestCase):
    def test_ffmpeg_failure_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            plan = root / "plan.json"
            plan.write_text(json.dumps({"segments": [{"source_path": str(source), "source_sha256": renderer.sha256(source),
                                                       "source_in_frame": 0, "speech_end_frame": 1,
                                                       "source_out_frame_exclusive": 2,
                                                       "source_fps_num": 60, "source_fps_den": 1}]}), encoding="utf-8")
            output = root / "output.mp4"
            info = {"streams": [{"codec_type": "video", "avg_frame_rate": "60/1"}]}
            def fail(command, **_):
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "encode failed")
            with patch.object(renderer, "runtime_binary", return_value=root / "ffmpeg.exe"), \
                 patch.object(renderer, "probe", return_value=info), \
                 patch.object(renderer, "require_constant_frame_rate"), \
                 patch.object(renderer.subprocess, "run", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "encode failed"):
                    renderer.render(plan, output, root / "evidence.json", 1440, 2560, 60, "libx264")
            self.assertFalse(output.with_suffix(".partial.mp4").exists())


if __name__ == "__main__":
    unittest.main()
