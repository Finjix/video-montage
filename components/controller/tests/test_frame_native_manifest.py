from __future__ import annotations

import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("controller", ROOT / "scripts/ffmpeg_controller.py")
controller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(controller)


class FrameNativeManifestTests(unittest.TestCase):
    def test_seconds_based_legacy_manifest_is_rejected(self):
        with self.assertRaises(RuntimeError):
            controller.require_frame_native_manifest({"results": [{"plan_id": "01"}]})

    def test_frame_native_manifest_passes(self):
        render=Path(tempfile.mkdtemp())/"render.json"
        render.write_text("{}",encoding="utf-8")
        controller.require_frame_native_manifest(
            {
                "render_mode": "source_frame_ranges/v1",
                "seconds_only_fallback": False,
                "results": [{"plan_id": "01", "render_mode": "source_frame_ranges/v1", "render_evidence_path": str(render), "render_evidence_sha256": hashlib.sha256(render.read_bytes()).hexdigest()}],
            }
        )


if __name__ == "__main__":
    unittest.main()
