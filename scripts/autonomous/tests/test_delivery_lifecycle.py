"""Real FFmpeg delivery/rollback tests; ASR is a deterministic test fixture."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py"
SPEC = importlib.util.spec_from_file_location("lifecycle_auto", SCRIPT)
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)


class DeliveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self):
        source = self.root / "original.mp4"
        subprocess.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i",
            "color=c=blue:s=1440x2560:r=60:d=1", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=1", "-af", "volume=0.01",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(source)], check=True, capture_output=True)
        delivery = self.root / "work" / "自动化混剪_20261008_120000"
        job = delivery / "临时文件"
        job.mkdir(parents=True)
        order = job / "work_order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA, "requested_outputs": 1})
        value = {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm", "work_order": auto.ref(order),
            "repair_round": 0, "output_root": str(delivery), "delivery_directory": str(delivery),
            "delivery_layout": "chinese/v1", "records_policy": "delivery-temporary/v1"}
        pending = auto.start_pending_delivery(job, value)
        packager = auto.module("lifecycle_packager", "scripts/packaging/scripts/package_video.py")
        packager.ensure_delivery_layout(delivery)
        packager.ensure_delivery_layout(pending)
        for folder in ("成片", "混剪（无包装）"):
            (delivery / folder / "P1.mp4").write_bytes(b"previous video")
        clean_output = pending / "混剪（无包装）" / "P1.mp4"
        shutil.copyfile(source, clean_output)
        asr = {"text": "口播", "segments": [{"start": .1, "end": .8, "text": "口播",
               "words": [{"word": "口播", "start": .1, "end": .8}]}]}
        source_asr = job / "evidence" / "source_asr.json"
        auto.write(source_asr, {"asr": asr})
        copy_source = self.root / "copy.txt"
        copy_source.write_text("口播", encoding="utf-8")
        assets = job / "evidence" / "asset_sources.json"
        auto.write(assets, {"assets": [auto.ref(copy_source)]})
        source_index = job / "evidence" / "source_index.json"
        auto.write(source_index, {"sources": [{"source": auto.ref(source), "asr": auto.ref(source_asr),
             "video": {"fps_num": 60, "fps_den": 1, "frames": 60}}], "asset_copy_sources": auto.ref(assets)})
        asset_copy = job / "evidence" / "asset_copy.json"
        auto.write(asset_copy, {"assets": [{**auto.ref(copy_source), "text": "口播"}]})
        plan = job / "plan.json"
        auto.write(plan, {"schema": auto.PLAN_SCHEMA, "outputs": [{"plan_id": "P1", "segments": [{
            "source_path": str(source), "source_sha256": auto.sha(source), "source_in_frame": 0,
            "speech_end_frame": 50, "source_out_frame_exclusive": 60, "source_fps_num": 60,
            "source_fps_den": 1, "text": "口播"}]}]})
        render = job / "evidence" / "render.json"
        auto.write(render, {"schema": "portable-frame-render-evidence/v260928", "actual_output_frames": 60,
            "expected_output_frames": 60, "segments": [{"expected_output_frames": 60, "source_segment_indexes": [1]}]})
        evidence = job / "evidence" / "plan_evidence.json"
        auto.write(evidence, {"results": [{"source": auto.ref(source), "pcm": auto.ref(copy_source), "frames": []}]})
        plan_review = job / "reviews" / "plan.json"
        auto.write(plan_review, {"decision": "pass"})
        clean = pending / "临时文件" / "manifests" / "clean_delivery.json"
        auto.write(clean, {"schema": "video-montage-autonomous-clean/v260929", "results": [{
            "plan_id": "P1", "output": auto.ref(clean_output), "render_evidence": auto.ref(render), "expected_text": "口播"}]})
        qc = job / "reports" / "clean_qc.json"
        auto.write(qc, {"schema": "video-montage-autonomous-clean-qc/v260929", "decision": "pass",
            "clean_delivery": auto.ref(clean), "results": [{"plan_id": "P1", "output": auto.ref(clean_output),
            "text_match": True, "asr": asr, "timing_asr": asr,
            "metrics": auto.audio_metrics(auto.pcm(clean_output)), "cut_pcm": []}]})
        for key, path in (("source_index", source_index), ("asset_copy", asset_copy), ("plan", plan),
                          ("plan_evidence", evidence), ("plan_review", plan_review), ("clean_delivery", clean), ("clean_qc", qc)):
            value[key] = auto.ref(path)
        auto.save(job, value, "clean_validated")
        return job, delivery, pending, asr, packager

    def final_review(self, job, **changes):
        value = auto.state(job)
        evidence = auto.read(Path(value["final_evidence"]["path"]))
        review = job / "reviews" / "final.json"
        auto.write(review, {"schema": auto.REVIEW_SCHEMA, "stage": "final", "reviewer_role": "codex",
            "evidence_sha256": value["final_evidence"]["sha256"], "outputs": [{"plan_id": "P1",
                "output_sha256": evidence["results"][0]["output"]["sha256"], "visual_pass": True,
                "subtitle_pass": True, "overlay_pass": True, "reason": "Test fixture", **changes}]})
        return review

    def approve_subtitle(self, job):
        value = auto.state(job)
        draft = auto.read(Path(value["subtitle_draft"]["path"]))["results"][0]
        path = job / "reviews" / "subtitle.json"
        auto.write(path, {"schema": "video-montage-codex-subtitle-review/v260929",
            "draft_sha256": value["subtitle_draft"]["sha256"], "asset_copy_sha256": value["asset_copy"]["sha256"],
            "results": [{"plan_id": "P1", "draft_subtitle_sha256": draft["subtitle_txt_sha256"], "cues": [{
                "index": 1, "draft_start_ms": 100, "draft_end_ms": 800, "before": "口播",
                "start_ms": 100, "end_ms": 800, "after": "口播"}]}]})
        auto.subtitle_review(SimpleNamespace(job_dir=job, review=path))

    def test_clean_only_completion_publishes_after_visual_approval(self):
        job, delivery, pending, asr, packager = self.fixture()
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr):
            auto.final_evidence(SimpleNamespace(job_dir=job, clean=True))
        self.assertEqual(b"previous video", (delivery / "混剪（无包装）" / "P1.mp4").read_bytes())
        auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        value = auto.state(job)
        self.assertEqual("complete", value["phase"])
        self.assertEqual("clean", value["delivery_mode"])
        self.assertFalse(pending.exists())
        auto.require_ref(value["outputs"][0], "published clean")
        self.assertEqual(50, packager.video_spec(Path(value["outputs"][0]["path"]))["frames"])
        self.assertEqual(60, packager.video_spec(job / "manifests" / "clean_inputs" / "P1.mp4")["frames"])
        self.assertFalse(list((delivery / "字幕").glob("subtitle-*.txt")))

    def test_rejected_final_review_leaves_both_delivered_videos_untouched(self):
        job, delivery, _, asr, _ = self.fixture()
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr):
            auto.final_evidence(SimpleNamespace(job_dir=job, clean=True))
        with self.assertRaisesRegex(RuntimeError, "incomplete visual finding"):
            auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job, visual_pass=False)))
        for folder in ("成片", "混剪（无包装）"):
            self.assertEqual(b"previous video", (delivery / folder / "P1.mp4").read_bytes())

    def test_compacted_packaged_job_reburns_and_completes_with_fresh_reviews(self):
        job, delivery, pending, asr, packager = self.fixture()
        subtitle = pending / "字幕" / "subtitle-P1.txt"
        subtitle.write_text("1\n00:00:00,100 --> 00:00:00,800\n口播\n", encoding="utf-8")
        snapshot = job / "evidence" / "draft.txt"
        shutil.copyfile(subtitle, snapshot)
        draft = job / "reports" / "subtitle_draft.json"
        value = auto.state(job)
        clean_row = auto.read(Path(value["clean_delivery"]["path"]))["results"][0]
        auto.write(draft, {"results": [{"plan_id": "P1", "input_path": clean_row["output"]["path"],
            "subtitle_txt_path": str(snapshot), "subtitle_txt_sha256": auto.sha(snapshot)}]})
        value["subtitle_draft"] = auto.ref(draft)
        auto.save(job, value, "subtitle_drafted")
        self.approve_subtitle(job)
        config = job / "initial_config.json"
        auto.write(config, {"schema": packager.SCHEMA, "outputs": [{"plan_id": "P1",
            "input_path": clean_row["output"]["path"], "input_sha256": clean_row["output"]["sha256"],
            "subtitle_txt": str(subtitle), "subtitle_sha256": auto.sha(subtitle)}]})
        auto.package(SimpleNamespace(job_dir=job, config=config))
        self.assertEqual(b"previous video", (delivery / "成片" / "P1.mp4").read_bytes())
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr):
            auto.final_evidence(SimpleNamespace(job_dir=job))
        auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        value = auto.state(job)
        self.assertTrue(value["compact_delivery"])
        self.assertFalse(config.exists())
        for key, reference in value["reburn_inputs"].items():
            auto.require_ref(reference, key)
        manifest_path = Path(value["reburn_inputs"]["packaging_delivery"]["path"])
        manifest = auto.read(manifest_path)
        self.assertEqual(1.2, manifest["results"][0]["final_speed"])
        self.assertEqual(60, manifest["results"][0]["input_frames"])
        self.assertEqual(50, packager.video_spec(delivery / "成片" / "P1.mp4")["frames"])
        self.assertEqual(manifest["config_snapshot_path"], manifest["config_path"])
        self.assertEqual("technical_pass_pending_review", packager.validate(manifest_path, job / "reports" / "check.json")["decision"])
        original = (delivery / "成片" / "P1.mp4").read_bytes()
        edited = delivery / "字幕" / "subtitle-P1.txt"
        auto.reburn(SimpleNamespace(job_dir=job, plan_id="P1", subtitle_txt=edited))
        self.assertEqual("subtitle_drafted", auto.state(job)["phase"])
        self.approve_subtitle(job)
        config = Path(auto.state(job)["reburn_config"]["path"])
        auto.package(SimpleNamespace(job_dir=job, config=config))
        self.assertEqual(original, (delivery / "成片" / "P1.mp4").read_bytes())
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr):
            auto.final_evidence(SimpleNamespace(job_dir=job))
        auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        self.assertEqual("complete", auto.state(job)["phase"])
        self.assertEqual(1, auto.state(job)["repair_round"])
        self.assertEqual(50, packager.video_spec(delivery / "成片" / "P1.mp4")["frames"])

    def test_publication_failure_rolls_back_previously_replaced_files(self):
        helper = auto.module("lifecycle_delivery_files", "scripts/packaging/scripts/delivery_files.py")
        copies = []
        for index in range(2):
            source, target = self.root / f"new-{index}", self.root / f"old-{index}"
            source.write_text("new")
            target.write_text("old")
            copies.append((source, target))
        original_replace = helper.os.replace
        def fail_second(source, target):
            if Path(target) == copies[1][1] and str(source).endswith(".publish"):
                raise OSError("second publication failed")
            return original_replace(source, target)
        with patch.object(helper.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second publication"):
                helper.publish_files(copies, self.root / "temporary")
        self.assertEqual(["old", "old"], [target.read_text() for _, target in copies])
        self.assertFalse(list(self.root.glob("*.publish")))

    def test_bad_source_intervals_and_case_aliases_are_rejected(self):
        registry = auto.read(auto.ROOT / "references/semantic/wuzimu-v20-invalid-intervals.json")
        entry = next(row for row in registry["entries"] if row["entry_id"] == "bad-wz09-silent-wave-before-speech")
        source = self.root / "renamed.mp4"
        segment = {"source_path": str(source), "source_sha256": entry["source_sha256"][0],
            "source_in_frame": 380, "speech_end_frame": 440, "source_out_frame_exclusive": 480,
            "source_fps_num": 60, "source_fps_den": 1, "text": "完整口播"}
        index = {"sources": [{"source": {"path": str(source), "sha256": segment["source_sha256"]},
            "video": {"fps_num": 60, "fps_den": 1, "frames": 2000}}]}
        plan = {"schema": auto.PLAN_SCHEMA, "outputs": [{"plan_id": pid, "segments": [segment]} for pid in ("P1", "p1")]}
        errors = auto.check_plan(plan, index, 2)
        self.assertIn("duplicate plan ID", errors)
        self.assertTrue(any(entry["entry_id"] in error for error in errors))

    def test_coalesced_shot_maps_all_original_speech_and_subtitles(self):
        planned = {"segments": [{"text": "开局"}, {"text": "点燃熔炉"}]}
        rendered = {"segments": [{"expected_output_frames": 120, "source_segment_indexes": [1, 2]}]}
        self.assertEqual([{"text": "开局点燃熔炉"}], auto.rendered_plan_segments(planned, rendered))
        self.assertEqual([(0, 2000)], auto.subtitle_segment_bounds([{"text": "开局点燃熔炉"}], planned, rendered))
        rendered["segments"][0]["source_segment_indexes"] = [1, 1]
        with self.assertRaisesRegex(ValueError, "incomplete or replayed"):
            auto.rendered_plan_segments(planned, rendered)


if __name__ == "__main__":
    unittest.main()
