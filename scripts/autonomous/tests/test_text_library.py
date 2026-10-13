"""Fixed spelling regressions; synthetic speech fixtures are not listening approvals."""
import copy
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_speech_review as fixtures

a = fixtures.a


class TextLibraryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SpeechReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.job = self.fixture.job
        self.path = self.job / "config/text_library.json"
        a.write(self.path, a.read(a.ROOT / "assets/text-library.json"))
        self.library = a.ref(self.path)
        self.raw = "欢迎无尽东日我是吴岳"
        self.correct = "欢迎无尽冬日我是吴樾"
        a.write(self.fixture.raw_path, {"asr": self.fixture.asr(self.raw)})
        self.fixture.row.update(asr=a.ref(self.fixture.raw_path), text_library=self.library)
        a.write(self.fixture.index_path, {"planning_policy": a.EDITING.POLICY, "text_library": self.library,
                                         "sources": [self.fixture.row]})
        order = a.read(self.fixture.order_path)
        order.update(text_library=self.library, speech_review_policy=a.SPEECH.POLICY)
        a.write(self.fixture.order_path, order)
        value = self.fixture.value
        value.update(text_library=self.library, speech_review_policy=a.SPEECH.POLICY,
                     work_order=a.ref(self.fixture.order_path), source_index=a.ref(self.fixture.index_path))
        a.save(self.job, value, "copy_indexed")

    def finding(self, before=None, after=None):
        before = self.raw if before is None else before
        after = self.correct if after is None else after
        corrections = a.SPEECH.changes(before, after, a.normalized)
        terms = {"冬": ("game:wu-jin-dong-ri", "无尽冬日"), "樾": ("person:wu-yue", "吴樾")}
        for change in corrections:
            change.update(start_ms=100, end_ms=1800, reason="Synthetic source context confirms the fixed spelling.",
                          evidence=[{"kind": "source_audio", "sha256": a.sha(self.fixture.audio),
                                     "start_ms": 100, "end_ms": 1800}])
            if change["after"] in terms:
                identity, text = terms[change["after"]]
                change["evidence"].append({"kind": "text_library", "sha256": self.library["sha256"],
                                           "term_id": identity, "text": text})
        return {"audio_sha256": a.sha(self.fixture.audio), "observed_text": before, "speech_pass": True,
                "reason": "Synthetic fixture: the complete original context identifies these terms.",
                "timing_reason": "Synthetic fixture: timings remain within the actual bound range.",
                "reviewed_asr": self.fixture.asr(after), "corrections": corrections}

    def context(self):
        return a.SPEECH.context(a.speech_api(), {"sources": [self.fixture.row]},
                                a.ref(self.fixture.copy_path), self.library)

    def validate(self, finding):
        return a.SPEECH.validate(a.speech_api(), finding, {"text": finding["observed_text"]},
                                 a.ref(self.fixture.audio), 2, self.context())

    def source_review(self):
        def audio(command):
            Path(command[-1]).write_bytes(self.fixture.audio.read_bytes())
        with patch.object(a, "run", side_effect=audio), patch("builtins.print"):
            a.source_speech_evidence(SimpleNamespace(job_dir=self.job))
        value = a.state(self.job)
        review = {"schema": a.SPEECH.SCHEMA, "stage": "source", "reviewer_role": "codex",
                  "evidence_sha256": value["source_speech_evidence"]["sha256"],
                  "asset_copy_sha256": value["asset_copy"]["sha256"],
                  "text_library_sha256": self.library["sha256"],
                  "results": [{"source_sha256": self.fixture.row["source"]["sha256"], **self.finding()}]}
        path = self.job / "reviews/source.json"
        a.write(path, review)
        a.source_speech_review(SimpleNamespace(job_dir=self.job, review=path))
        return a.state(self.job)

    def test_init_and_prepare_snapshot_library_without_changing_recognizer_output(self):
        source = self.fixture.source
        assets = self.job / "assets"
        assets.mkdir()
        (assets / "copy.txt").write_text(self.correct, encoding="utf-8")
        job = self.job / "work/自动化混剪_20261013_120000/临时文件"
        order = {"schema": a.ORDER_SCHEMA, "requested_outputs": 1, "sources": [{"path": str(source)}],
                 "asset_root": str(assets), "output_root": str(job.parent.parent)}
        with patch.object(a.sys, "stdin", io.StringIO(json.dumps(order))), patch("builtins.print"):
            a.init(SimpleNamespace(work_order=Path("-"), job_dir=job))
        value = a.state(job)
        snapshot = value["text_library"]
        self.assertEqual(snapshot, a.read(Path(value["work_order"]["path"]))["text_library"])
        self.assertEqual(a.read(a.ROOT / "assets/text-library.json"), a.TEXT_LIBRARY.load(snapshot))
        spec = {"frames": 120, "fps_num": 60, "fps_den": 1}
        with patch.object(a, "load_model"), patch.object(a, "video", return_value=spec), \
                patch.object(a, "transcribe", return_value=self.fixture.asr(self.raw)):
            a.prepare(SimpleNamespace(job_dir=job))
        index = a.read(Path(a.state(job)["source_index"]["path"]))
        self.assertEqual(snapshot, index["text_library"])
        self.assertEqual(snapshot, index["sources"][0]["text_library"])
        self.assertEqual(self.raw, a.read(Path(index["sources"][0]["asr"]["path"]))["asr"]["text"])
        # Future library contents do not replace this task's copied bytes.
        different = copy.deepcopy(a.TEXT_LIBRARY.load(snapshot))
        different["entries"].append({"id": "term:future", "category": "term", "text": "冰雪生存"})
        future = self.job / "assets/text-library.json"
        a.write(future, different)
        packaging = a.module("future_library_init", "scripts/packaging/scripts/package_video.py")
        later = job.parent.parent / "自动化混剪_20261013_130000/临时文件"
        with patch.object(a, "ROOT", self.job), patch.object(a, "module", return_value=packaging), \
                patch.object(a.sys, "stdin", io.StringIO(json.dumps(order))), patch("builtins.print"):
            a.init(SimpleNamespace(work_order=Path("-"), job_dir=later))
            self.assertEqual(snapshot, a.state(job)["text_library"])
            self.assertEqual(4, len(a.TEXT_LIBRARY.load(snapshot)["entries"]))
            self.assertEqual(5, len(a.TEXT_LIBRARY.load(a.state(later)["text_library"])["entries"]))

    def test_game_and_person_corrections_preserve_original_asr(self):
        original = a.ref(self.fixture.raw_path)
        value = self.source_review()
        index = a.read(Path(value["source_index"]["path"]))
        self.assertEqual(self.correct, a.source_speech(index["sources"][0])["text"])
        self.assertEqual(original, a.ref(self.fixture.raw_path))
        self.assertEqual(self.raw, a.read(self.fixture.raw_path)["asr"]["text"])
        artifact = a.read(Path(index["sources"][0]["reviewed_speech"]["path"]))
        self.assertEqual(self.library, artifact["text_library"])
        keep = set()
        a.SPEECH.retain_refs(a.speech_api(), index["sources"][0]["reviewed_speech"], self.job, keep)
        self.assertIn(self.path, keep)
        self.assertIn(self.fixture.raw_path, keep)
        evidence = a.read(Path(artifact["evidence"]["path"]))
        self.assertIn(Path(evidence["sources"][0]["audio"]["path"]), keep)

    def test_clean_review_keeps_raw_words_and_passes_snapshot_to_caption_generation(self):
        value = self.source_review()
        plan = self.job / "plan.json"
        output = {"plan_id": "P1", "segments": [{"text": self.correct}]}
        a.write(plan, {"planning_policy": a.EDITING.POLICY, "speech_review_policy": a.SPEECH.POLICY,
                       "text_library": self.library, "outputs": [output]})
        clean_video = self.job / "clean.mp4"
        clean_video.write_bytes(b"synthetic clean output")
        output_ref = a.ref(clean_video)
        render_path = self.job / "render.json"
        a.write(render_path, {"actual_output_frames": 120, "segments": [{"expected_output_frames": 120}]})
        observation = {"shot_index": 1, "audio": a.ref(self.fixture.audio), "asr": self.fixture.asr(self.raw),
                       "expected_text": self.correct, "output_in_frame": 0, "output_out_frame_exclusive": 120}
        evidence_path = self.job / "clean-evidence.json"
        a.write(evidence_path, {"policy": a.SPEECH.POLICY, "text_library": self.library,
                "plan": a.ref(plan), "source_index": value["source_index"], "asset_copy": value["asset_copy"],
                "results": [{"plan_id": "P1", "output_sha256": output_ref["sha256"],
                             "actual_output_frames": 120, "observations": [observation]}]})
        review_path = self.job / "clean-review.json"
        a.write(review_path, {"schema": a.SPEECH.SCHEMA, "stage": "clean", "reviewer_role": "codex",
                "evidence_sha256": a.sha(evidence_path), "asset_copy_sha256": value["asset_copy"]["sha256"],
                "text_library_sha256": self.library["sha256"],
                "results": [{"plan_id": "P1", "shots": [{"shot_index": 1, **self.finding()}]}]})
        clean_path = self.job / "clean-delivery.json"
        a.write(clean_path, {"results": [{"plan_id": "P1", "output": output_ref, "render_evidence": a.ref(render_path)}]})
        qc_path = self.job / "clean-qc.json"
        a.write(qc_path, {"decision": "pending_speech_review", "failures": [], "results": [{"plan_id": "P1",
                "output": output_ref, "asr": self.fixture.asr(self.raw), "timing_asr": {"output": output_ref,
                "timing_basis": "verified_rendered_shot_asr", "shot_observations": [observation]}}]})
        value.update(plan=a.ref(plan), clean_speech_evidence=a.ref(evidence_path),
                     clean_delivery=a.ref(clean_path), clean_qc=a.ref(qc_path))
        a.save(self.job, value, "clean_speech_pending")
        with patch.object(a, "require_editing_approval"):
            a.clean_speech_review(SimpleNamespace(job_dir=self.job, review=review_path))
        qc = a.read(Path(a.state(self.job)["clean_qc"]["path"]))
        self.assertEqual("pass", qc["decision"])
        row = qc["results"][0]
        self.assertEqual(self.raw, row["asr"]["text"])
        timing = row["timing_asr"]
        self.assertEqual(self.library, timing["text_library"])
        packaging = a.module("clean_library_captions", "scripts/packaging/scripts/package_video.py")
        result = packaging.verified_caption_rows(timing)
        self.assertEqual(self.correct, "".join(r["text"] for r in result))
        self.assertTrue(a.verified_shot_speech(timing, output_ref, output, a.read(render_path), {}))
        keep = set()
        a.SPEECH.retain_refs(a.speech_api(), timing["review_context"], self.job, keep)
        self.assertIn(self.path, keep)
        timing.pop("text_library")
        with self.assertRaisesRegex(ValueError, "snapshot binding"):
            a.verified_shot_speech(timing, output_ref, output, a.read(render_path), {})

    def test_unknown_changed_missing_or_wrong_term_evidence_is_rejected(self):
        for update in ({"term_id": "person:unknown"}, {"sha256": "0" * 64},
                       {"text": "无尽东日"}, {"term_id": "person:wu-yue", "text": "吴樾"}):
            finding = self.finding()
            finding["corrections"][0]["evidence"][1].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "text library correction"):
                self.validate(finding)
        finding = self.finding()
        finding["corrections"][0]["evidence"].pop()
        with self.assertRaisesRegex(ValueError, "term reference"):
            self.validate(finding)
        finding = self.finding()
        finding["corrections"][0]["evidence"].pop(0)
        with self.assertRaisesRegex(ValueError, "source-audio evidence required"):
            self.validate(finding)

    def test_library_cannot_justify_noncanonical_reviewed_text(self):
        finding = self.finding("我是吴岳", "我是吴越")
        finding["corrections"][0]["evidence"].append({"kind": "text_library", "sha256": self.library["sha256"],
                                                    "term_id": "person:wu-yue", "text": "吴樾"})
        with self.assertRaisesRegex(ValueError, "reviewed span invalid"):
            self.validate(finding)

    def test_unrelated_speech_is_not_extended_with_library_terms(self):
        finding = self.finding("今天天气好", "今天天气好")
        self.assertEqual("今天天气好", self.validate(finding)["text"])
        self.assertEqual([], finding["corrections"])

    def test_candidate_review_requires_same_snapshot_and_canonical_terms(self):
        value = self.source_review()
        segment = {"text": self.correct, "source_in_frame": 0, "source_out_frame_exclusive": 120,
                   "source_fps_num": 60, "source_fps_den": 1}
        plan = self.job / "plan.json"
        a.write(plan, {"text_library": self.library, "speech_review_policy": a.SPEECH.POLICY,
                       "outputs": [{"plan_id": "P1", "segments": [segment]}]})
        value["plan"] = a.ref(plan)
        evidence = {"speech_review_policy": a.SPEECH.POLICY, "text_library": self.library,
                    "results": [{"plan_id": "P1", "segment_index": 1, "asr": self.fixture.asr(self.raw),
                                 "pcm": a.ref(self.fixture.audio), "expected_text": self.correct, "source_text_match": True}]}
        review = {"speech_review_policy": a.SPEECH.POLICY, "text_library_sha256": self.library["sha256"],
                  "segments": [{"plan_id": "P1", "segment_index": 1, "speech_review": self.finding()}]}
        a.require_candidate_speech(value, evidence, review)
        evidence.pop("text_library")
        with self.assertRaisesRegex(ValueError, "snapshot binding"):
            a.require_candidate_speech(value, evidence, review)

    def test_review_header_state_plan_and_snapshot_cannot_drop_or_change_binding(self):
        review = {"text_library_sha256": self.library["sha256"]}
        a.SPEECH.library_header(review, self.library)
        for bad in ({}, {"text_library_sha256": "0" * 64}):
            with self.assertRaisesRegex(ValueError, "hash binding"):
                a.SPEECH.library_header(bad, self.library)
        value = a.state(self.job)
        value.pop("text_library")
        a.save(self.job, value, "copy_indexed")
        with self.assertRaisesRegex(ValueError, "snapshot binding"):
            a.state(self.job)
        index = a.read(self.fixture.index_path)
        with self.assertRaisesRegex(ValueError, "snapshot binding"):
            a.require_speech_policy(self.fixture.value, index, {"speech_review_policy": a.SPEECH.POLICY})
        self.path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            a.TEXT_LIBRARY.load(self.library)

    def test_all_seed_names_are_atomic_and_subtitles_cannot_revert_spelling(self):
        packaging = a.module("library_caption_test", "scripts/packaging/scripts/package_video.py")
        for name in ("吴樾", "林子聪", "许君聪", "无尽冬日"):
            text = "今天我们特别邀请" + name + "为大家介绍这款游戏"
            result = packaging.verified_caption_rows({**self.fixture.asr(text), "text_library": self.library})
            self.assertEqual(text, "".join(r["text"] for r in result))
            self.assertEqual(1, sum(name in r["text"] for r in result))
        protected = a.TEXT_LIBRARY.protected_terms(self.library)
        with self.assertRaisesRegex(ValueError, "split between"):
            a.TEXT_LIBRARY.require_complete_terms([{"text": "我是许君"}, {"text": "聪"}], protected, a.normalized)
        with self.assertRaisesRegex(ValueError, "spelling differs"):
            a.TEXT_LIBRARY.require_complete_terms([{"text": "我是吴岳"}], protected, a.normalized, expected="我是吴樾")

    def test_invalid_library_data_is_rejected_and_legacy_jobs_need_no_snapshot(self):
        seed = a.TEXT_LIBRARY.load(self.library)
        for entry in ({"id": "person:wu-yue", "category": "person", "text": "另一个人"},
                      {"id": "term:bad", "category": "unknown", "text": "文字"},
                      {"id": "term:bad", "category": "term", "text": "两行\n文字"}):
            bad = copy.deepcopy(seed)
            bad["entries"].append(entry)
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                a.TEXT_LIBRARY.checked(bad)
        a.SPEECH.library_header({}, None)
        a.TEXT_LIBRARY.require_binding(None, {})
        self.assertEqual((), a.TEXT_LIBRARY.protected_terms(None))


if __name__ == "__main__":
    unittest.main()
