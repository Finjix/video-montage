"""Speech review regressions. Text/audio here are synthetic fixtures, not approvals."""
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("speech_review_tests", Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class SpeechReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name)
        self.source = self.job / "source.mp4"
        self.source.write_bytes(b"synthetic source")
        self.audio = self.job / "audio.wav"
        self.audio.write_bytes(b"synthetic audio observation")
        self.raw_text = "不干不克欢迎无尽东日"
        self.correct = "不肝不氪欢迎无尽冬日"
        self.raw_path = self.job / "raw.json"
        a.write(self.raw_path, {"asr": self.asr(self.raw_text)})
        asset = self.job / "copy.txt"
        asset.write_text("不肝不氪欢迎无尽冬日", encoding="utf-8")
        self.copy_path = self.job / "copy.json"
        a.write(self.copy_path, {"assets": [{**a.ref(asset), "text": self.correct}]})
        self.row = {"source": a.ref(self.source), "asr": a.ref(self.raw_path),
                    "video": {"frames": 120, "fps_num": 60, "fps_den": 1}}
        self.index_path = self.job / "index.json"
        a.write(self.index_path, {"planning_policy": a.EDITING.POLICY, "sources": [self.row]})
        self.order_path = self.job / "order.json"
        a.write(self.order_path, {"schema": a.ORDER_SCHEMA, "planning_policy": a.EDITING.POLICY, "requested_outputs": 1})
        self.value = {"schema": a.STATE_SCHEMA, "review_mode": "codex_asr_pcm", "planning_policy": a.EDITING.POLICY,
                      "source_index": a.ref(self.index_path), "asset_copy": a.ref(self.copy_path),
                      "work_order": a.ref(self.order_path), "repair_round": 0}
        a.save(self.job, self.value, "copy_indexed")

    def asr(self, text):
        return {"text": text, "segments": ([{"text": text, "start": .1, "end": 1.8,
                    "words": [{"word": text, "start": .1, "end": 1.8}]}] if text else [])}

    def finding(self, before, after, audio=None):
        corrections = a.SPEECH.changes(before, after, a.normalized)
        for c in corrections:
            c.update(start_ms=100, end_ms=1800, reason="Synthetic fixture: source context and verified game-name copy support this spelling.",
                     evidence=[{"kind": "source_asr", "sha256": a.ref(self.raw_path)["sha256"], "text": self.raw_text}])
        return {"audio_sha256": (audio or a.ref(self.audio))["sha256"], "observed_text": before,
                "speech_pass": True, "reason": "Synthetic fixture: reviewed the complete context, including the recognition mismatch.",
                "timing_reason": "Synthetic fixture: words remain within this bound audio range.",
                "reviewed_asr": self.asr(after), "corrections": corrections}

    def source_review(self, *, real_audio=False):
        if real_audio:
            a.run([str(a.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x48:r=60:d=2",
                   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
                   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-y", str(self.source)])
            self.row["source"] = a.ref(self.source)
            a.write(self.index_path, {"planning_policy": a.EDITING.POLICY, "sources": [self.row]})
            self.value["source_index"] = a.ref(self.index_path)
            a.save(self.job, self.value, "copy_indexed")
        def write_audio(command):
            Path(command[-1]).write_bytes(self.audio.read_bytes())
        with patch("builtins.print"), (patch.object(a, "run", side_effect=write_audio) if not real_audio else patch("builtins.print")):
            a.source_speech_evidence(SimpleNamespace(job_dir=self.job))
        value = a.state(self.job)
        evidence = a.read(Path(value["source_speech_evidence"]["path"]))
        review = {"schema": a.SPEECH.SCHEMA, "stage": "source", "reviewer_role": "codex",
                  "evidence_sha256": value["source_speech_evidence"]["sha256"], "asset_copy_sha256": value["asset_copy"]["sha256"],
                  "results": [{"source_sha256": self.row["source"]["sha256"],
                               **self.finding(self.raw_text, self.correct, evidence["sources"][0]["audio"])}]}
        path = self.job / "source-review.json"
        a.write(path, review)
        a.source_speech_review(SimpleNamespace(job_dir=self.job, review=path))
        index = a.read(Path(a.state(self.job)["source_index"]["path"]))
        return index, review, path

    def test_general_source_correction_keeps_raw_asr_immutable(self):
        raw_ref = a.ref(self.raw_path)
        index, _, _ = self.source_review()
        self.assertEqual(raw_ref, a.ref(self.raw_path))
        self.assertEqual(self.raw_text, a.read(self.raw_path)["asr"]["text"])
        self.assertEqual(self.correct, a.source_speech(index["sources"][0])["text"])
        self.assertEqual(a.SPEECH.POLICY, a.state(self.job)["speech_review_policy"])

    def test_real_ffmpeg_source_evidence_and_review_command(self):
        index, _, _ = self.source_review(real_audio=True)
        self.assertEqual(self.correct, a.source_speech(index["sources"][0])["text"])

    def test_missing_or_changed_source_review_fails_closed(self):
        index, _, _ = self.source_review()
        artifact = a.read(Path(index["sources"][0]["reviewed_speech"]["path"]))
        Path(artifact["review"]["path"]).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            a.source_speech(index["sources"][0])

    def test_reviewed_words_may_change_count_but_need_all_changes(self):
        texts = a.SPEECH.context(a.speech_api(), {"sources": [self.row]}, a.ref(self.copy_path))
        before = "欢迎无尽东日谢谢观看"
        after = "欢迎来到无尽冬日"
        f = self.finding(before, after)
        self.assertEqual(after, a.SPEECH.validate(a.speech_api(), f, {"text": before}, a.ref(self.audio), 2, texts)["text"])
        f["corrections"].pop()
        with self.assertRaisesRegex(ValueError, "every ASR change"):
            a.SPEECH.validate(a.speech_api(), f, {"text": before}, a.ref(self.audio), 2, texts)

    def test_hallucination_deletion_is_explicit_and_can_leave_source_silent(self):
        texts = a.SPEECH.context(a.speech_api(), {"sources": [self.row]}, a.ref(self.copy_path))
        f = self.finding(self.raw_text, "")
        self.assertEqual("", a.SPEECH.validate(a.speech_api(), f, {"text": self.raw_text}, a.ref(self.audio), 2, texts, allow_empty=True)["text"])
        with self.assertRaises(ValueError):
            a.SPEECH.validate(a.speech_api(), f, {"text": self.raw_text}, a.ref(self.audio), 2, texts)

    def test_hash_scope_time_and_evidence_are_mandatory(self):
        texts = a.SPEECH.context(a.speech_api(), {"sources": [self.row]}, a.ref(self.copy_path))
        good = self.finding(self.raw_text, self.correct)
        bads = []
        for key, val in (("audio_sha256", "wrong"), ("observed_text", "edited raw"), ("speech_pass", False), ("timing_reason", "")):
            bad = copy.deepcopy(good); bad[key] = val; bads.append(bad)
        for modify in (lambda x: x["corrections"][0].update(evidence=[]),
                       lambda x: x["corrections"][0]["evidence"][0].update(text="invented advertising claim"),
                       lambda x: x["reviewed_asr"]["segments"][0]["words"][0].update(end=3),
                       lambda x: x["reviewed_asr"]["segments"][0]["words"][0].update(word="missing coverage")):
            bad = copy.deepcopy(good); modify(bad); bads.append(bad)
        for bad in bads:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                a.SPEECH.validate(a.speech_api(), bad, {"text": self.raw_text}, a.ref(self.audio), 2, texts)

    def test_source_range_cannot_use_another_occurrence_or_cut_a_word(self):
        segment = {"source_in_frame": 0, "source_out_frame_exclusive": 120, "source_fps_num": 60, "source_fps_den": 1}
        self.assertEqual(a.normalized(self.correct), a.SPEECH.range_text(a.speech_api(), self.asr(self.correct), segment))
        segment["source_in_frame"] = 60
        with self.assertRaisesRegex(ValueError, "truncates reviewed word"):
            a.SPEECH.range_text(a.speech_api(), self.asr(self.correct), segment)

    def test_plan_cannot_drop_review_or_revert_to_raw_asr(self):
        index, _, _ = self.source_review()
        with self.assertRaisesRegex(ValueError, "plan speech review policy"):
            a.require_speech_policy(a.state(self.job), index, {})
        unbound = {"sources": index["sources"]}
        with self.assertRaisesRegex(ValueError, "without its bound policy"):
            a.require_speech_policy({}, unbound, {})
        index["sources"][0].pop("reviewed_speech")
        with self.assertRaisesRegex(ValueError, "source speech review required"):
            a.require_speech_policy(a.state(self.job), index, {"speech_review_policy": a.SPEECH.POLICY})

    def test_candidate_asr_mismatch_requires_audio_bound_review(self):
        index, _, _ = self.source_review()
        value = a.state(self.job)
        segment = {"text": self.correct, "source_in_frame": 0, "source_out_frame_exclusive": 120,
                   "source_fps_num": 60, "source_fps_den": 1}
        plan = self.job / "plan.json"
        a.write(plan, {"speech_review_policy": a.SPEECH.POLICY, "outputs": [{"plan_id": "P1", "segments": [segment]}]})
        value["plan"] = a.ref(plan)
        evidence = {"speech_review_policy": a.SPEECH.POLICY, "results": [{"plan_id": "P1", "segment_index": 1,
                    "asr": self.asr(self.raw_text), "pcm": a.ref(self.audio), "expected_text": self.correct, "source_text_match": True}]}
        review = {"speech_review_policy": a.SPEECH.POLICY, "segments": [{"plan_id": "P1", "segment_index": 1,
                  "speech_review": self.finding(self.raw_text, self.correct)}]}
        a.require_candidate_speech(value, evidence, review)
        review["segments"][0].pop("speech_review")
        with self.assertRaisesRegex(ValueError, "explicit audio-bound"):
            a.require_candidate_speech(value, evidence, review)

    def clean_fixture(self):
        self.source_review()
        value = a.state(self.job)
        plan_path = self.job / "plan.json"
        output = {"plan_id": "P1", "segments": [{"text": self.correct}]}
        a.write(plan_path, {"speech_review_policy": a.SPEECH.POLICY, "outputs": [output]})
        observation = {"shot_index": 1, "audio": a.ref(self.audio), "asr": self.asr(self.raw_text),
                       "expected_text": self.correct, "output_in_frame": 0, "output_out_frame_exclusive": 120}
        evidence_path = self.job / "clean-evidence.json"
        a.write(evidence_path, {"policy": a.SPEECH.POLICY, "plan": a.ref(plan_path), "source_index": value["source_index"],
                "asset_copy": value["asset_copy"], "results": [{"plan_id": "P1", "output_sha256": "output-hash",
                "actual_output_frames": 120, "observations": [observation]}]})
        review_path = self.job / "clean-review.json"
        finding = {"shot_index": 1, **self.finding(self.raw_text, self.correct)}
        a.write(review_path, {"schema": a.SPEECH.SCHEMA, "stage": "clean", "reviewer_role": "codex",
                 "evidence_sha256": a.sha(evidence_path), "asset_copy_sha256": value["asset_copy"]["sha256"],
                 "results": [{"plan_id": "P1", "shots": [finding]}]})
        texts = a.SPEECH.context(a.speech_api(), a.read(Path(value["source_index"]["path"])), value["asset_copy"])
        timing = a.reviewed_shot_timing([observation], [finding], texts)
        output_ref = {"path": "actual-output.mp4", "sha256": "output-hash"}
        timing.update(output=output_ref, timing_basis="verified_rendered_shot_asr", review_basis=a.SPEECH.POLICY,
                      review_context={"evidence": a.ref(evidence_path), "review": a.ref(review_path)})
        rendered = {"actual_output_frames": 120, "segments": [{"expected_output_frames": 120}]}
        return timing, output_ref, output, rendered

    def test_clean_review_uses_corrected_words_keeps_raw_and_binds_output(self):
        timing, output_ref, output, rendered = self.clean_fixture()
        self.assertEqual(self.raw_text, timing["shot_observations"][0]["asr"]["text"])
        self.assertEqual(self.correct, timing["text"])
        self.assertTrue(a.verified_shot_speech(timing, output_ref, output, rendered, {}))
        packaging = a.module("speech_caption_test", "scripts/packaging/scripts/package_video.py")
        captions = packaging.verified_caption_rows(timing)
        self.assertEqual(a.normalized(self.correct), a.normalized("".join(r["text"] for r in captions)))
        self.assertEqual(self.raw_text, timing["shot_observations"][0]["asr"]["text"])
        changed = dict(output_ref, sha256="replaced output")
        timing["output"] = changed
        with self.assertRaisesRegex(ValueError, "output hash/frame"):
            a.verified_shot_speech(timing, changed, output, rendered, {})

    def test_clean_review_cannot_skip_shots_or_edit_compiled_timing(self):
        timing, output_ref, output, rendered = self.clean_fixture()
        timing["segments"][0]["words"][0]["start"] = .2
        self.assertFalse(a.verified_shot_speech(timing, output_ref, output, rendered, {}))
        with self.assertRaisesRegex(ValueError, "every rendered shot"):
            a.reviewed_shot_timing(timing["shot_observations"], [], {})
        packaging = a.module("missing_speech_caption_test", "scripts/packaging/scripts/package_video.py")
        timing["shot_observations"][0].pop("reviewed_asr")
        with self.assertRaisesRegex(ValueError, "every shot"):
            packaging.verified_caption_rows(timing)

    def test_compaction_retains_review_raw_and_audio_dependencies(self):
        timing, _, _, _ = self.clean_fixture()
        keep = set()
        a.SPEECH.retain_refs(a.speech_api(), timing["review_context"], self.job, keep)
        self.assertIn(self.raw_path, keep)
        self.assertIn(self.audio, keep)
        self.assertIn(Path(a.state(self.job)["source_index"]["path"]), keep)
        # compact_delivery initially marks every manifest for retention. That
        # must not skip traversing a manifest's evidence and review dependencies.
        index = a.read(Path(a.state(self.job)["source_index"]["path"]))
        artifact_ref = index["sources"][0]["reviewed_speech"]
        artifact = a.read(Path(artifact_ref["path"]))
        keep = {Path(artifact_ref["path"])}
        a.SPEECH.retain_refs(a.speech_api(), artifact_ref, self.job, keep)
        self.assertIn(Path(artifact["review"]["path"]), keep)
        self.assertIn(Path(artifact["evidence"]["path"]), keep)

    def test_clean_qc_waits_for_review_then_uses_reviewed_timing(self):
        self.source_review(real_audio=True)
        value = a.state(self.job)
        plan_path = self.job / "plan.json"
        segment = {"source_path": str(self.source), "source_sha256": a.sha(self.source), "text": self.correct,
                   "source_in_frame": 0, "source_out_frame_exclusive": 120, "source_fps_num": 60, "source_fps_den": 1}
        a.write(plan_path, {"schema": a.PLAN_SCHEMA, "planning_policy": a.EDITING.POLICY,
                           "speech_review_policy": a.SPEECH.POLICY, "outputs": [{"plan_id": "P1", "segments": [segment]}]})
        render_path = self.job / "render.json"
        a.write(render_path, {"actual_output_frames": 120, "segments": [{"expected_output_frames": 120}]})
        clean_path = self.job / "clean.json"
        a.write(clean_path, {"results": [{"plan_id": "P1", "output": a.ref(self.source),
                              "render_evidence": a.ref(render_path), "expected_text": self.correct}]})
        value.update(plan=a.ref(plan_path), clean_delivery=a.ref(clean_path))
        a.save(self.job, value, "clean_rendered")
        # Duration/visual editorial gates are tested elsewhere. Exercise actual
        # audio extraction and the new pending -> reviewed state transition here.
        with patch.object(a, "require_editing_approval"), patch.object(a, "semantic_first", return_value=False), \
             patch.object(a, "load_model"), patch.object(a, "transcribe", return_value=self.asr(self.raw_text)) as recognizer, patch("builtins.print"):
            a.clean_qc(SimpleNamespace(job_dir=self.job))
        pending = a.state(self.job)
        self.assertEqual("clean_speech_pending", pending["phase"])
        self.assertEqual(2, recognizer.call_count)
        self.assertTrue(all(len(c.args) == 2 for c in recognizer.call_args_list))
        self.assertEqual("pending_speech_review", a.read(Path(pending["clean_qc"]["path"]))["decision"])
        with self.assertRaisesRegex(ValueError, "clean QC"):
            a.subtitle_draft(SimpleNamespace(job_dir=self.job, subtitle_dir=None))
        evidence = a.read(Path(pending["clean_speech_evidence"]["path"]))
        obs = evidence["results"][0]["observations"][0]
        review_path = self.job / "review-clean-command.json"
        a.write(review_path, {"schema": a.SPEECH.SCHEMA, "stage": "clean", "reviewer_role": "codex",
                  "evidence_sha256": pending["clean_speech_evidence"]["sha256"], "asset_copy_sha256": pending["asset_copy"]["sha256"],
                  "results": [{"plan_id": "P1", "shots": [{"shot_index": 1, **self.finding(self.raw_text, self.correct, obs["audio"])}]}]})
        # A rejected review may be repaired against the same immutable evidence;
        # there is no need to rerun recognition just to correct the review file.
        a.save(self.job, pending, "repair_required")
        with patch.object(a, "require_editing_approval"):
            a.clean_speech_review(SimpleNamespace(job_dir=self.job, review=review_path))
        completed = a.state(self.job)
        self.assertEqual("clean_validated", completed["phase"])
        qc = a.read(Path(completed["clean_qc"]["path"]))
        self.assertEqual("pass", qc["decision"])
        self.assertEqual(self.raw_text, qc["results"][0]["asr"]["text"])
        self.assertEqual(self.correct, qc["results"][0]["timing_asr"]["text"])

    def test_source_speech_policy_cannot_be_dropped_from_state(self):
        self.source_review()
        value = a.state(self.job)
        value.pop("speech_review_policy")
        a.write(self.job / "autonomous_state.json", value)
        with self.assertRaisesRegex(ValueError, "speech review policy"):
            a.state(self.job)

    def test_compact_copy_relocation_keeps_the_same_bound_content(self):
        index, _, _ = self.source_review()
        value = a.state(self.job)
        relocated = self.job / "manifests" / "reburn_asset_copy.json"
        relocated.write_bytes(self.copy_path.read_bytes())
        value["asset_copy"] = a.ref(relocated)
        a.require_speech_policy(value, index, {"speech_review_policy": a.SPEECH.POLICY})
        a.write(relocated, {"assets": []})
        value["asset_copy"] = a.ref(relocated)
        with self.assertRaisesRegex(ValueError, "asset-copy binding"):
            a.require_speech_policy(value, index, {"speech_review_policy": a.SPEECH.POLICY})

    def test_empty_recognition_can_be_reviewed_with_bound_audio_not_a_fake_quote(self):
        finding = self.finding("", self.correct)
        finding["corrections"][0]["evidence"] = [{"kind": "source_audio", "sha256": a.sha(self.audio),
                                                 "start_ms": 100, "end_ms": 1800}]
        self.assertEqual(self.correct, a.SPEECH.validate(a.speech_api(), finding, {"text": ""}, a.ref(self.audio), 2, {})["text"])
        finding["corrections"][0]["evidence"][0]["end_ms"] = 1900
        with self.assertRaisesRegex(ValueError, "source-audio hash/range"):
            a.SPEECH.validate(a.speech_api(), finding, {"text": ""}, a.ref(self.audio), 2, {})


if __name__ == "__main__":
    unittest.main()
