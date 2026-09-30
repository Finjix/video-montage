from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py"
SPEC = importlib.util.spec_from_file_location("autonomous_montage_test", SCRIPT)
auto = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(auto)


class AutonomousContractTests(unittest.TestCase):
    def test_explicit_continuation_preserves_round_numbers_and_failed_history(self):
        job = self.root / "job"
        order = self.root / "order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA})
        job.mkdir()
        auto.write(job / "autonomous_state.json", {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm",
                   "work_order": auto.ref(order), "repair_round": 3, "phase": "failed"})
        auto.repair(SimpleNamespace(job_dir=job, reason="fix overlay", continue_until_complete=True,
                                   authorization="User explicitly requested repair until full delivery"))
        value = auto.state(job)
        self.assertEqual(4, value["repair_round"])
        self.assertEqual("repair_required", value["phase"])
        with self.assertRaises(RuntimeError):
            auto.fail_round(job, value, "qc", ["still defective"])
        self.assertEqual(5, auto.state(job)["repair_round"])
        self.assertEqual("repair_required", auto.state(job)["phase"])
        self.assertFalse((job / "video_montage_autonomous_completion.json").exists())

    def test_packaged_mix_rejects_missing_voice_and_foreign_audio(self):
        rate = 16000
        t = np.arange(rate * 4) / rate
        voice = .1 * np.sin(2 * np.pi * 440 * t)
        music = .05 * np.sin(2 * np.pi * 120 * t)
        mixed = voice + music * .1
        self.assertEqual("pass", auto.packaged_mix_evidence(mixed, voice, music, -20)["decision"])
        dropped = mixed.copy(); dropped[rate:rate + 4000] -= voice[rate:rate + 4000]
        self.assertEqual("reject", auto.packaged_mix_evidence(dropped, voice, music, -20)["decision"])
        inserted = mixed.copy(); inserted[rate:rate + 4000] += .08 * np.sin(2 * np.pi * 900 * t[:4000])
        self.assertEqual("reject", auto.packaged_mix_evidence(inserted, voice, music, -20)["decision"])
        self.assertEqual("reject", auto.packaged_mix_evidence(mixed[:-1], voice, music, -20)["decision"])

    def test_reviewed_subtitle_spelling_keeps_other_speech_exact(self):
        self.assertEqual(auto.subtitle_spelling("做的是真呆劲"), auto.subtitle_spelling("做的是真带劲"))
        self.assertNotEqual(auto.subtitle_spelling("做的是真带劲"), auto.subtitle_spelling("做的是真没劲"))
        self.assertNotEqual(auto.subtitle_spelling("做的是真带劲"), auto.subtitle_spelling("做的是真带劲谢谢观看"))
        self.assertNotEqual(auto.normalized("真呆劲"), auto.normalized("真带劲"))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_new_job_creates_timestamped_delivery_under_requested_parent(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        assets = self.root / "assets"
        assets.mkdir()
        parent = self.root / "work"
        order = self.root / "order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA, "sources": [{"path": str(source)}],
                           "requested_outputs": 1, "asset_root": str(assets), "output_root": str(parent)})
        job = parent / "自动化混剪_20260930_153000_123456" / "临时文件"
        auto.init(SimpleNamespace(job_dir=job, work_order=order))
        value = auto.state(job)
        output = Path(value["output_root"])
        self.assertEqual(parent, output.parent)
        self.assertRegex(output.name, r"^自动化混剪_\d{8}_\d{6}_\d{6}$")
        self.assertEqual("chinese/v1", value["delivery_layout"])
        self.assertEqual(output, auto.attempt_dir(value))
        self.assertEqual(output / "临时文件", job)
        self.assertEqual("delivery-temporary/v1", value["records_policy"])
        self.assertTrue((output / "临时文件" / "配置").is_dir())
        self.assertEqual(job / "work_order.json", Path(value["work_order"]["path"]))
        auto.init(SimpleNamespace(job_dir=None, work_order=order))
        generated = [p for p in parent.iterdir() if p != output]
        self.assertEqual(1, len(generated))
        self.assertRegex(generated[0].name, r"^自动化混剪_\d{8}_\d{6}_\d{6}$")
        generated_job = generated[0] / "临时文件"
        self.assertEqual(generated[0], auto.attempt_dir(auto.state(generated_job)))
        self.assertFalse((self.root / ".runtime").exists())
        with self.assertRaisesRegex(ValueError, "job-dir must be work"):
            auto.init(SimpleNamespace(job_dir=self.root / ".runtime" / "jobs" / output.name, work_order=order))
        with self.assertRaisesRegex(FileExistsError, "non-empty delivery"):
            auto.init(SimpleNamespace(job_dir=job, work_order=order))
        bad_order = auto.read(order)
        bad_order["output_root"] = str(self.root / "output")
        auto.write(order, bad_order)
        with self.assertRaisesRegex(ValueError, "must be the work directory"):
            auto.init(SimpleNamespace(job_dir=None, work_order=order))

    def test_delivery_rounds_reuse_the_original_directory(self):
        value = {"output_root": str(self.root / "自动化混剪_20260930_235959_123456"),
                 "delivery_layout": "chinese/v1", "repair_round": 0}
        first = auto.attempt_dir(value)
        value["repair_round"] = 1
        second = auto.attempt_dir(value)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual("自动化混剪_20260930_235959_123456", second.name)
        self.assertEqual(first, second)

    def test_completion_keeps_records_in_delivery_temporary_directory(self):
        output = self.root / "work" / "自动化混剪_20260930_153000_123456"
        packager = auto.module("external_layout_test", "scripts/packaging/scripts/package_video.py")
        job = packager.runtime_directory(output)
        job.mkdir(parents=True)
        dummy = job / "fixture.json"; auto.write(dummy, {})
        media = output / "成片" / "P1.mp4"
        media.parent.mkdir(parents=True); media.write_bytes(b"video fixture")
        evidence = job / "evidence.json"
        auto.write(evidence, {"results": [{"plan_id": "P1", "output": auto.ref(media), "frames": []}]})
        review = job / "review.json"
        auto.write(review, {"schema": auto.REVIEW_SCHEMA, "stage": "final", "reviewer_role": "codex",
                   "evidence_sha256": auto.sha(evidence), "outputs": [{"plan_id": "P1",
                   "output_sha256": auto.sha(media), "visual_pass": True, "subtitle_pass": True,
                   "overlay_pass": True, "reason": "layout test fixture"}]})
        value = {key: auto.ref(dummy) for key in ("work_order", "source_index", "asset_copy", "plan",
                 "plan_evidence", "plan_review", "clean_delivery", "clean_qc", "subtitle_review", "packaging_delivery")}
        value.update({"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm", "phase": "final_evidenced",
                     "output_root": str(output), "delivery_layout": "chinese/v1", "final_evidence": auto.ref(evidence)})
        auto.write(job / "autonomous_state.json", value)
        def validate(manifest, report, **kwargs):
            auto.write(report, {"decision": "pass"})
            return {"decision": "pass"}
        with patch.object(auto, "audit_provenance"), patch.object(auto, "module", return_value=packager), \
             patch.object(packager, "validate", side_effect=validate):
            auto.complete(SimpleNamespace(job_dir=job, review=review))
        self.assertTrue((job / "video_montage_autonomous_completion.json").is_file())
        self.assertTrue((output / "临时文件" / "配置").is_dir())
        self.assertEqual(job, output / "临时文件")
        self.assertFalse(list((output / "成片").glob("*.json")))

    def test_completed_job_repair_pins_actual_delivery_and_invalidates_completion(self):
        job = self.root / "job"; job.mkdir()
        order = self.root / "order.json"; auto.write(order, {"schema": auto.ORDER_SCHEMA})
        directory = self.root / "自动化混剪_20260930_120003_123456"
        manifest = directory / "日志" / "清单" / "packaging_manifest.json"
        auto.write(manifest, {"results": [{"output_path": str(directory / "成片" / "P1.mp4")}]})
        completion = job / "video_montage_autonomous_completion.json"; auto.write(completion, {"decision": "pass"})
        auto.write(job / "autonomous_state.json", {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm",
                   "work_order": auto.ref(order), "repair_round": 1, "phase": "complete", "completion": auto.ref(completion),
                   "output_root": str(self.root / "自动化混剪_20260930_120000_123456"),
                   "delivery_layout": "chinese/v1", "packaging_delivery": auto.ref(manifest)})
        auto.repair(SimpleNamespace(job_dir=job, reason="fix subtitle", continue_until_complete=False, authorization=None))
        value = auto.state(job)
        self.assertEqual(directory, auto.attempt_dir(value))
        self.assertEqual("repair_required", value["phase"])
        self.assertNotIn("completion", value)
        self.assertFalse(completion.exists())

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
