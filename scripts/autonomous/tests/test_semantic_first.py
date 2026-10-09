from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py"
SPEC = importlib.util.spec_from_file_location("semantic_first_test_runtime", SCRIPT)
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
editing = auto.EDITING


def transition(left: str, right: str, relation="cause_effect") -> dict:
    return {"relation": relation, "information_gain": "从保暖措施推进到营地生活的结果",
            "content_evidence": {"from_quote": left, "to_quote": right,
                "new_information": "稳定温度让营地居民能够安心生活",
                "entity_flow": "同一座营地及其居民", "state_flow": "采取保暖措施后营地恢复生活"}}


class SemanticFirstTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"hash-bound original test source")
        self.image = self.root / "frame.jpg"
        Image.new("RGB", (32, 48), "blue").save(self.image)
        self.plan = self.make_plan()

    def tearDown(self):
        self.temporary.cleanup()

    def make_plan(self, frames=1440, source_fps=(60, 1)):
        # Two complete spoken turns, coalesced into one render interval, with
        # four actual camera changes proved separately by the visual review.
        split = frames // 2
        texts = ["开局先收集木材，再用资源把营地的温度升起来", "温度稳定以后大家才能安心生活，这座营地也终于有了希望"]
        segments = []
        for index, (start, end) in enumerate(((0, split), (split, frames))):
            segments.append({"segment_id": f"S{index + 1}", "source_path": str(self.source),
                "source_sha256": auto.sha(self.source), "source_in_frame": start,
                "speech_end_frame": end - 3, "source_out_frame_exclusive": end,
                "source_fps_num": source_fps[0], "source_fps_den": source_fps[1],
                "speed": 1.0, "text": texts[index], "visible_person_ids": ["person:lead"],
                "purpose_contract": {"function": "mechanism" if index == 0 else "story_resolution",
                    "new_claim_ids": [f"claim-{index}"], "non_redundant": True, "narrative_stage": index,
                    **({"closing_payoff": True, "closing_quote": texts[index]} if index else {})}})
        cuts = [round(frames * index / 4) for index in range(5)]
        shots = [{"render_segment_index": 1, "source_sha256": auto.sha(self.source),
                  "source_in_frame": cuts[index], "source_out_frame_exclusive": cuts[index + 1],
                  "source_visual_shot_id": f"camera-{index}", "visible_person_ids": ["person:lead"],
                  "boundary_kind": "opening" if index == 0 else "source_camera_change"} for index in range(4)]
        return {"schema": auto.PLAN_SCHEMA, "planning_policy": editing.POLICY,
                "outputs": [{"plan_id": "P1", "protagonist_id": "person:lead", "segments": segments,
                             "transitions": [transition(*texts)], "visual_shots": shots}]}

    def source_index(self, fps=(60, 1)):
        return {"sources": [{"source": auto.ref(self.source),
                "video": {"fps_num": fps[0], "fps_den": fps[1], "frames": 10000}}]}

    def errors(self, plan=None):
        return auto.check_plan(plan or self.plan, self.source_index(), 1, editing.POLICY)

    def findings(self, output):
        return {"plan_id": output["plan_id"], "protagonist_id": output["protagonist_id"],
                "visual_shot_count": len(output["visual_shots"]), "semantic_pass": True,
                "protagonist_only_pass": True, "visual_shots_pass": True,
                "semantic_reason": "保暖措施推进到营地生活结果，最后完整收束。",
                "protagonist_reason": "逐帧检查完整区间，只有该条主角出镜，画外问话不计为可见角色。",
                "shot_reason": "四个真实机位镜头，各六秒，完整覆盖成片。"}

    def approval_fixture(self, job=None):
        folder = job or self.root
        order = folder / "order.json"
        auto.write(order, {"schema": auto.ORDER_SCHEMA, "requested_outputs": 1, "planning_policy": editing.POLICY})
        index = folder / "source_index.json"
        auto.write(index, self.source_index())
        plan = folder / "plan.json"
        auto.write(plan, self.plan)
        rows = []
        for i, segment in enumerate(self.plan["outputs"][0]["segments"], 1):
            start, end = segment["source_in_frame"], segment["source_out_frame_exclusive"]
            visual_path = folder / f"continuous-{i}.json"
            # Synthetic test pixels validate coverage/binding, not visual quality.
            auto.write(visual_path, {"schema": "video-montage-continuous-visual/v1", "source": auto.ref(self.source),
                "source_in_frame": start, "source_out_frame_exclusive": end, "width": 960,
                "frames": [{"frame": number, **auto.ref(self.image)} for number in range(start, end)],
                "contact_sheets": [], "decision": "pending_review"})
            rows.append({"plan_id": "P1", "segment_index": i, "source": auto.ref(self.source),
                         "continuous_visual": auto.ref(visual_path), "frames": [], "pcm": auto.ref(self.image),
                         "asr_exact": True, "decision": "pass"})
        evidence_path = folder / "evidence.json"
        auto.write(evidence_path, {"plan": auto.ref(plan), "source_index": auto.ref(index),
                                   "results": rows, "planning_policy": editing.POLICY})
        output = self.plan["outputs"][0]
        review = {"schema": auto.REVIEW_SCHEMA, "stage": "plan", "reviewer_role": "codex",
            "planning_policy": editing.POLICY, "evidence_sha256": auto.sha(evidence_path),
            "plan_sha256": auto.sha(plan), "outputs": [self.findings(output)],
            "segments": [{"plan_id": "P1", "segment_index": row["segment_index"], "semantic_pass": True,
                "visual_pass": True, "reason": "完整源话轮", "protagonist_only_pass": True,
                "visible_person_ids": ["person:lead"], "continuous_visual_sha256": row["continuous_visual"]["sha256"],
                "protagonist_reason": "全部帧只有主角"} for row in rows],
            "transitions": [{"plan_id": "P1", "transition_index": 1, "continuity_pass": True,
                "reason": "先改善温度，再说明营地生活的结果"}],
            "visual_shots": [{"plan_id": "P1", "shot_index": index, "shot_pass": True,
                "protagonist_only_pass": True, "visible_person_ids": ["person:lead"],
                "source_visual_shot_id": shot["source_visual_shot_id"], "reason": "只有主角",
                "boundary_reason": "原片机位真实改变，非拆分台词"} for index, shot in enumerate(output["visual_shots"], 1)]}
        review_path = folder / "review.json"
        auto.write(review_path, review)
        value = {"schema": auto.STATE_SCHEMA, "review_mode": "codex_asr_pcm", "repair_round": 0,
            "phase": "plan_evidenced", "planning_policy": editing.POLICY, "work_order": auto.ref(order),
            "source_index": auto.ref(index), "plan": auto.ref(plan), "plan_evidence": auto.ref(evidence_path)}
        auto.write(folder / "autonomous_state.json", value)
        return review_path, value, review

    def delivery_fixture(self):
        lifecycle = auto.module("editing_delivery_fixture", "scripts/autonomous/tests/test_delivery_lifecycle.py")
        fixture = lifecycle.DeliveryLifecycleTests()
        fixture.root = self.root
        job, delivery, pending, _, packager = fixture.fixture()
        previous = auto.state(job)
        self.source = self.root / "original.mp4"
        auto.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=1440x2560:r=60:d=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=24", "-af", "volume=0.01",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(self.source)])
        self.plan = self.make_plan()
        output = self.plan["outputs"][0]
        texts = ["开局收集木材，让营地暖起来", "温度稳定，大家终于安心生活"]
        for segment, text in zip(output["segments"], texts):
            segment["text"] = text
        output["segments"][-1]["purpose_contract"]["closing_quote"] = texts[-1]
        output["transitions"] = [transition(*texts)]
        review_path, value, _ = self.approval_fixture(job)
        auto.approve_plan(SimpleNamespace(job_dir=job, review=review_path))
        value = auto.state(job)
        for key in ("output_root", "delivery_directory", "pending_delivery_directory", "delivery_layout", "records_policy", "asset_copy"):
            value[key] = previous[key]
        value["source_hashes"] = {str(self.source): auto.sha(self.source)}
        texts = [segment["text"] for segment in self.plan["outputs"][0]["segments"]]
        asr = {"text": "".join(texts), "segments": [
            {"start": index * 12 + .1, "end": index * 12 + 11.8, "text": text,
             "words": [{"word": text, "start": index * 12 + .1, "end": index * 12 + 11.8}]} for index, text in enumerate(texts)]}
        source_asr = job / "evidence" / "new_source_asr.json"
        auto.write(source_asr, {"source": auto.ref(self.source), "asr": asr})
        index_path = Path(value["source_index"]["path"])
        index = auto.read(index_path)
        index["sources"][0]["video"]["frames"] = 1440
        index["sources"][0]["asr"] = auto.ref(source_asr)
        index["asset_copy_sources"] = auto.read(Path(previous["source_index"]["path"]))["asset_copy_sources"]
        auto.write(index_path, index)
        value["source_index"] = auto.ref(index_path)
        evidence_path = Path(value["plan_evidence"]["path"])
        evidence = auto.read(evidence_path)
        evidence["source_index"] = value["source_index"]
        auto.write(evidence_path, evidence)
        value["plan_evidence"] = auto.ref(evidence_path)
        review = auto.read(review_path)
        review["evidence_sha256"] = auto.sha(evidence_path)
        auto.write(review_path, review)
        value["plan_review"] = auto.ref(review_path)
        clean_output = pending / "混剪（无包装）" / "P1.mp4"
        shutil.copyfile(self.source, clean_output)
        render_path = job / "evidence" / "new_render.json"
        timeline = [{**row, "source_segment_indexes": row["_source_segment_indexes"]}
                    for row in editing.renderer.planned_timeline(self.plan["outputs"][0]["segments"])]
        auto.write(render_path, {"schema": "portable-frame-render-evidence/v260928", "expected_output_frames": 1440,
            "actual_output_frames": 1440, "segments": timeline})
        clean_path = pending / "临时文件" / "manifests" / "clean_delivery.json"
        auto.write(clean_path, {"schema": "video-montage-autonomous-clean/v260929", "planning_policy": editing.POLICY,
            "plan_sha256": value["plan"]["sha256"], "results": [{"plan_id": "P1", "output": auto.ref(clean_output),
            "render_evidence": auto.ref(render_path), "expected_text": asr["text"]}]})
        value["clean_delivery"] = auto.ref(clean_path)
        qc_path = job / "reports" / "clean_qc.json"
        auto.write(qc_path, {"schema": "video-montage-autonomous-clean-qc/v260929", "decision": "pass", "clean_delivery": value["clean_delivery"], "planning_policy": editing.POLICY,
            "plan_sha256": value["plan"]["sha256"], "results": [{"plan_id": "P1", "output": auto.ref(clean_output),
                "text_match": True, "asr": asr, "timing_asr": asr, "metrics": auto.audio_metrics(auto.pcm(clean_output)), "cut_pcm": []}]})
        value["clean_qc"] = auto.ref(qc_path)
        auto.save(job, value, "clean_validated")
        return job, delivery, pending, asr, packager

    def mock_continuous(self, path, start, end, directory):
        reference = directory / "synthetic-test-navigation.json"
        auto.write(reference, {"schema": "video-montage-continuous-visual/v1", "source": auto.ref(path),
            "source_in_frame": start, "source_out_frame_exclusive": end, "width": 960,
            "frames": [{"frame": frame, **auto.ref(self.image)} for frame in range(start, end)],
            "contact_sheets": [], "decision": "pending_review"})
        return auto.ref(reference)

    def final_review(self, job):
        value = auto.state(job)
        evidence = auto.read(Path(value["final_evidence"]["path"]))
        row = evidence["results"][0]
        path = job / "reviews" / "final.json"
        auto.write(path, {"schema": auto.REVIEW_SCHEMA, "stage": "final", "reviewer_role": "codex",
            "planning_policy": editing.POLICY, "evidence_sha256": value["final_evidence"]["sha256"],
            "outputs": [{**self.findings(self.plan["outputs"][0]), "output_sha256": row["output"]["sha256"],
                "visual_pass": True, "subtitle_pass": True, "overlay_pass": True, "design_pass": True,
                "design_reason": "Test static subtitles", "reason": "Synthetic editorial test findings",
                "continuous_visual_sha256": row["continuous_visual"]["sha256"]}]})
        return path

    def prepare_subtitles(self, job, pending, asr, packager):
        value = auto.state(job)
        clean_row = auto.read(Path(value["clean_delivery"]["path"]))["results"][0]
        subtitle = pending / "字幕" / "subtitle-P1.txt"
        texts = [segment["text"] for segment in self.plan["outputs"][0]["segments"]]
        subtitle.write_text(f"1\n00:00:00,100 --> 00:00:11,800\n{texts[0]}\n\n2\n00:00:12,100 --> 00:00:23,800\n{texts[1]}\n", encoding="utf-8")
        snapshot = job / "evidence" / "draft.txt"
        shutil.copyfile(subtitle, snapshot)
        draft = job / "reports" / "subtitle_draft.json"
        auto.write(draft, {"results": [{"plan_id": "P1", "input_path": clean_row["output"]["path"],
            "subtitle_txt_path": str(snapshot), "subtitle_txt_sha256": auto.sha(snapshot)}]})
        value["subtitle_draft"] = auto.ref(draft)
        auto.save(job, value, "subtitle_drafted")
        config = job / "config" / "new_config.json"
        auto.write(config, {"schema": packager.SCHEMA, "outputs": [{"plan_id": "P1", "input_path": clean_row["output"]["path"],
            "input_sha256": clean_row["output"]["sha256"], "subtitle_txt": str(subtitle), "subtitle_sha256": auto.sha(subtitle),
            "graphic_layers": [], "subtitle_design": {"subtitle_sha256": auto.sha(subtitle), "font": "smiley", "game_names": [],
                "reason": "Test static yellow subtitles", "cues": [{"index": index, "text": text, "color": "yellow"} for index, text in enumerate(texts, 1)]}}]})
        self.approve_subtitle(job)
        return config

    def approve_subtitle(self, job):
        value = auto.state(job)
        draft = auto.read(Path(value["subtitle_draft"]["path"]))["results"][0]
        packager = auto.module("semantic_test_subtitles", "scripts/packaging/scripts/package_video.py")
        cues = packager.parse_srt(Path(draft["subtitle_txt_path"]), 24000)
        path = job / "reviews" / "subtitle.json"
        auto.write(path, {"schema": "video-montage-codex-subtitle-review/v260929", "draft_sha256": value["subtitle_draft"]["sha256"],
            "asset_copy_sha256": value["asset_copy"]["sha256"], "results": [{"plan_id": "P1", "draft_subtitle_sha256": draft["subtitle_txt_sha256"],
                "cues": [{"index": index, "draft_start_ms": cue["start_ms"], "draft_end_ms": cue["end_ms"],
                    "before": cue["text"], "start_ms": cue["start_ms"], "end_ms": cue["end_ms"], "after": cue["text"]} for index, cue in enumerate(cues, 1)]}]})
        auto.subtitle_review(SimpleNamespace(job_dir=job, review=path))

    def test_coherent_two_turns_with_four_native_camera_shots_pass(self):
        self.assertEqual([], self.errors())
        output = self.plan["outputs"][0]
        self.assertEqual(1, len(editing.renderer.planned_timeline(output["segments"])))
        self.assertEqual(4, editing.summary(output)["visual_shot_count"])
        self.assertEqual(1440, editing.summary(output)["clean_frames"])

    def test_three_shots_or_splitting_one_continuous_shot_cannot_pass(self):
        changed = copy.deepcopy(self.plan)
        shots = changed["outputs"][0]["visual_shots"]
        shots[0]["source_out_frame_exclusive"] = shots[1]["source_out_frame_exclusive"]
        del shots[1]
        self.assertTrue(any("FOUR_REAL" in error for error in self.errors(changed)))
        changed = copy.deepcopy(self.plan)
        for shot in changed["outputs"][0]["visual_shots"]:
            shot["source_visual_shot_id"] = "one-continuous-shot"
        self.assertTrue(any("CONTINUOUS_SHOT_SPLIT" in error for error in self.errors(changed)))

    def test_shot_table_gaps_wrong_hashes_and_flash_frames_fail(self):
        for field, value in (("source_in_frame", 1), ("source_sha256", "f" * 64), ("source_out_frame_exclusive", 1)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.plan)
                changed["outputs"][0]["visual_shots"][0][field] = value
                self.assertTrue(self.errors(changed))

    def test_source_bound_cast_excludes_supporting_people_even_with_protagonist(self):
        for people in ([], ["person:unknown"], ["person:lead", "person:extra"]):
            changed = copy.deepcopy(self.plan)
            changed["outputs"][0]["segments"][0]["visible_person_ids"] = people
            self.assertTrue(any("VISIBLE_PERSON" in error for error in self.errors(changed)))
        changed = copy.deepcopy(self.plan)
        changed["outputs"][0]["visual_shots"][1]["visible_person_ids"] = ["person:lead", "person:extra"]
        self.assertTrue(any("VISIBLE_PERSON" in error for error in self.errors(changed)))

    def test_offscreen_question_is_allowed_with_grounded_answer(self):
        changed = copy.deepcopy(self.plan)
        output = changed["outputs"][0]
        output["segments"][0]["text"] = "营地太冷了，我们该怎么办才能安心生活"
        output["segments"][0]["speaker_id"] = "person:offscreen"
        output["segments"][1]["text"] = "把营地的温度稳定下来，大家就能安心生活，这座营地也终于有了希望"
        output["segments"][1]["purpose_contract"]["closing_quote"] = output["segments"][1]["text"]
        output["transitions"] = [transition(output["segments"][0]["text"], output["segments"][1]["text"], "question_answer")]
        self.assertEqual([], self.errors(changed))

    def test_missing_connectors_wrong_quotes_repeated_claims_and_unearned_closing_fail(self):
        mutations = [
            lambda output: output["segments"][0].update(text="所以" + output["segments"][0]["text"]),
            lambda output: output["transitions"][0]["content_evidence"].update(to_quote="另一个人物的无关台词"),
            lambda output: output["segments"][1]["purpose_contract"].update(new_claim_ids=["claim-0"]),
            lambda output: output["segments"][1]["purpose_contract"].update(closing_payoff=False),
            lambda output: output["segments"][1]["purpose_contract"].update(narrative_stage=-1),
            lambda output: output["transitions"][0]["content_evidence"].pop("entity_flow"),
        ]
        for mutation in mutations:
            changed = copy.deepcopy(self.plan)
            mutation(changed["outputs"][0])
            self.assertTrue(self.errors(changed))

    def test_unrelated_benefit_cannot_interrupt_mechanism(self):
        changed = copy.deepcopy(self.plan)
        changed["outputs"][0]["segments"][0]["text"] = "点击领取海量福利，再回去收集木材"
        errors = self.errors(changed)
        self.assertTrue(any("GENERIC_BENEFIT_NOT_FINAL" in error for error in errors))

    def test_duration_edges_are_based_on_renderer_frames(self):
        for count, accepted in ((1290, False), (1295, False), (1296, True), (2160, True), (2161, False)):
            changed = self.make_plan(frames=count)
            duration_errors = [error for error in self.errors(changed) if "DURATION_VIOLATION" in error]
            self.assertEqual(not accepted, bool(duration_errors), count)
        for count, accepted in ((1075, False), (1079, False), (1080, True), (1800, True), (1801, False)):
            self.assertEqual(not accepted, bool(editing.frame_errors(count, final=True)), count)

    def test_mixed_native_rates_and_overlap_use_shared_render_rounding(self):
        for fps, count in (((25, 1), 600), ((30000, 1001), 720)):
            changed = self.make_plan(frames=count, source_fps=fps)
            self.assertEqual([], auto.check_plan(changed, self.source_index(fps), 1, editing.POLICY))
            expected = editing.renderer.segment_output_frames(editing.renderer.coalesce_segments(changed["outputs"][0]["segments"])[0])
            self.assertEqual(expected, editing.summary(changed["outputs"][0])["clean_frames"])
        changed = copy.deepcopy(self.plan)
        changed["outputs"][0]["segments"][1]["source_in_frame"] = 600
        self.assertEqual(1440, editing.summary(changed["outputs"][0])["clean_frames"])
        self.assertEqual([], self.errors(changed))

    def test_new_policy_cannot_be_dropped_and_legacy_plan_still_reads(self):
        changed = copy.deepcopy(self.plan)
        changed.pop("planning_policy")
        changed["outputs"][0].pop("protagonist_id")
        self.assertTrue(self.errors(changed))
        self.assertEqual([], auto.check_plan(changed, self.source_index(), 1))

    def test_full_edit_review_and_every_transition_are_required(self):
        review_path, value, review = self.approval_fixture()
        auto.approve_plan(SimpleNamespace(job_dir=self.root, review=review_path))
        self.assertEqual("plan_approved", auto.state(self.root)["phase"])
        auto.require_editing_approval(auto.state(self.root))
        for key in ("outputs", "transitions", "visual_shots"):
            bad = copy.deepcopy(review)
            bad[key] = []
            self.assertTrue(editing.plan_review_errors(self.plan, auto.read(Path(value["plan_evidence"]["path"])), bad))
        # A review bound to the prior plan cannot be retained after a recut.
        plan_path = Path(value["plan"]["path"])
        changed = copy.deepcopy(self.plan)
        changed["outputs"][0]["segments"][0]["source_in_frame"] = 1
        auto.write(plan_path, changed)
        with self.assertRaisesRegex(ValueError, "mismatch|changed"):
            auto.require_editing_approval(auto.state(self.root))

    def test_legacy_approval_cannot_authorize_new_policy(self):
        review_path, _, review = self.approval_fixture()
        review.pop("planning_policy")
        auto.write(review_path, review)
        with self.assertRaisesRegex(ValueError, "legacy approval"):
            auto.approve_plan(SimpleNamespace(job_dir=self.root, review=review_path))

    def test_actor_appearing_mid_interval_rejects_review(self):
        review_path, _, review = self.approval_fixture()
        review["segments"][0]["protagonist_only_pass"] = False
        review["segments"][0]["visible_person_ids"] = ["person:lead", "person:extra"]
        review["segments"][0]["protagonist_reason"] = "中途一帧出现其他演员"
        auto.write(review_path, review)
        with self.assertRaisesRegex(RuntimeError, "FULL_INTERVAL_CAST_REVIEW_REQUIRED"):
            auto.approve_plan(SimpleNamespace(job_dir=self.root, review=review_path))

    def test_continuous_frame_cache_reuses_source_range_and_detects_missing_frame(self):
        def fake_frames(path, numbers, output, **kwargs):
            return [{"frame": number, **auto.ref(self.image)} for number in numbers]
        with patch.object(auto, "frames", side_effect=fake_frames) as decode:
            first = auto.continuous_visual(self.source, 3, 20, self.root / "cache")
            second = auto.continuous_visual(self.source, 3, 20, self.root / "cache")
            self.assertEqual(first, second)
            self.assertEqual(1, decode.call_count)
        document = auto.read(Path(first["path"]))
        document["frames"].pop(8)
        auto.write(Path(first["path"]), document)
        with self.assertRaisesRegex(ValueError, "every selected native frame"):
            auto.verify_continuous_visual(auto.ref(Path(first["path"])), auto.ref(self.source), 3, 20)

    def test_real_ffmpeg_continuous_evidence_enumerates_every_native_frame(self):
        if not auto.FFMPEG.is_file():
            self.skipTest("bundled FFmpeg unavailable")
        auto.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x160:rate=25:duration=0.4",
                  "-c:v", "libx264", "-y", str(self.source)])
        reference = auto.continuous_visual(self.source, 2, 9, self.root / "real")
        document = auto.verify_continuous_visual(reference, auto.ref(self.source), 2, 9)
        self.assertEqual(list(range(2, 9)), [row["frame"] for row in document["frames"]])
        with Image.open(document["frames"][0]["path"]) as picture:
            self.assertEqual(960, picture.width)

    def test_cli_has_no_diversification_step(self):
        result = subprocess.run([str(auto.ROOT / "assets/dependencies/python/python.exe"), "-B", str(SCRIPT), "--help"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("diversify-plan", result.stdout)

    def test_historical_diversity_report_keeps_hash_binding(self):
        plan = self.root / "historical-plan.json"
        auto.write(plan, {"outputs": []})
        report = self.root / "historical-diversity.json"
        auto.write(report, {"plan": auto.ref(plan), "policy": "best_effort"})
        value = {"plan": auto.ref(plan), "batch_diversity": auto.ref(report)}
        evidence = {"batch_diversity": value["batch_diversity"]}
        self.assertEqual("best_effort", auto.require_diversity(value, evidence)["policy"])
        auto.write(plan, {"outputs": ["changed"]})
        with self.assertRaisesRegex(ValueError, "changed|mismatch"):
            auto.require_diversity(value, evidence)

    def test_clean_delivery_requires_final_editorial_review_and_retains_policy(self):
        job, delivery, _, asr, packager = self.delivery_fixture()
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr), \
                patch.object(auto, "continuous_visual", side_effect=self.mock_continuous):
            auto.final_evidence(SimpleNamespace(job_dir=job, clean=True))
        review_path = self.final_review(job)
        good = auto.read(review_path)
        bad = copy.deepcopy(good)
        bad["outputs"][0]["semantic_pass"] = False
        auto.write(review_path, bad)
        with patch.object(auto, "fail_round", side_effect=RuntimeError("editorial rejection")) as fail:
            with self.assertRaisesRegex(RuntimeError, "editorial rejection"):
                auto.complete(SimpleNamespace(job_dir=job, review=review_path))
            self.assertEqual("codex_final_editing_review", fail.call_args.args[2])
        self.assertEqual(b"previous video", (delivery / "混剪（无包装）" / "P1.mp4").read_bytes())
        auto.write(review_path, good)
        auto.complete(SimpleNamespace(job_dir=job, review=review_path))
        value = auto.state(job)
        self.assertEqual(editing.POLICY, value["planning_policy"])
        self.assertTrue(value["compact_delivery"])
        self.assertEqual(1200, packager.video_spec(delivery / "混剪（无包装）" / "P1.mp4")["frames"])
        self.assertEqual(1440, packager.video_spec(job / "manifests" / "clean_inputs" / "P1.mp4")["frames"])

    def test_packaging_and_reburn_preserve_clean_authorization_and_apply_speed_once(self):
        job, delivery, pending, asr, packager = self.delivery_fixture()
        config = self.prepare_subtitles(job, pending, asr, packager)
        auto.package(SimpleNamespace(job_dir=job, config=config))
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr), \
                patch.object(auto, "continuous_visual", side_effect=self.mock_continuous):
            auto.final_evidence(SimpleNamespace(job_dir=job))
        auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        value = auto.state(job)
        context = value["reburn_inputs"]["editing_context"]
        self.assertEqual("validated_clean_input", auto.read(Path(context["path"]))["basis"])
        self.assertFalse((job / "review.json").exists())
        self.assertEqual(1200, packager.video_spec(delivery / "成片" / "P1.mp4")["frames"])
        subtitle = delivery / "字幕" / "subtitle-P1.txt"
        auto.reburn(SimpleNamespace(job_dir=job, plan_id="P1", subtitle_txt=subtitle))
        self.assertEqual(editing.POLICY, auto.state(job)["planning_policy"])
        self.approve_subtitle(job)
        config = Path(auto.state(job)["reburn_config"]["path"])
        auto.package(SimpleNamespace(job_dir=job, config=config))
        with patch.object(auto, "load_model"), patch.object(auto, "transcribe", return_value=asr), \
                patch.object(auto, "continuous_visual", side_effect=self.mock_continuous):
            auto.final_evidence(SimpleNamespace(job_dir=job))
        auto.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        self.assertEqual(1200, packager.video_spec(delivery / "成片" / "P1.mp4")["frames"])
        self.assertEqual(1440, packager.video_spec(delivery / "混剪（无包装）" / "P1.mp4")["frames"])

    def test_missing_candidate_or_duplicate_review_cannot_hide_a_selected_interval(self):
        _, value, review = self.approval_fixture()
        evidence = auto.read(Path(value["plan_evidence"]["path"]))
        evidence["results"].pop()
        review["segments"].pop()
        self.assertTrue(editing.plan_review_errors(self.plan, evidence, review))
        _, value, review = self.approval_fixture()
        evidence = auto.read(Path(value["plan_evidence"]["path"]))
        review["transitions"].append(copy.deepcopy(review["transitions"][0]))
        self.assertTrue(editing.plan_review_errors(self.plan, evidence, review))

    def test_decoded_clean_frames_and_lineage_are_rechecked(self):
        output = self.plan["outputs"][0]
        render = {"actual_output_frames": 1440, "expected_output_frames": 1440,
                  "segments": editing.renderer.planned_timeline(output["segments"])}
        self.assertEqual([], editing.render_errors(output, render, 1440))
        self.assertTrue(any("DURATION_VIOLATION" in error for error in editing.render_errors(output, render, 1290)))
        changed = copy.deepcopy(render)
        changed["segments"][0]["source_out_frame_exclusive"] -= 1
        self.assertIn("CLEAN_RENDER_LINEAGE_CHANGED", editing.render_errors(output, changed, 1440))

    def test_narrative_regression_needs_real_how_why_bridge(self):
        output = copy.deepcopy(self.plan["outputs"][0])
        left, right = output["segments"]
        left["text"] = "营地熔炉满级了，大家都想知道怎么做到的"
        left["purpose_contract"].update(function="hook", narrative_stage=3)
        right["text"] = "因为先收集资源让营地暖起来，大家终于能够安心生活"
        right["purpose_contract"].update(narrative_stage=1, closing_quote=right["text"])
        output["transitions"] = [transition(left["text"], right["text"], "hook_response")]
        self.assertTrue(any("NARRATIVE_STAGE_REGRESSION" in error for error in editing.audit_output(output)))
        output["transitions"][0]["content_evidence"]["how_why_quote"] = "因为先收集资源让营地暖起来"
        self.assertEqual([], editing.audit_output(output))
        output["transitions"][0]["content_evidence"]["how_why_quote"] = "因为另一段不存在的原因"
        self.assertTrue(editing.audit_output(output))

    def test_known_unearned_numeric_or_method_ending_is_rejected(self):
        for text in ("我收了十个幸存者", "把资源拿来升级熔炉"):
            changed = copy.deepcopy(self.plan)
            output = changed["outputs"][0]
            output["segments"][-1]["text"] = text
            output["segments"][-1]["purpose_contract"].update(closing_quote=text, function="result")
            output["transitions"] = [transition(output["segments"][0]["text"], text)]
            self.assertTrue(any("NOT_A_CLOSE" in error for error in self.errors(changed)))


class RecognitionEvidenceTests(unittest.TestCase):
    def test_single_digit_orthography_does_not_accept_missing_words(self):
        self.assertEqual(auto.normalized("全球三亿人"), auto.normalized("全球3亿人"))
        self.assertNotEqual(auto.normalized("13"), auto.normalized("一三"))
        self.assertNotEqual(auto.normalized("无需下载点击即玩"), auto.normalized("点击即玩"))

    def test_independent_retry_preserves_trials_and_restores_word_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "speech.wav"
            source.write_bytes(b"source audio")
            primary = {"text": "错误识别", "segments": []}
            alternative = {"text": "完整台词", "segments": [{"start": 1.0, "end": 2.0,
                "words": [{"word": "台词", "start": 1.5, "end": 2.0}]}]}
            recognizer = SimpleNamespace(transcribe=unittest.mock.Mock(side_effect=[(primary, None), (alternative, None)]))
            def encode(command):
                Path(command[-1]).write_bytes(b"independent tempo audio")
            with patch.object(auto, "module", return_value=recognizer), patch.object(auto, "run", side_effect=encode):
                result = auto.transcribe(None, source, "完整台词", {})
            self.assertEqual(1.1, result["recognition_tempo"])
            self.assertEqual(1.65, result["segments"][0]["words"][0]["start"])
            self.assertEqual("错误识别", result["recognition_trials"][0]["text"])
            self.assertEqual(auto.sha(source.with_name("speech.asr-tempo110.wav")), result["recognition_trials"][1]["audio"]["sha256"])
            for call in recognizer.transcribe.call_args_list:
                self.assertNotIn("完整台词", call.kwargs["initial_prompt"])

    def test_unmatched_retry_does_not_replace_original_or_relax_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "speech.wav"
            source.write_bytes(b"audio")
            recognizer = SimpleNamespace(transcribe=unittest.mock.Mock(side_effect=[
                ({"text": "原始错误", "segments": []}, None), ({"text": "另一错误", "segments": []}, None),
                ({"text": "仍然错误", "segments": []}, None)]))
            with patch.object(auto, "module", return_value=recognizer), patch.object(auto, "run", side_effect=lambda command: Path(command[-1]).write_bytes(b"tempo audio")):
                result = auto.transcribe(None, source, "正确台词", {})
            self.assertEqual("原始错误", result["text"])
            self.assertNotIn("recognition_tempo", result)
            self.assertEqual(3, len(result["recognition_trials"]))


if __name__ == "__main__":
    unittest.main()
