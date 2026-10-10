"""Regressions for cloned plans, disguised reuse and actual decoded base video."""
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

SPEC = importlib.util.spec_from_file_location("batch_test_auto", Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
batch = auto.BATCH


def add_analysis(plan, index):
    plan["batch_review_policy"] = batch.POLICY
    by_source = {}
    for output in plan["outputs"]:
        for segment in output["segments"]:
            segment.setdefault("semantic_cluster_id", "claim:" + batch.digest(batch.text(segment["text"])))
            by_source.setdefault(segment["source_sha256"], {})[(segment["source_in_frame"], segment["source_out_frame_exclusive"], segment["text"])] = segment
        for shot in output["visual_shots"]:
            shot.setdefault("visual_family_id", "visual:" + batch.digest([shot["source_sha256"], shot["source_visual_shot_id"]]))
    plan["batch_analysis"] = {"sources": [{"source_sha256": source["source"]["sha256"],
        "source_asr_sha256": source["asr"]["sha256"], "extraction_status": "exhausted",
        "reason": "Synthetic fixture enumerates all full turns; not a production visual approval.",
        "candidates": [{"source_in_frame": unit["source_in_frame"], "source_out_frame_exclusive": unit["source_out_frame_exclusive"],
                        "text": unit["text"], "status": "eligible", "reason": "Complete synthetic spoken unit"}
                       for unit in by_source.get(source["source"]["sha256"], {}).values()]}
        for source in index["sources"]]}


def add_findings(review, report):
    review["batch_review_policy"] = batch.POLICY
    review["batch_review"] = {"report_digest": batch.digest(report), "source_inventory_pass": True,
        "semantic_routes_pass": True, "visual_families_pass": True, "reuse_pass": True,
        "reason": "Synthetic test findings exercise hash binding, not actual editorial approval."}
    for finding, result in zip(review["outputs"], report["plans"]):
        finding.update(opening_visual_family_id=result["opening_visual_family_id"],
                       second_visual_family_id=result["second_visual_family_id"], opening_visual_pass=True,
                       opening_visual_reason="Synthetic family fixture", reviewed_opening_frame_count=60)


class BatchPlanTests(unittest.TestCase):
    def make_batch(self, count=2):
        index = {"sources": []}
        plan = {"outputs": []}
        quotes = [["窗外暴雪来袭，大家急需一个温暖的营地", "派居民出去寻找木材，点燃炉子驱走寒意", "有了住所和食物，居民终于安定下来", "守住这座城镇，一起度过漫长的冬天"],
                  ["我终于发现一个适合闲暇时玩的轻松游戏", "忙完一天的工作打开手机，享受几分钟休息", "和朋友分享各自的进度，互相交流经验", "今天的小目标顺利完成，心情也舒畅了"]]
        for n in range(count):
            segments, shots = [], []
            for i in range(4):
                sha = batch.digest([n, i])
                index["sources"].append({"source": {"sha256": sha}, "asr": {"sha256": batch.digest(["asr", n, i])}, "video": {"frames": 1000}})
                segments.append({"source_sha256": sha, "source_in_frame": 100, "source_out_frame_exclusive": 460,
                    "text": quotes[n % 2][i] + (str(n) if n >= 2 else ""),
                    "purpose_contract": {"function": "introduction" if i == 0 else "cta" if i == 3 else "mechanism"}})
                shots.append({"source_sha256": sha, "source_in_frame": 100, "source_out_frame_exclusive": 460,
                              "source_visual_shot_id": "camera", "visual_family_id": f"family-{n}-{i}"})
            plan["outputs"].append({"plan_id": f"P{n}", "segments": segments, "visual_shots": shots})
        add_analysis(plan, index)
        return plan, index

    def codes(self, plan, index):
        return {row["code"] for row in batch.audit(plan, index)["failures"]}

    def test_distinct_grounded_routes_pass(self):
        plan, index = self.make_batch()
        self.assertEqual(batch.audit(plan, index)["decision"], "pass")

    def authorize_reuse(self, plan, index):
        authorization = {"policy": batch.REASONABLE_REUSE_POLICY, "authorized_by": "user",
                         "instruction": "Allow reasonable reuse to meet the count, avoid extensive duplicates."}
        index["batch_reuse_authorization"] = authorization
        plan["batch_reuse_authorization"] = copy.deepcopy(authorization)

    def test_reasonable_reuse_requires_bound_user_authorization(self):
        plan, index = self.make_batch()
        self.authorize_reuse(plan, index)
        report = batch.audit(plan, index)
        self.assertEqual(report["limits"]["opening_uses"], 7)
        plan["batch_reuse_authorization"]["instruction"] = "different"
        self.assertIn("BATCH_REUSE_AUTHORIZATION_MISMATCH", self.codes(plan, index))
        index["batch_reuse_authorization"]["authorized_by"] = "codex"
        with self.assertRaises(ValueError):
            batch.audit(plan, index)

    def test_reasonable_reuse_never_allows_whole_plan_clones(self):
        plan, index = self.make_batch()
        self.authorize_reuse(plan, index)
        plan["outputs"][1] = dict(copy.deepcopy(plan["outputs"][0]), plan_id="clone")
        self.assertIn("DUPLICATE_COMPLETE_SPOKEN_CONTENT", self.codes(plan, index))

    def test_source_index_cannot_add_reuse_authorization_to_work_order(self):
        plan, index = self.make_batch()
        self.authorize_reuse(plan, index)
        with tempfile.TemporaryDirectory() as directory:
            order = Path(directory) / "order.json"
            auto.write(order, {"requested_outputs": 2})
            value = {"batch_review_policy": batch.POLICY, "work_order": auto.ref(order)}
            with self.assertRaisesRegex(ValueError, "bound user work order"):
                auto.batch_fields(value, plan, index)

    def test_reasonable_reuse_allows_three_middle_routes_but_not_mass_variants(self):
        plan, index = self.make_batch(4)
        for row in plan["outputs"]:
            for i in (1, 2):
                row["segments"][i]["semantic_cluster_id"] = plan["outputs"][0]["segments"][i]["semantic_cluster_id"]
        self.authorize_reuse(plan, index)
        self.assertIn("HEAD_OR_TAIL_ONLY_VARIANT", self.codes(plan, index))
        plan["outputs"].pop()
        self.assertNotIn("HEAD_OR_TAIL_ONLY_VARIANT", self.codes(plan, index))

    def test_three_routes_copied_to_forty_cannot_pass(self):
        plan, index = self.make_batch(3)
        originals = copy.deepcopy(plan["outputs"])
        plan["outputs"] = [dict(copy.deepcopy(originals[n % 3]), plan_id=f"test-{n + 1:02}") for n in range(40)]
        codes = self.codes(plan, index)
        self.assertTrue({"DUPLICATE_ORDERED_PLAN_SEQUENCE", "UNIQUE_PLAN_CAPACITY_SHORTAGE", "CANDIDATE_REUSE_EXCEEDED",
                         "OPENING_VISUAL_FAMILY_REUSE_EXCEEDED", "CLOSING_REUSE_EXCEEDED"} <= codes)

    def test_reuse_caps_have_inclusive_boundaries(self):
        cases = [("opening", 3, "OPENING_REUSE_EXCEEDED"),
                 ("family", 3, "OPENING_VISUAL_FAMILY_REUSE_EXCEEDED"),
                 ("pair", 2, "OPENING_SECOND_VISUAL_PAIR_REUSE_EXCEEDED"),
                 ("closing", 4, "CLOSING_REUSE_EXCEEDED"),
                 ("candidate", 6, "CANDIDATE_REUSE_EXCEEDED")]
        for kind, limit, code in cases:
            for count in (limit, limit + 1):
                with self.subTest(kind=kind, count=count):
                    plan, index = self.make_batch(count)
                    first = plan["outputs"][0]
                    for output in plan["outputs"]:
                        if kind in {"opening", "closing"}:
                            position = -1 if kind == "closing" else 0
                            output["segments"][position]["text"] = first["segments"][position]["text"]
                        elif kind == "candidate":
                            output["segments"][1] = copy.deepcopy(first["segments"][1])
                        else:
                            output["visual_shots"][0]["visual_family_id"] = "shared-opening"
                            if kind == "pair":
                                output["visual_shots"][1]["visual_family_id"] = "shared-second"
                        for segment in output["segments"]:
                            segment.pop("semantic_cluster_id", None)
                    add_analysis(plan, index)
                    self.assertEqual(code in self.codes(plan, index), count > limit)

    def test_ids_filenames_gain_cut_jitter_and_cluster_rename_do_not_hide_duplicate(self):
        plan, index = self.make_batch()
        other = copy.deepcopy(plan["outputs"][0]);other["plan_id"] = "different-file"
        for segment in other["segments"]:
            segment.update(source_path="renamed.mp4", audio_gain_db=-6, source_in_frame=101, source_out_frame_exclusive=461,
                           segment_id="new-id", semantic_cluster_id="invented-cluster")
        plan["outputs"][1] = other
        codes = self.codes(plan, index)
        self.assertIn("DUPLICATE_ORDERED_PLAN_SEQUENCE", codes)
        self.assertIn("RENAMED_SEMANTIC_CLUSTER", codes)

    def test_split_contiguous_turn_does_not_create_unique_route(self):
        plan, index = self.make_batch()
        other = copy.deepcopy(plan["outputs"][0]);other["plan_id"] = "split"
        segment = other["segments"].pop(0);left, right = copy.deepcopy(segment), copy.deepcopy(segment)
        cut = len(segment["text"]) // 2
        left.update(text=segment["text"][:cut], source_out_frame_exclusive=280)
        right.update(text=segment["text"][cut:], source_in_frame=280)
        other["segments"][:0] = [left, right]
        plan["outputs"][1] = other
        self.assertIn("DUPLICATE_ORDERED_PLAN_SEQUENCE", self.codes(plan, index))

    def test_same_spoken_content_from_different_sources_is_duplicate(self):
        plan, index = self.make_batch()
        for left, right in zip(plan["outputs"][0]["segments"], plan["outputs"][1]["segments"]):
            right["text"] = left["text"];right["semantic_cluster_id"] = left["semantic_cluster_id"]
        add_analysis(plan, index)
        self.assertIn("DUPLICATE_COMPLETE_SPOKEN_CONTENT", self.codes(plan, index))

    def test_only_changing_head_and_tail_cannot_hide_identical_middle(self):
        plan, index = self.make_batch()
        for i in (1, 2):
            plan["outputs"][1]["segments"][i]["semantic_cluster_id"] = plan["outputs"][0]["segments"][i]["semantic_cluster_id"]
        self.assertIn("HEAD_OR_TAIL_ONLY_VARIANT", self.codes(plan, index))
        self.assertIn("SAME_POSITION_ROUTE_OVERLAP_EXCEEDED", self.codes(plan, index))

    def test_changing_editorial_role_does_not_disguise_same_middle(self):
        plan, index = self.make_batch()
        for i in (1, 2):
            unit = plan["outputs"][1]["segments"][i]
            unit.update(text=plan["outputs"][0]["segments"][i]["text"],
                        semantic_cluster_id=plan["outputs"][0]["segments"][i]["semantic_cluster_id"],
                        purpose_contract={"function": "proof"})
        add_analysis(plan, index)
        self.assertIn("HEAD_OR_TAIL_ONLY_VARIANT", self.codes(plan, index))
        self.assertIn("SAME_POSITION_ROUTE_OVERLAP_EXCEEDED", self.codes(plan, index))

    def test_renaming_visual_family_or_shot_does_not_hide_overlapping_frames(self):
        plan, index = self.make_batch()
        shot = copy.deepcopy(plan["outputs"][0]["visual_shots"][0]);shot.update(visual_family_id="fake", source_visual_shot_id="renamed")
        plan["outputs"][1]["visual_shots"][0] = shot
        self.assertIn("RENAMED_OVERLAPPING_VISUAL_FAMILY", self.codes(plan, index))

    def test_partial_source_inventory_and_stale_asr_are_rejected(self):
        plan, index = self.make_batch()
        plan["batch_analysis"]["sources"][0].update(extraction_status="partial", source_asr_sha256="old")
        self.assertIn("CANDIDATE_INVENTORY_INCOMPLETE", self.codes(plan, index))

    def test_missing_or_stale_batch_and_final_opening_review_are_rejected(self):
        plan, index = self.make_batch();report = batch.audit(plan, index)
        review = {"stage": "final", "outputs": [{"plan_id": row["plan_id"]} for row in plan["outputs"]]}
        self.assertTrue(batch.review_errors(report, review))
        add_findings(review, report);self.assertFalse(batch.review_errors(report, review))
        review["outputs"][0]["reviewed_opening_frame_count"] = 59
        self.assertTrue(batch.review_errors(report, review))
        review["outputs"][0]["reviewed_opening_frame_count"] = 60
        review["batch_review"]["report_digest"] = "old"
        self.assertTrue(batch.review_errors(report, review))


class BatchMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def movie(self, name, color, tone):
        path = self.root / (name + ".mp4")
        subprocess.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=64x96:r=60:d=2",
            "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-y", str(path)], check=True, capture_output=True)
        render = self.root / (name + ".json");auto.write(render, {"actual_output_frames": 120})
        return {"plan_id": name, "output": auto.ref(path), "render_evidence": auto.ref(render)}

    def report(self, rows):
        clean_path = self.root / "clean.json";auto.write(clean_path, {"results": rows})
        value = {"clean_delivery": auto.ref(clean_path), "plan": {"sha256": "plan"}, "batch_review_policy": batch.POLICY}
        clean = auto.read(clean_path);report = auto.clean_batch_evidence(self.root, value, clean)
        return value, clean, report

    def test_actual_same_video_with_different_audio_is_rejected(self):
        rows = [self.movie("A", "blue", 440), self.movie("B", "blue", 880)]
        self.assertNotEqual(rows[0]["output"]["sha256"], rows[1]["output"]["sha256"])
        value, clean, report = self.report(rows)
        self.assertEqual(report["decision"], "reject")
        self.assertIn("DUPLICATE_RENDERED_CLEAN_VISUAL:A|B", report["failures"])
        report["decision"] = "pass";report["failures"] = []
        with self.assertRaisesRegex(ValueError, "duplicate rendered"):
            auto.require_clean_batch(value, clean, {"batch_review_policy": batch.POLICY, "batch_clean": report})

    def test_distinct_rendered_video_passes_and_changed_proof_fails(self):
        value, clean, report = self.report([self.movie("A", "blue", 440), self.movie("B", "red", 440)])
        qc = {"batch_review_policy": batch.POLICY, "batch_clean": report}
        self.assertEqual(report["decision"], "pass");auto.require_clean_batch(value, clean, qc)
        Path(report["results"][0]["proof"]["path"]).write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            auto.require_clean_batch(value, clean, qc)

    def test_incomplete_decoded_frame_sequence_cannot_pass(self):
        value, clean, report = self.report([self.movie("A", "blue", 440)])
        payload = Path(report["results"][0]["proof"]["path"]).read_text()
        with self.assertRaisesRegex(ValueError, "complete decoded"):
            batch.frame_digest(payload, 121)


class BatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        semantic = auto.module("batch_semantic_fixture", "scripts/autonomous/tests/test_semantic_first.py")
        self.fixture = semantic.SemanticFirstTests()
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.runtime = semantic.auto

    def upgrade(self, job):
        a = self.runtime;value = a.state(job)
        order_path = Path(value["work_order"]["path"]);order = a.read(order_path)
        order["batch_review_policy"] = batch.POLICY;a.write(order_path, order)
        value["work_order"] = a.ref(order_path);value["batch_review_policy"] = batch.POLICY
        index = a.read(Path(value["source_index"]["path"]))
        add_analysis(self.fixture.plan, index)
        plan_path = Path(value["plan"]["path"]);a.write(plan_path, self.fixture.plan);value["plan"] = a.ref(plan_path)
        report = a.BATCH.audit(self.fixture.plan, index)
        self.assertEqual(report["decision"], "pass", report["failures"])
        evidence_path = Path(value["plan_evidence"]["path"]);evidence = a.read(evidence_path)
        evidence.update(plan=value["plan"], batch_review_policy=batch.POLICY, batch_review_report=report)
        a.write(evidence_path, evidence);value["plan_evidence"] = a.ref(evidence_path)
        review_path = Path(value["plan_review"]["path"]);review = a.read(review_path)
        review.update(plan_sha256=value["plan"]["sha256"], evidence_sha256=value["plan_evidence"]["sha256"])
        add_findings(review, report);a.write(review_path, review);value["plan_review"] = a.ref(review_path)
        clean_path = Path(value["clean_delivery"]["path"]);clean = a.read(clean_path)
        clean["plan_sha256"] = value["plan"]["sha256"];a.write(clean_path, clean);value["clean_delivery"] = a.ref(clean_path)
        qc_path = Path(value["clean_qc"]["path"]);qc = a.read(qc_path)
        qc.update(clean_delivery=value["clean_delivery"], plan_sha256=value["plan"]["sha256"], batch_review_policy=batch.POLICY,
                  batch_clean=a.clean_batch_evidence(job, value, clean))
        a.write(qc_path, qc);value["clean_qc"] = a.ref(qc_path)
        a.save(job, value, "clean_validated")
        return value

    def final_review(self, job):
        a = self.runtime;path = self.fixture.final_review(job)
        evidence = a.read(Path(a.state(job)["final_evidence"]["path"]));review = a.read(path)
        add_findings(review, evidence["batch_review_report"]);a.write(path, review)
        return path

    def test_new_policy_is_pinned_and_full_repair_invalidates_old_approval(self):
        a = self.runtime;_, value, _ = self.fixture.approval_fixture()
        a.repair(SimpleNamespace(job_dir=self.fixture.root, reason="duplicate batch", continue_until_complete=False))
        repaired = a.state(self.fixture.root)
        self.assertEqual(repaired["batch_review_policy"], batch.POLICY)
        self.assertNotIn("plan_review", repaired)
        self.assertEqual(a.read(Path(repaired["work_order"]["path"]))["batch_review_policy"], batch.POLICY)
        repaired.pop("batch_review_policy");a.save(self.fixture.root, repaired, "repair_required")
        with self.assertRaisesRegex(ValueError, "batch review policy changed"):
            a.state(self.fixture.root)

    def test_missing_new_batch_plan_is_rejected_before_model_or_render(self):
        a = self.runtime
        errors = a.check_plan(self.fixture.plan, self.fixture.source_index(), 1, a.EDITING.POLICY, batch.POLICY)
        self.assertTrue(any("BATCH_REVIEW_POLICY_REQUIRED" in error for error in errors))

    def test_single_output_approval_cannot_authorize_new_batch_policy(self):
        a = self.runtime;review_path, value, review = self.fixture.approval_fixture()
        source_asr = self.fixture.root / "synthetic-source-asr.json"
        a.write(source_asr, {"text": "".join(unit["text"] for unit in self.fixture.plan["outputs"][0]["segments"])})
        index_path = Path(value["source_index"]["path"]);index = a.read(index_path)
        index["sources"][0]["asr"] = a.ref(source_asr);a.write(index_path, index)
        value["source_index"] = a.ref(index_path)
        add_analysis(self.fixture.plan, index)
        plan_path = Path(value["plan"]["path"]);a.write(plan_path, self.fixture.plan);value["plan"] = a.ref(plan_path)
        order_path = Path(value["work_order"]["path"]);order = a.read(order_path)
        order["batch_review_policy"] = batch.POLICY;a.write(order_path, order)
        value.update(work_order=a.ref(order_path), batch_review_policy=batch.POLICY)
        evidence_path = Path(value["plan_evidence"]["path"]);evidence = a.read(evidence_path)
        report = a.BATCH.audit(self.fixture.plan, index)
        evidence.update(plan=value["plan"], source_index=value["source_index"],
                        batch_review_policy=batch.POLICY, batch_review_report=report)
        a.write(evidence_path, evidence);value["plan_evidence"] = a.ref(evidence_path)
        review.update(plan_sha256=value["plan"]["sha256"], evidence_sha256=value["plan_evidence"]["sha256"])
        a.write(review_path, review);a.save(self.fixture.root, value, "plan_evidenced")
        with self.assertRaisesRegex(RuntimeError, "HASH_BOUND_WHOLE_BATCH_REVIEW_REQUIRED"):
            a.approve_plan(SimpleNamespace(job_dir=self.fixture.root, review=review_path))
        self.assertNotIn("plan_review", a.state(self.fixture.root))
        add_findings(review, report);a.write(review_path, review)
        a.save(self.fixture.root, a.state(self.fixture.root), "plan_evidenced")
        a.approve_plan(SimpleNamespace(job_dir=self.fixture.root, review=review_path))
        self.assertEqual(a.state(self.fixture.root)["phase"], "plan_approved")

    def test_new_clean_delivery_publishes_with_retained_fingerprints(self):
        a = self.runtime;job, delivery, pending, asr, packager = self.fixture.delivery_fixture()
        self.upgrade(job)
        with patch.object(a, "load_model"), patch.object(a, "transcribe", return_value=asr), \
                patch.object(a, "continuous_visual", side_effect=self.fixture.mock_continuous):
            a.final_evidence(SimpleNamespace(job_dir=job, clean=True))
        a.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        state = a.state(job);self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["batch_review_policy"], batch.POLICY)
        self.assertEqual(packager.video_spec(delivery / "混剪（无包装）" / "P1.mp4")["frames"], 1200)
        qc = a.read(job / "manifests" / "clean_validation.json")
        self.assertTrue(Path(qc["batch_clean"]["results"][0]["proof"]["path"]).is_file())
        self.assertIn("clean_inputs", qc["batch_clean"]["results"][0]["output"]["path"])

    def test_new_packaged_delivery_and_subtitle_reburn_preserve_batch_contract(self):
        a = self.runtime;job, delivery, pending, asr, packager = self.fixture.delivery_fixture()
        self.upgrade(job)
        config = self.fixture.prepare_subtitles(job, pending, asr, packager)
        a.package(SimpleNamespace(job_dir=job, config=config))
        with patch.object(a, "load_model"), patch.object(a, "transcribe", return_value=asr), \
                patch.object(a, "continuous_visual", side_effect=self.fixture.mock_continuous):
            a.final_evidence(SimpleNamespace(job_dir=job))
        a.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        state = a.state(job);context = a.read(Path(state["reburn_inputs"]["editing_context"]["path"]))
        self.assertEqual(context["batch_review_policy"], batch.POLICY)
        a.reburn(SimpleNamespace(job_dir=job, plan_id="P1", subtitle_txt=delivery / "字幕" / "subtitle-P1.txt"))
        self.fixture.approve_subtitle(job)
        a.package(SimpleNamespace(job_dir=job, config=Path(a.state(job)["reburn_config"]["path"])))
        with patch.object(a, "load_model"), patch.object(a, "transcribe", return_value=asr), \
                patch.object(a, "continuous_visual", side_effect=self.fixture.mock_continuous):
            a.final_evidence(SimpleNamespace(job_dir=job))
        a.complete(SimpleNamespace(job_dir=job, review=self.final_review(job)))
        self.assertEqual(packager.video_spec(delivery / "成片" / "P1.mp4")["frames"], 1200)
        self.assertEqual(packager.video_spec(delivery / "混剪（无包装）" / "P1.mp4")["frames"], 1440)


if __name__ == "__main__":
    unittest.main()
