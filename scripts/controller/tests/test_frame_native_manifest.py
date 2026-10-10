from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("controller", ROOT / "scripts/ffmpeg_controller.py")
controller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(controller)


class FrameNativeManifestTests(unittest.TestCase):
    def test_authorized_premaster_hash_chain(self):
        root = Path(tempfile.mkdtemp())
        export = root / "premaster.mp4"
        export.write_bytes(b"approved")
        plan = root / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        evidence = root / "render.json"
        evidence.write_text(json.dumps({"render_mode": "source_frame_ranges/v1", "plan_path": str(plan), "plan_sha256": controller.sha(plan), "export_path": str(export), "export_sha256": controller.sha(export)}), encoding="utf-8")
        item = {"plan_id": "P1", "export_path": str(export), "export_sha256": controller.sha(export), "plan_path": str(plan), "plan_sha256": controller.sha(plan), "render_evidence_path": str(evidence)}
        release = {"outputs": [{"plan_id": "P1", "path": str(export), "sha256": controller.sha(export)}]}
        controller.require_authorized_premasters([item], release)
        export.write_bytes(b"swapped")
        with self.assertRaisesRegex(RuntimeError, "semantic-authorized"):
            controller.require_authorized_premasters([item], release)

    def test_unsafe_plan_id_is_rejected_before_output_path(self):
        with self.assertRaisesRegex(RuntimeError, "unsafe plan ID"):
            controller.require_authorized_premasters([{"plan_id": "../escape"}], {"outputs": [{"plan_id": "../escape"}]})

    def test_case_only_plan_ids_are_rejected_before_reading_media(self):
        items = [{"plan_id": "P1"}, {"plan_id": "p1"}]
        with self.assertRaisesRegex(RuntimeError, "duplicate.*plan IDs"):
            controller.require_authorized_premasters(items, {"outputs": items})

    def test_duplicate_premaster_ids_cannot_reuse_one_authorization(self):
        items = [{"plan_id": "P1"}, {"plan_id": "P1"}]
        with self.assertRaisesRegex(RuntimeError, "duplicate.*plan IDs"):
            controller.require_authorized_premasters(items, {"outputs": [{"plan_id": "P1"}, {"plan_id": "P2"}]})

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
