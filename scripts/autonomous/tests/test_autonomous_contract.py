from __future__ import annotations

import importlib.util
import io
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
    def test_init_stdin_keeps_work_root_free_of_loose_files(self):
        source = self.root / 'source.mp4'; source.write_bytes(b'source fixture')
        assets = self.root / 'assets'; assets.mkdir()
        parent = self.root / 'work'
        job = parent / '自动化混剪_20261010_120000' / '临时文件'
        order = {'schema': auto.ORDER_SCHEMA, 'requested_outputs': 1,
                 'sources': [{'path': str(source)}], 'asset_root': str(assets),
                 'output_root': str(parent)}
        with patch.object(auto.sys, 'stdin', io.StringIO(json.dumps(order))), patch('builtins.print'):
            auto.init(SimpleNamespace(work_order=Path('-'), job_dir=job))
        self.assertEqual(auto.state(job)['phase'], 'initialized')
        self.assertEqual(auto.TEXT_PIN_POLICY, auto.state(job)['text_pin_policy'])
        self.assertEqual(auto.TEXT_PIN_POLICY, auto.read(job / 'work_order.json')['text_pin_policy'])
        self.assertEqual(auto.read(job / 'work_order.json')['sources'], order['sources'])
        self.assertEqual(list(parent.iterdir()), [job.parent])
        with patch.object(auto.sys, 'stdin', io.StringIO(json.dumps(order))):
            with self.assertRaises(FileExistsError):
                auto.init(SimpleNamespace(work_order=Path('-'), job_dir=job))

    def test_required_text_pin_policy_cannot_be_removed_from_bound_state(self):
        job = self.root / 'pin-job'; job.mkdir()
        order = job / 'order.json'
        auto.write(order, {'schema': auto.ORDER_SCHEMA, 'text_pin_policy': auto.TEXT_PIN_POLICY})
        value = {'schema': auto.STATE_SCHEMA, 'review_mode': 'codex_asr_pcm',
                 'work_order': auto.ref(order), 'text_pin_policy': auto.TEXT_PIN_POLICY}
        auto.write(job / 'autonomous_state.json', value)
        self.assertEqual(auto.TEXT_PIN_POLICY, auto.state(job)['text_pin_policy'])
        value.pop('text_pin_policy')
        auto.write(job / 'autonomous_state.json', value)
        with self.assertRaisesRegex(ValueError, 'text pin'):
            auto.state(job)

    def test_overlay_reburn_missing_unused_original_keeps_inventory_but_used_missing_rejected(self):
        original = self.root / 'used.mp4'; original.write_bytes(b'original bytes')
        unused = self.root / 'unused.mp4'; unused.write_bytes(b'unused bytes')
        asr_a = self.root / 'used-asr.json'; auto.write(asr_a, {'text': 'used speech'})
        asr_b = self.root / 'unused-asr.json'; auto.write(asr_b, {'text': 'unused speech'})
        index = {'sources': [{'source': auto.ref(original), 'asr': auto.ref(asr_a)},
                             {'source': auto.ref(unused), 'asr': auto.ref(asr_b)}]}
        plan = {'outputs': [{'segments': [{'source_sha256': auto.sha(original)}]}]}
        unused.unlink()
        audit = auto.audit_original_sources(index, plan, reburn_only=True)
        self.assertEqual(1, audit['verified_source_count'])
        self.assertEqual([index['sources'][1]['source']], audit['unavailable_unused_sources'])
        with self.assertRaisesRegex(ValueError, 'original source'):
            auto.audit_original_sources(index, plan, reburn_only=False)
        selected_missing = {'outputs': [{'segments': [{'source_sha256': index['sources'][1]['source']['sha256']}]}]}
        with self.assertRaisesRegex(ValueError, 'original source'):
            auto.audit_original_sources(index, selected_missing, reburn_only=True)
        unused.write_bytes(b'changed unused bytes')
        with self.assertRaisesRegex(ValueError, 'original source'):
            auto.audit_original_sources(index, plan, reburn_only=True)
        unused.unlink(); auto.write(asr_b, {'text': 'tampered historical speech'})
        with self.assertRaisesRegex(ValueError, 'original source ASR'):
            auto.audit_original_sources(index, plan, reburn_only=True)

    def test_completion_retry_rejects_evidence_for_different_packaged_delivery(self):
        job = self.root / 'retry-job'; job.mkdir()
        order = job / 'order.json'; auto.write(order, {'schema': auto.ORDER_SCHEMA})
        package = job / 'package.json'; auto.write(package, {'fixture': 'current delivery'})
        evidence = job / 'final.json'
        auto.write(evidence, {'decision': 'pass', 'manifest': {'path': str(package), 'sha256': '0' * 64}})
        review = job / 'review.json'
        auto.write(review, {'schema': auto.REVIEW_SCHEMA, 'stage': 'final', 'reviewer_role': 'codex',
                           'evidence_sha256': auto.sha(evidence)})
        auto.write(job / 'autonomous_state.json', {'schema': auto.STATE_SCHEMA, 'review_mode': 'codex_asr_pcm',
                   'phase': 'repair_required', 'work_order': auto.ref(order),
                   'final_evidence': auto.ref(evidence), 'packaging_delivery': auto.ref(package)})
        with self.assertRaisesRegex(ValueError, 'unchanged packaged delivery'):
            auto.complete(SimpleNamespace(job_dir=job, review=review))

    def test_full_repair_can_invalidate_an_edited_old_plan_without_authorizing_it(self):
        source = self.root / 'source.mp4'; source.write_bytes(b'source fixture')
        assets = self.root / 'assets'; assets.mkdir()
        job = self.root / 'work' / '自动化混剪_20261010_120000' / '临时文件'
        order = {'schema': auto.ORDER_SCHEMA, 'requested_outputs': 1,
                 'sources': [{'path': str(source)}], 'asset_root': str(assets),
                 'output_root': str(job.parent.parent)}
        with patch.object(auto.sys, 'stdin', io.StringIO(json.dumps(order))), patch('builtins.print'):
            auto.init(SimpleNamespace(work_order=Path('-'), job_dir=job))
        value = auto.state(job)
        plan = job / 'config' / 'plan.json'
        auto.write(plan, {'planning_policy': value['planning_policy'],
                          'batch_review_policy': value['batch_review_policy'],
                          'audio_cleanup_policy': value['audio_cleanup_policy']})
        value.update(plan=auto.ref(plan), plan_review={'path': 'old-approval', 'sha256': 'stale'}, phase='repair_required')
        auto.write(job / 'autonomous_state.json', value)
        auto.write(plan, {'outputs': []})
        with self.assertRaisesRegex(ValueError, 'changed or missing'):
            auto.state(job)
        with patch('builtins.print'):
            auto.repair(SimpleNamespace(job_dir=job, reason='replace rejected cuts', continue_until_complete=False))
        repaired = auto.state(job)
        self.assertNotIn('plan', repaired)
        self.assertNotIn('plan_review', repaired)
        self.assertEqual('repair_required', repaired['phase'])
        self.assertEqual(auto.AUDIO.POLICY, repaired['audio_cleanup_policy'])

    def test_rendered_shot_proof_requires_complete_bound_audio_and_speech(self):
        audio = self.root / 'shot.wav'; audio.write_bytes(b'actual encoded shot audio')
        planned = {'segments': [{'text': '三十个幸存者'}]}
        rendered = {'segments': [{'expected_output_frames': 60}], 'actual_output_frames': 60}
        output = {'path': 'clean.mp4', 'sha256': 'clean-hash'}
        timing = {'output': output, 'timing_basis': 'verified_rendered_shot_asr', 'shot_observations': [
            {'shot_index': 1, 'audio': auto.ref(audio), 'output_in_frame': 0, 'output_out_frame_exclusive': 60,
             'expected_text': '三十个幸存者', 'asr': {'text': '30个幸存者'}}]}
        self.assertTrue(auto.verified_shot_speech(timing, output, planned, rendered, {}))
        timing['shot_observations'][0]['asr']['text'] = '三个幸存者'
        self.assertFalse(auto.verified_shot_speech(timing, output, planned, rendered, {}))
        timing['shot_observations'][0]['asr']['text'] = '30个幸存者'
        timing['shot_observations'][0]['output_out_frame_exclusive'] = 59
        self.assertFalse(auto.verified_shot_speech(timing, output, planned, rendered, {}))
        timing['shot_observations'][0]['output_out_frame_exclusive'] = 60
        audio.write_bytes(b'changed audio')
        with self.assertRaisesRegex(ValueError, 'changed or missing'):
            auto.verified_shot_speech(timing, output, planned, rendered, {})

    def test_rendered_shot_asr_anchors_words_and_rejects_missing_speech(self):
        output = self.root / "clean.mp4"
        output.write_bytes(b"hash-bound clean video")
        rendered = {"segments": [{"expected_output_frames": 60}, {"expected_output_frames": 120}]}
        planned = {"segments": [{"text": "开局"}, {"text": "点燃熔炉"}]}
        observations = [
            {"text": "开局", "segments": [{"start": .1, "end": .6,
              "words": [{"word": "开局", "start": .1, "end": .6}]}]},
            {"text": "点燃熔炉", "segments": [{"start": 0., "end": 1.4,
              "words": [{"word": "点燃熔炉", "start": 0., "end": 1.4}]}]}]
        def encode(command):
            Path(command[-1]).write_bytes(b"mock decoded shot audio")
        with patch.object(auto, "run", side_effect=encode), patch.object(auto, "transcribe", side_effect=observations):
            result = auto.rendered_shot_asr(None, output, rendered, planned, {}, self.root / "shots")
        self.assertEqual(auto.ref(output), result["output"])
        self.assertEqual(1., result["segments"][1]["words"][0]["start"])
        self.assertEqual(2.4, result["segments"][1]["words"][0]["end"])
        self.assertEqual("开局 点燃熔炉", result["text"])
        self.assertEqual(auto.ref(self.root / "shots/shot_001.wav"), result["shot_observations"][0]["audio"])
        self.assertEqual(observations[0], result["shot_observations"][0]["asr"])
        truncated = {"text": "燃熔炉", "segments": []}
        with patch.object(auto, "run", side_effect=encode), patch.object(auto, "transcribe", side_effect=[observations[0], truncated]):
            with self.assertRaisesRegex(ValueError, "rendered speech differs"):
                auto.rendered_shot_asr(None, output, rendered, planned, {}, self.root / "shots")

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
        self.assertEqual(auto.normalized('我收了30个幸存者'), auto.normalized('我收了三十个幸存者'))
        self.assertEqual(auto.normalized('13个'), auto.normalized('十三个'))
        self.assertNotEqual(auto.normalized('13个'), auto.normalized('一三个'))
        self.assertNotEqual(auto.normalized('30个'), auto.normalized('300个'))
        self.assertEqual(auto.subtitle_spelling('随开随晚吴岳聚爽聚解压'),
                         auto.subtitle_spelling('随开随玩吴樾巨爽巨解压'))
        self.assertNotEqual(auto.normalized('随开随晚吴岳'), auto.normalized('随开随玩吴樾'))
        self.assertNotEqual(auto.subtitle_spelling('随开随晚'), auto.subtitle_spelling('随开随玩免费领钱'))
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
        self.assertTrue((output / "临时文件" / "config").is_dir())
        self.assertEqual(job / "work_order.json", Path(value["work_order"]["path"]))
        auto.init(SimpleNamespace(job_dir=None, work_order=order))
        generated = [p for p in parent.iterdir() if p != output]
        self.assertEqual(1, len(generated))
        self.assertRegex(generated[0].name, r"^自动化混剪_\d{8}_\d{6}$")
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
        package = job / "packaging.json"
        auto.write(package, {"results": [{"plan_id": "P1", "final_speed": 1.2}]})
        value["packaging_delivery"] = auto.ref(package)
        auto.write(evidence, {"decision": "pass", "manifest": auto.ref(package),
                            "results": [{"plan_id": "P1", "output": auto.ref(media), "frames": []}]})
        reviewed = auto.read(review); reviewed["evidence_sha256"] = auto.sha(evidence); auto.write(review, reviewed)
        value.update({"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm", "phase": "final_evidenced",
                     "output_root": str(output), "delivery_layout": "chinese/v1", "final_evidence": auto.ref(evidence)})
        auto.write(job / "autonomous_state.json", value)
        def validate(manifest, report, **kwargs):
            auto.write(report, {"decision": "pass"})
            return {"decision": "pass"}
        with patch.object(auto, "audit_provenance"), patch.object(auto, "module", return_value=packager), \
             patch.object(packager, "validate", side_effect=validate):
            auto.complete(SimpleNamespace(job_dir=job, review=review))
            # A previously failed signing step may retry the same bound media;
            # it still runs the audit, review and technical checks again.
            value["phase"] = "repair_required"
            auto.write(job / "autonomous_state.json", value)
            auto.complete(SimpleNamespace(job_dir=job, review=review))
        self.assertTrue((job / "video_montage_autonomous_completion.json").is_file())
        self.assertTrue((output / "临时文件" / "config").is_dir())
        self.assertEqual(job, output / "临时文件")
        self.assertFalse(list((output / "成片").glob("*.json")))

    def test_compact_delivery_keeps_completion_and_preserves_reburn_gate(self):
        output = self.root / "work" / "自动化混剪_20261008_120000"
        job = output / "临时文件"
        order = job / "work_order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA})
        clean = job / "manifests" / "clean_delivery.json"
        auto.write(clean, {"decision": "pass"})
        qc = job / "reports" / "clean_qc.json"
        auto.write(qc, {"decision": "pass", "clean_delivery": auto.ref(clean)})
        manifest = job / "manifests" / "packaging_manifest.json"
        auto.write(manifest, {"clean_delivery_path": str(clean.resolve()),
                   "controller_validation_path": str(qc.resolve()),
                   "controller_validation_sha256": auto.sha(qc)})
        auto.write(job / "config" / "packaging.json", {})
        auto.write(job / "evidence" / "frames.json", {})
        final_evidence = job / "reports" / "final_evidence.json"
        auto.write(final_evidence, {"decision": "pass"})
        final_review = job / "reviews" / "final_review.json"
        auto.write(final_review, {"stage": "final", "reviewer_role": "codex"})
        technical = job / "reports" / "packaging_technical.json"
        auto.write(technical, {"decision": "pass"})
        completion = job / "video_montage_autonomous_completion.json"
        auto.write(completion, {"decision": "pass", "final_evidence": auto.ref(final_evidence),
                               "final_review": auto.ref(final_review), "packaging_technical": auto.ref(technical)})
        value = {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm",
                 "work_order": auto.ref(order), "packaging_delivery": auto.ref(manifest),
                 "completion": auto.ref(completion),
                 "output_root": str(output), "delivery_directory": str(output), "repair_round": 0}
        auto.compact_delivery(job, value)
        self.assertEqual({"config", "manifests", "reports", "reviews"}, {p.name for p in job.iterdir() if p.is_dir()})
        self.assertEqual(completion, auto.require_ref(auto.state(job)["completion"], "completion"))
        for retained in (final_evidence, final_review, technical):
            self.assertTrue(retained.is_file())
        self.assertFalse((job / "evidence" / "frames.json").exists())
        updated = auto.read(manifest)
        copied = Path(updated["controller_validation_path"])
        self.assertEqual(job / "manifests" / "clean_validation.json", copied)
        self.assertEqual(updated["controller_validation_sha256"], auto.sha(copied))
        self.assertTrue(auto.state(job)["compact_delivery"])
        auto.repair(SimpleNamespace(job_dir=job, reason="redo cut", continue_until_complete=False))
        self.assertEqual("repair_required", auto.state(job)["phase"])
        self.assertNotIn("compact_delivery", auto.state(job))
        self.assertFalse(completion.exists())

    def test_completed_job_repair_pins_actual_delivery_and_invalidates_completion(self):
        job = self.root / "job"; job.mkdir()
        order = self.root / "order.json"; auto.write(order, {"schema": auto.ORDER_SCHEMA})
        directory = self.root / "自动化混剪_20260930_120003_123456"
        manifest = directory / "日志" / "manifests" / "packaging_manifest.json"
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
        # A quiet speech onset is not an abrupt loud music change.
        samples[:16000] = .0001
        samples[16000:] = .025
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

    def test_delivery_tempo_retry_preserves_trials_and_rescales_actual_word_times(self):
        audio = self.root / 'actual.wav'; audio.write_bytes(b'actual audio fixture')
        expected = '我是无尽冬日明星玩家吴岳我在无尽冬日等你'
        bad = expected.replace('明星', '明信').replace('吴岳', '吴越')
        good = expected.replace('明星', '明信')
        observations = iter([bad, bad, bad, good])
        prompts = []
        def recognize(model, path, language, **kwargs):
            prompts.append(kwargs['initial_prompt'])
            text = next(observations)
            return {'text': text, 'segments': [{'start': .2, 'end': 1., 'text': text,
                'words': [{'word': text, 'start': .2, 'end': 1.}]}]}, {}
        def encode(command, **kwargs):
            Path(command[-1]).write_bytes(b'time augmentation fixture')
        assets = {'assets': [{'text': '无尽冬日 明星玩家 吴樾'}]}
        with patch.object(auto, 'module', return_value=SimpleNamespace(transcribe=recognize)), patch.object(auto, 'run', side_effect=encode):
            result = auto.transcribe(object(), audio, expected, assets, auto.EDITING.POLICY)
        self.assertEqual(result['recognition_tempo'], 1.2)
        self.assertEqual([r['tempo'] for r in result['recognition_trials']], [1., 1.1, 1.3, 1.2])
        self.assertEqual(result['recognition_trials'][0]['text'], bad)
        self.assertEqual(result['segments'][0]['words'][0]['start'], .24)
        self.assertEqual(result['segments'][0]['words'][0]['end'], 1.2)
        self.assertEqual(set(prompts), {'以下是简体中文口播。'})

    def test_delivery_tempo_retry_cannot_turn_truncated_speech_into_a_match(self):
        audio = self.root / 'actual.wav'; audio.write_bytes(b'actual audio fixture')
        expected = '我是无尽冬日明星玩家吴岳我在无尽冬日等你'
        missing = expected[1:]
        def recognize(*args, **kwargs):
            return {'text': missing, 'segments': []}, {}
        def encode(command, **kwargs):
            Path(command[-1]).write_bytes(b'time augmentation fixture')
        with patch.object(auto, 'module', return_value=SimpleNamespace(transcribe=recognize)), patch.object(auto, 'run', side_effect=encode):
            result = auto.transcribe(object(), audio, expected, {'assets': [{'text': '无尽冬日 明星玩家 吴樾'}]}, auto.EDITING.POLICY)
        self.assertEqual(result['text'], missing)
        self.assertNotIn('recognition_tempo', result)
        self.assertFalse(auto.context_corroborates_asr(expected, result['text'], {'assets': [{'text': expected}]}, auto.EDITING.POLICY))

    def test_subtitle_early_asr_edge_clamps_to_bound_shot(self):
        auto.validate_subtitle_timing(2817, 4120, (2560, 3020), (3920, 4120), (2817, 9667))
        auto.validate_subtitle_timing(11217, 14180, (10860, 11250), (14000, 14180), (11217, 18467))
        with self.assertRaisesRegex(ValueError, "outside its rendered shot"):
            auto.validate_subtitle_timing(2560, 4120, (2560, 3020), (3920, 4120), (2817, 9667))
        with self.assertRaisesRegex(ValueError, "do not overlap"):
            auto.validate_subtitle_timing(2817, 4120, (2000, 2500), (3920, 4120), (2817, 9667))
        with self.assertRaisesRegex(ValueError, "does not follow"):
            auto.validate_subtitle_timing(3200, 4120, (2560, 3020), (3920, 4120), (2817, 9667))

    def test_repairs_continue_beyond_three_rounds_without_pass_receipt(self):
        job = self.root / "job"
        order = self.root / "order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA})
        job.mkdir()
        value = {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm",
                 "work_order": auto.ref(order), "repair_round": 0}
        for round_number in range(1, 9):
            with self.assertRaisesRegex(RuntimeError, f"repair round {round_number};"):
                auto.fail_round(job, value, "test", ["unconfirmed audio"])
            self.assertEqual(round_number, auto.state(job)["repair_round"])
            self.assertEqual("repair_required", auto.state(job)["phase"])
            report = auto.read(job / "reports" / f"repair_round_{round_number}.json")
            self.assertEqual(["unconfirmed audio"], report["failures"])
            self.assertIsNone(report["max_rounds"])
        self.assertTrue(auto.state(job)["continue_until_complete"])
        self.assertIsNone(auto.state(job)["max_repair_rounds"])
        self.assertFalse((job / "video_montage_autonomous_completion.json").exists())


if __name__ == "__main__":
    unittest.main()
