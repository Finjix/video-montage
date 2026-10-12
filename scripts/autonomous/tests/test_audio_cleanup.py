"""Signal, review, rendering and legacy-compatibility tests for spoken cleanup."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cleanup_test_auto", ROOT / "scripts/autonomous/scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)
audio = auto.AUDIO
renderer = auto.module("cleanup_test_renderer", "scripts/semantic/scripts/portable_frame_renderer.py")


class AudioCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.segment = {"source_path": "source.mp4", "source_sha256": "a" * 64, "source_in_frame": 0,
                        "speech_end_frame": 50, "source_out_frame_exclusive": 60,
                        "source_fps_num": 60, "source_fps_den": 1, "text": "口播"}
        self.asr = {"segments": [{"words": [{"word": "前句", "start": .08, "end": .22},
                                            {"word": "口播", "start": .5, "end": .8}]}]}
        self.samples = np.zeros((48000, 2), dtype=np.float32)
        tone = np.sin(np.arange(4800) * 2 * np.pi * 700 / 48000) * .08
        self.samples[4800:9600, 0] = tone
        self.samples[4800:9600, 1] = -tone
        self.samples[24000:38400, :] = .05
        self.evidence = audio.scan(self.samples, 0, self.segment, self.asr, np.ones(32) * .9)
        pcm = self.root / "context.wav"
        audio.write_pcm(pcm, self.samples)
        preview = self.root / "waveform.png"
        audio.draw_waveform(self.evidence, preview)
        self.evidence.update(context_pcm=audio.file_ref(pcm), waveform_preview=audio.file_ref(preview), source_asr_digest=audio.digest(self.asr))
        self.evidence["evidence_digest"] = audio.digest(self.evidence)
        self.review = {"policy": audio.POLICY, "evidence_digest": self.evidence["evidence_digest"],
                       "natural_expression_preserved": True,
                       "protected_speech_ranges": [{k: w[k] for k in ("word_index", "text", "start_sample", "end_sample")}
                                                    for w in self.evidence["target_words"]],
                       "decisions": [{"candidate_id": r["candidate_id"], "classification": "previous_next_speech",
                                      "action": "mute", "reason": "source context identifies the preceding word",
                                      "start_sample": r["start_sample"], "end_sample": r["end_sample"]}
                                     for r in self.evidence["candidates"]]}

    def test_detects_previous_syllable_after_old_40ms_window_even_with_high_vad(self):
        self.assertEqual(1, len(self.evidence["candidates"]))
        event = self.evidence["candidates"][0]
        self.assertGreater(event["start_sample"], 1920)
        self.assertEqual("previous_next_speech", event["suggested_class"])
        self.assertFalse(event["automatic_mute"])
        self.assertGreater(event["max_speech_probability"], .5)
        self.assertEqual(1, len(audio.compile_review(self.evidence, self.review, self.segment)["mute_ranges"]))

    def test_natural_sounds_are_retained_and_cannot_be_muted(self):
        for kind in ("natural_expression", "valid_speech"):
            with self.subTest(kind=kind):
                review = copy.deepcopy(self.review)
                review["decisions"][0].update(classification=kind, action="retain", reason="breath, laugh or soft spoken consonant")
                self.assertEqual([], audio.compile_review(self.evidence, review, self.segment)["mute_ranges"])
                review["decisions"][0]["action"] = "mute"
                with self.assertRaisesRegex(ValueError, "cannot mute"):
                    audio.compile_review(self.evidence, review, self.segment)

    def test_uncertain_missing_and_stale_reviews_reject(self):
        for change in ("uncertain", "missing", "stale"):
            with self.subTest(change=change):
                review = copy.deepcopy(self.review)
                if change == "uncertain": review["decisions"][0]["classification"] = "uncertain"
                if change == "missing": review["decisions"] = []
                if change == "stale": review["evidence_digest"] = "old"
                with self.assertRaises(ValueError): audio.compile_review(self.evidence, review, self.segment)

    def test_low_noise_head_tail_and_internal_events_are_scanned(self):
        for position in (.001, .3, .9):
            samples = np.zeros((48000, 1), dtype=np.float32)
            start = round(position * 48000)
            samples[start:min(48000, start + 2400)] = .004
            result = audio.scan(samples, 0, self.segment, self.asr, np.zeros(32))
            self.assertTrue(result["candidates"], position)

    def test_soft_words_and_unrecognized_text_never_authorize_mutes(self):
        samples = np.ones((48000, 1), dtype=np.float32) * .001
        result = audio.scan(samples, 0, self.segment, self.asr, np.zeros(32))
        self.assertEqual([], result["candidates"])
        self.assertEqual("口播", result["target_words"][0]["text"])
        result = copy.deepcopy(self.evidence)
        result["alignment_status"] = "unresolved"
        result["evidence_digest"] = audio.digest({k: v for k, v in result.items() if k != "evidence_digest"})
        review = dict(self.review, evidence_digest=result["evidence_digest"])
        with self.assertRaisesRegex(ValueError, "alignment unresolved"):
            audio.compile_review(result, review, self.segment)

    def test_protected_speech_overlap_and_forged_parameters_reject(self):
        review = copy.deepcopy(self.review)
        review["protected_speech_ranges"][0]["start_sample"] = 4800
        review["protected_speech_ranges"][0]["boundary_reason"] = "local waveform valley"
        with self.assertRaisesRegex(ValueError, "intersects protected"):
            audio.compile_review(self.evidence, review, self.segment)
        cleanup = audio.compile_review(self.evidence, self.review, self.segment)
        cleanup["mute_ranges"][0]["end_sample"] += 10
        with self.assertRaisesRegex(ValueError, "authorization changed"):
            audio.filter_chain(cleanup, self.segment)

    def test_evidence_bytes_and_transcript_binding_reject(self):
        Path(self.evidence["context_pcm"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            audio.compile_review(self.evidence, self.review, self.segment)

    def test_conflicting_timestamp_needs_supported_boundary_correction(self):
        asr = copy.deepcopy(self.asr)
        asr["segments"][0]["words"][1]["start"] = .18
        evidence = audio.scan(self.samples, 0, self.segment, asr, np.ones(32) * .9)
        evidence.update(context_pcm=self.evidence["context_pcm"], waveform_preview=self.evidence["waveform_preview"])
        evidence["evidence_digest"] = audio.digest(evidence)
        review = copy.deepcopy(self.review)
        review["evidence_digest"] = evidence["evidence_digest"]
        review["protected_speech_ranges"][0]["start_sample"] = 8640
        with self.assertRaisesRegex(ValueError, "intersects protected"):
            audio.compile_review(evidence, review, self.segment)
        review["protected_speech_ranges"][0]["start_sample"] = 24000
        with self.assertRaisesRegex(ValueError, "unsupported word boundary"):
            audio.compile_review(evidence, review, self.segment)
        review["protected_speech_ranges"][0]["boundary_reason"] = "full-context word sequence and intervening signal valley separate previous word"
        self.assertEqual(1, len(audio.compile_review(evidence, review, self.segment)["mute_ranges"]))

    def test_active_workflow_rechecks_review_and_source_transcript(self):
        plan = {"audio_cleanup_policy": audio.POLICY, "outputs": [{"plan_id": "P1", "segments": [
            {**self.segment, "audio_cleanup_policy": audio.POLICY}]}]}
        evidence = {"audio_cleanup_policy": audio.POLICY, "results": [{"plan_id": "P1", "segment_index": 1, "audio_cleanup": self.evidence}]}
        review = {"audio_cleanup_policy": audio.POLICY, "reviewer_role": "codex", "segments": [{"plan_id": "P1", "segment_index": 1, "audio_cleanup": self.review}]}
        for name, doc in (("plan", plan), ("asr", {"asr": self.asr})):
            auto.write(self.root / f"{name}.json", doc)
        index = {"sources": [{"source": {"sha256": self.segment["source_sha256"]}, "asr": auto.ref(self.root / "asr.json")}]}
        auto.write(self.root / "index.json", index)
        value = {"audio_cleanup_policy": audio.POLICY, "plan": auto.ref(self.root / "plan.json"), "source_index": auto.ref(self.root / "index.json")}
        evidence["plan"] = value["plan"]
        auto.write(self.root / "evidence.json", evidence)
        value["plan_evidence"] = auto.ref(self.root / "evidence.json")
        review.update(evidence_sha256=value["plan_evidence"]["sha256"], plan_sha256=value["plan"]["sha256"])
        auto.write(self.root / "review.json", review)
        value["plan_review"] = auto.ref(self.root / "review.json")
        segments = auto.compile_audio_segments(value, plan, evidence, review)
        artifact = {"policy": audio.POLICY, "plan": value["plan"], "plan_evidence": value["plan_evidence"], "plan_review": value["plan_review"], "segments": segments}
        auto.write(self.root / "authorization.json", artifact)
        value["audio_cleanup_authorization"] = auto.ref(self.root / "authorization.json")
        auto.require_audio_approval(value)
        auto.audio_render_check(value, "P1", {"segments": [{"audio_cleanup": segments[0]["audio_cleanup"]}]})
        with self.assertRaisesRegex(ValueError, "did not use"):
            auto.audio_render_check(value, "P1", {"segments": [{}]})
        review["segments"][0]["audio_cleanup"]["decisions"][0]["action"] = "retain"
        auto.write(self.root / "review.json", review)
        with self.assertRaisesRegex(ValueError, "changed"):
            auto.require_audio_approval(value)

    def test_fractional_fps_uses_exact_source_sample_origin(self):
        segment = dict(self.segment, source_in_frame=1110, source_out_frame_exclusive=1377, source_fps_num=60000, source_fps_den=1001)
        self.assertEqual((888888, 1102702), audio.bounds(segment))

    def test_cleanup_does_not_coalesce_and_legacy_still_coalesces(self):
        left = dict(self.segment, source_out_frame_exclusive=30, speech_end_frame=25)
        right = dict(self.segment, source_in_frame=30)
        self.assertEqual(1, len(renderer.coalesce_segments([left, right])))
        left["audio_cleanup_policy"] = audio.POLICY
        self.assertEqual(2, len(renderer.coalesce_segments([left, right])))
        self.assertEqual([], auto.audio_plan_errors({}, {"outputs": [{"segments": [self.segment]}]}))
        with patch.object(auto, "read", side_effect=AssertionError("legacy must not load new receipts")):
            auto.require_audio_approval({})

    def test_final_speed_mapping_and_qc_reject_unchanged_noise(self):
        cleanup = audio.compile_review(self.evidence, self.review, self.segment)
        rendered = {"segments": [{"audio_cleanup": cleanup, "speed": 1, "expected_output_frames": 60}]}
        original = audio.output_ranges(rendered)[0]
        sped = audio.output_ranges(rendered, 1.2)[0]
        self.assertAlmostEqual(original["output_start_sample"] / 1.2, sped["output_start_sample"], delta=1)
        self.assertEqual("reject", audio.verify_output(self.samples, rendered)["decision"])
        clean = self.samples.copy()
        mute = cleanup["mute_ranges"][0]
        clean[mute["start_sample"]:mute["end_sample"]] = 0
        self.assertEqual("pass", audio.verify_output(clean, rendered)["decision"])

    def test_short_click_uses_available_interior_instead_of_skipping_qc(self):
        cleanup = audio.compile_review(self.evidence, self.review, self.segment)
        cleanup["mute_ranges"][0].update(start_sample=4800, end_sample=6240)
        cleanup["authorization_digest"] = audio.digest({k: v for k, v in cleanup.items() if k != "authorization_digest"})
        rendered = {"segments": [{"audio_cleanup": cleanup, "speed": 1, "expected_output_frames": 60}]}
        samples = np.zeros((48000, 2), dtype=np.float32)
        self.assertEqual("pass", audio.verify_output(samples, rendered)["decision"])
        samples[5280:5760] = .1
        self.assertEqual("reject", audio.verify_output(samples, rendered)["decision"])

    def test_real_ffmpeg_mutes_both_channels_and_keeps_frames(self):
        source_audio = self.root / "source.wav"
        audio.write_pcm(source_audio, self.samples)
        source = self.root / "source.mp4"
        subprocess.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x284:r=60:d=1",
            "-i", str(source_audio), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-y", str(source)], check=True, capture_output=True)
        self.segment.update(source_path=str(source), source_sha256=auto.sha(source))
        self.evidence["source_sha256"] = self.segment["source_sha256"]
        self.evidence["evidence_digest"] = audio.digest({k: v for k, v in self.evidence.items() if k != "evidence_digest"})
        self.review["evidence_digest"] = self.evidence["evidence_digest"]
        cleanup = audio.compile_review(self.evidence, self.review, self.segment)
        plan = self.root / "plan.json"
        auto.write(plan, {"segments": [{**self.segment, "audio_cleanup_policy": audio.POLICY, "audio_cleanup": cleanup}]})
        out = self.root / "out.mp4"
        rendered = renderer.render(plan, out, self.root / "render.json", 160, 284, 60, "libx264")
        self.assertEqual(60, rendered["actual_output_frames"])
        decoded = audio.decode(auto.FFMPEG, out)
        self.assertEqual(2, decoded.shape[1])
        self.assertEqual("pass", audio.verify_output(decoded, rendered)["decision"])
        self.assertGreater(float(np.max(np.abs(decoded[25000:36000]))), .025)
        self.assertEqual(cleanup, rendered["segments"][0]["audio_cleanup"])


if __name__ == "__main__":
    unittest.main()
