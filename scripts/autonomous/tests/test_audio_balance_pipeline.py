"""Ensure autonomous rendering forwards batch/individual balance settings."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("balance_pipeline_auto",
    Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class AudioBalancePipelineTests(unittest.TestCase):
    def test_render_clean_forwards_inherited_and_individual_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            auto.write(plan_path, {"audio_balance": {"target_lufs": -22}, "outputs": [
                {"plan_id": "A", "segments": [{"text": "口播"}]},
                {"plan_id": "B", "segments": [{"text": "口播"}], "audio_balance": {"enabled": False}},
            ]})
            value = {"phase": "plan_approved", "repair_round": 0, "plan": auto.ref(plan_path),
                     "plan_review": auto.ref(plan_path), "plan_evidence": auto.ref(plan_path)}
            received = []

            def render(unit, target, evidence, *args, **kwargs):
                received.append(json.loads(unit.read_text(encoding="utf-8")))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"rendered")
                auto.write(evidence, {"actual_output_frames": 60})
                return {"actual_output_frames": 60}

            with patch.multiple(auto, state=lambda _: value, require_editing_approval=lambda _: None,
                                start_pending_delivery=lambda *args: None, processing_dir=lambda _: root,
                                module=lambda *args: SimpleNamespace(render=render), save=lambda *args: None):
                auto.render_clean(SimpleNamespace(job_dir=root))
            self.assertEqual(-22, received[0]["audio_balance"]["target_lufs"])
            self.assertTrue(received[0]["audio_balance"]["enabled"])
            self.assertFalse(received[1]["audio_balance"]["enabled"])
            self.assertEqual(-22, received[1]["audio_balance"]["target_lufs"])

    def test_render_check_rejects_failed_balance_evidence(self):
        with self.assertRaisesRegex(ValueError, "audio balance"):
            auto.audio_render_check({}, "A", {"audio_balance": {"enabled": True},
                "checks": {"audio_balance": False}, "segments": []})


if __name__ == "__main__":
    unittest.main()
