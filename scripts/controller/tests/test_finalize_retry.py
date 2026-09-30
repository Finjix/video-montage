from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ffmpeg_controller.py"
SPEC = importlib.util.spec_from_file_location("controller_retry", SCRIPT)
controller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(controller)


class FinalizeRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.item = {"plan_id": "P1", "export_path": str(self.source),
                     "render_evidence_path": str(self.root / "render.json"), "render_evidence_sha256": "hash"}
        self.output = self.root / "output"

    def test_failed_encode_removes_its_partial(self):
        self.output.mkdir()
        def fail(command, **_):
            Path(command[-1]).write_bytes(b"incomplete")
            return subprocess.CompletedProcess(command, 1, "", "encode failed")
        with patch.object(controller.subprocess, "run", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "encode failed"):
                controller.finalize_one(self.item, self.output, 1440, 2560, 60, "libx264")
        self.assertFalse((self.output / "P1.partial.mp4").exists())

    def test_successful_item_is_reused_only_with_matching_receipt(self):
        self.output.mkdir()
        def encode(command, **_):
            Path(command[-1]).write_bytes(b"encoded")
            return subprocess.CompletedProcess(command, 0, "", "")
        info = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1440,
                             "height": 2560, "avg_frame_rate": "60/1"},
                            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}],
                "format": {"duration": "1.0"}}
        with patch.object(controller.subprocess, "run", side_effect=encode), patch.object(controller, "probe", return_value=info):
            first = controller.finalize_one(self.item, self.output, 1440, 2560, 60, "libx264")
        with patch.object(controller.subprocess, "run", side_effect=AssertionError("must reuse")):
            second = controller.finalize_one(self.item, self.output, 1440, 2560, 60, "libx264")
        self.assertEqual(first, second)
        (self.output / "P1.mp4").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "matching receipt"):
            controller.finalize_one(self.item, self.output, 1440, 2560, 60, "libx264")


if __name__ == "__main__":
    unittest.main()
