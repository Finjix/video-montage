from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py"
SPEC = importlib.util.spec_from_file_location("autonomous_montage_test", SCRIPT)
auto = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(auto)


class AutonomousContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_old_job_cannot_be_reinterpreted(self):
        job = self.root / "old"
        job.mkdir()
        auto.write(job / "autonomous_state.json", {"schema": "video-montage-state/v260928"})
        with self.assertRaisesRegex(ValueError, "legacy state cannot be migrated"):
            auto.state(job)

    def test_frame_native_plan_requires_full_scope_and_source_binding(self):
        source = self.root / "original.mp4"
        source.write_bytes(b"original")
        source_ref = auto.ref(source)
        index = {"sources": [{"source": source_ref, "video": {"fps_num": 60, "fps_den": 1, "frames": 100}}]}
        segment = {"source_path": str(source), "source_sha256": source_ref["sha256"],
                   "source_in_frame": 4, "speech_end_frame": 25, "source_out_frame_exclusive": 30,
                   "source_fps_num": 60, "source_fps_den": 1, "text": "完整口播"}
        plan = {"schema": auto.PLAN_SCHEMA, "outputs": [{"plan_id": "P1", "segments": [segment]}]}
        self.assertEqual([], auto.check_plan(plan, index, 1))
        self.assertIn("plan schema or complete output scope", auto.check_plan(plan, index, 2))
        changed = json.loads(json.dumps(plan))
        changed["outputs"][0]["segments"][0]["source_sha256"] = "0" * 64
        self.assertIn("P1: source not in work order", auto.check_plan(changed, index, 1))
        changed = json.loads(json.dumps(plan))
        changed["outputs"][0]["segments"][0]["source_in_frame"] = 4.5
        self.assertTrue(any("required_integer" in error for error in auto.check_plan(changed, index, 1)))

    def test_subtitle_change_needs_real_bound_copy_or_asr_text(self):
        source_asr = self.root / "source_asr.json"
        auto.write(source_asr, {"asr": {"text": "欢迎来到无尽冬日"}})
        plan = self.root / "plan.json"
        auto.write(plan, {"outputs": [{"segments": [{"text": "欢迎来到无尽冬日"}]}]})
        asset = {"assets": [{"sha256": "a" * 64, "text": "无尽冬日"}]}
        source_index = {"sources": [{"asr": auto.ref(source_asr)}]}
        self.assertTrue(auto.subtitle_change_supported(
            {"evidence": [{"kind": "asset_copy", "sha256": "a" * 64, "text": "无尽冬日"}]},
            asset, source_index, auto.ref(plan)))
        self.assertFalse(auto.subtitle_change_supported(
            {"evidence": [{"kind": "asset_copy", "sha256": "a" * 64, "text": "不存在的广告语"}]},
            asset, source_index, auto.ref(plan)))
        self.assertFalse(auto.subtitle_change_supported(
            {"evidence": [{"kind": "source_asr", "sha256": "0" * 64, "text": "无尽冬日"}]},
            asset, source_index, auto.ref(plan)))

    def test_pcm_reports_clipping_and_isolated_impact(self):
        samples = np.zeros(16000, dtype=np.float32)
        samples[8000] = 1.0
        self.assertEqual(1, auto.audio_metrics(samples)["clipped_samples"])
        self.assertEqual(1, auto.isolated_transients(samples))
        quiet = np.zeros(16000, dtype=np.float32)
        self.assertEqual(0, auto.isolated_transients(quiet))

    def test_visual_boundary_rejects_source_transition_frames(self):
        visual = []
        for frame in range(26):
            color = (200, 35, 35) if frame < 2 else (120, 75, 115) if frame == 2 else (25, 110, 195)
            path = self.root / f"frame_{frame}.png"
            Image.new("RGB", (64, 114), color).save(path)
            visual.append({"frame": frame, **auto.ref(path)})
        original = auto.visual_boundary_metrics(visual, 0, 26)
        self.assertEqual("reject", original["entry"]["decision"])
        self.assertEqual("pass", original["exit"]["decision"])
        repaired = auto.visual_boundary_metrics(visual, 3, 26)
        self.assertEqual("pass", repaired["decision"])
        self.assertEqual("reject", auto.visual_boundary_metrics(visual[:-1], 3, 26)["decision"])

    def test_visual_boundary_rejects_transition_just_outside_last_13_frames(self):
        visual = []
        for frame in range(70):
            color = (25, 110, 195) if frame < 56 else (200, 35, 35)
            path = self.root / f"late_frame_{frame}.png"
            Image.new("RGB", (64, 114), color).save(path)
            visual.append({"frame": frame, **auto.ref(path)})
        result = auto.visual_boundary_metrics(visual, 0, 70)
        self.assertEqual("pass", result["entry"]["decision"])
        self.assertEqual("reject", result["exit"]["decision"])
        self.assertLess(result["exit"]["largest_adjacent_difference"], .07)
        self.assertGreater(result["exit"]["largest_nearby_adjacent_difference"], .07)

    def test_cut_pcm_rejects_loud_tail_and_abrupt_music_change(self):
        samples = np.zeros(32000, dtype=np.float32)
        samples[15000:16000] = .1
        self.assertEqual("reject", auto.cut_pcm_metrics(samples, [60], clean=True)[0]["decision"])
        samples[:] = .001
        samples[16000:] = .1
        self.assertEqual("reject", auto.cut_pcm_metrics(samples, [60], clean=False)[0]["decision"])
        samples[:] = .001
        self.assertEqual("pass", auto.cut_pcm_metrics(samples, [60], clean=False)[0]["decision"])

    def test_bgm_masking_margin_and_stale_hash_are_rejected(self):
        voice = np.full(16000, .1, dtype=np.float32)
        loud_music = np.full(16000, .1, dtype=np.float32)
        soft_music = np.full(16000, .01, dtype=np.float32)
        self.assertLess(auto.voice_over_music_db(voice, loud_music, 0), 6)
        self.assertGreater(auto.voice_over_music_db(voice, soft_music, 0), 6)
        evidence = self.root / "evidence.json"
        evidence.write_text("first", encoding="utf-8")
        bound = auto.ref(evidence)
        evidence.write_text("stale", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            auto.require_ref(bound, "evidence")

    def test_subtitle_cannot_start_before_its_rendered_shot(self):
        plan = {"segments": [{"text": "无尽冬日"}, {"text": "中年人的解压神器"}]}
        render = {"segments": [{"expected_output_frames": 315}, {"expected_output_frames": 156}]}
        cues = [{"text": "无尽冬日"}, {"text": "中年人的解压神器"}]
        self.assertEqual([(0, 5250), (5250, 7850)], auto.subtitle_segment_bounds(cues, plan, render))
        premature_start_ms = 5020
        self.assertLess(premature_start_ms, auto.subtitle_segment_bounds(cues, plan, render)[1][0])
        with self.assertRaisesRegex(ValueError, "crosses rendered shot"):
            auto.subtitle_segment_bounds([{"text": "无尽冬日中"}, {"text": "年人的解压神器"}], plan, render)

    def test_context_rejects_missing_edge_words_and_unsupported_homophone(self):
        assets = {"assets": [{"text": "无尽冬日"}]}
        self.assertFalse(auto.context_corroborates_asr("欢迎无尽冬日", "无尽冬日", assets))
        self.assertFalse(auto.context_corroborates_asr("无尽冬日欢迎", "无尽冬日", assets))
        self.assertFalse(auto.context_corroborates_asr("欢迎无尽冬日啊", "欢迎无尽度日啊", None))
        self.assertTrue(auto.context_corroborates_asr("欢迎无尽冬日啊", "欢迎无尽度日啊", assets))

    def test_subtitle_early_asr_edge_clamps_to_bound_shot(self):
        auto.validate_subtitle_timing(2817, 4120, (2560, 3020), (3920, 4120), (2817, 9667))
        auto.validate_subtitle_timing(11217, 14180, (10860, 11250), (14000, 14180), (11217, 18467))
        with self.assertRaisesRegex(ValueError, "outside its rendered shot"):
            auto.validate_subtitle_timing(2560, 4120, (2560, 3020), (3920, 4120), (2817, 9667))
        with self.assertRaisesRegex(ValueError, "do not overlap"):
            auto.validate_subtitle_timing(2817, 4120, (2000, 2500), (3920, 4120), (2817, 9667))
        with self.assertRaisesRegex(ValueError, "does not follow"):
            auto.validate_subtitle_timing(3200, 4120, (2560, 3020), (3920, 4120), (2817, 9667))

    def test_three_repair_rounds_stop_without_pass_receipt(self):
        job = self.root / "job"
        order = self.root / "order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA})
        job.mkdir()
        value = {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm",
                 "work_order": auto.ref(order), "repair_round": 0}
        for round_number in range(1, 4):
            with self.assertRaisesRegex(RuntimeError, f"repair round {round_number}/3"):
                auto.fail_round(job, value, "test", ["unconfirmed audio"])
            self.assertEqual(round_number, auto.state(job)["repair_round"])
        self.assertEqual("failed", auto.state(job)["phase"])
        self.assertFalse((job / "video_montage_autonomous_completion.json").exists())


if __name__ == "__main__":
    unittest.main()
