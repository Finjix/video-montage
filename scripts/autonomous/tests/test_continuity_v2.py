"""Spoken grounding, final-review binding and opening-cap regressions."""
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("continuity_v2_runtime", Path(__file__).resolve().parents[1] / "scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
editing, batch = auto.EDITING, auto.BATCH


def spoken_output(texts):
    return {"plan_id": "sample", "segments": [{"text": quote, "purpose_contract": {"function": "mechanism"}}
                for quote in texts],
            "transitions": [{"relation": "cause_effect", "information_gain": "所述措施改善营地生活",
                "content_evidence": {"from_quote": left, "to_quote": right,
                    "new_information": "后续行动及结果体现了资源对营地的作用",
                    "entity_flow": "木材、营地设施及营地居民是前后叙述的主体",
                    "state_flow": "收集资源以后建设设施，让居民获得稳定生活",
                    "premise_refs": [{"segment_index": index, "quote": left}]}}
                for index, (left, right) in enumerate(zip(texts, texts[1:]), 1)]}


class SpokenGroundingTests(unittest.TestCase):
    def test_feedback_is_rejected_even_with_valid_quotes_and_new_annotations(self):
        for intro in ("实不相瞒啊,朋友们,我也是刷着广告才入坑的无尽冬日,",
                      "无尽冬日可是全球三亿人都在玩的冰雪游戏。"):
            output = spoken_output([intro, "只有砍树才能获取资源,这才能建造厨房和庇护所,"])
            for relation in ("explanation", "progression", "speaker_experience"):
                output["transitions"][0]["relation"] = relation
                self.assertIn("transition:1:DISCOVERY_TO_GAMEPLAY_SPOKEN_BRIDGE_REQUIRED",
                              editing.grounded_transition_errors(output))
            # The old validator accepts the supplied quotes: this tests the
            # actual missing relation, not merely the absence of a new field.
            self.assertEqual([], editing.semantic._validate_transition_evidence(
                output["plan_id"], 0, *output["segments"], output["transitions"][0]))

    def test_real_retained_bridge_and_gameplay_chain_pass(self):
        output = spoken_output(["我也是刷着广告才入坑的无尽冬日",
            "这个游戏开局先让幸存者砍树，取得木材资源",
            "把取得的资源用于升级熔炉，提升营地温度",
            "温度提升以后才能吸引更多的幸存者"])
        output["transitions"][0]["content_evidence"]["bridge_ref"] = {
            "segment_index": 2, "quote": output["segments"][1]["text"]}
        self.assertEqual([], editing.grounded_transition_errors(output))

    def test_bridge_can_be_in_the_retained_intro(self):
        output = spoken_output(["刷着广告入坑以后才发现，这个游戏开局要先收集资源",
                                "只有砍树才能获取资源，这才能建造厨房和庇护所"])
        output["transitions"][0]["content_evidence"]["bridge_ref"] = {
            "segment_index": 1, "quote": "这个游戏开局要先收集资源"}
        self.assertEqual([], editing.grounded_transition_errors(output))

    def test_omitted_future_or_editorial_quotes_cannot_be_premises(self):
        output = spoken_output(["先收集木材，再把资源投入熔炉", "温度提升以后大家才能安心生活"])
        for refs in ([], [{"segment_index": 2, "quote": output["segments"][1]["text"]}],
                     [{"segment_index": 1, "quote": "游戏一开始就需要派人砍树"}],
                     [{"segment_index": True, "quote": output["segments"][0]["text"]}],
                     [{"segment_index": 1, "quote": "木材"}]):
            output["transitions"][0]["content_evidence"]["premise_refs"] = refs
            self.assertIn("transition:1:RETAINED_PREMISE_REQUIRED", editing.grounded_transition_errors(output))

    def test_keyword_or_unselected_bridge_is_rejected(self):
        output = spoken_output(["刷着广告才入坑的无尽冬日", "只有砍树才能获取资源，这才能建造厨房和庇护所"])
        for bridge in ({"segment_index": 2, "quote": "砍树才能获取资源"},
                       {"segment_index": 1, "quote": "游戏开局先砍树"},
                       {"segment_index": 3, "quote": "游戏开局先砍树"}):
            output["transitions"][0]["content_evidence"]["bridge_ref"] = bridge
            self.assertIn("transition:1:DISCOVERY_TO_GAMEPLAY_SPOKEN_BRIDGE_REQUIRED",
                          editing.grounded_transition_errors(output))

    def test_copied_or_generic_flows_do_not_prove_a_relation(self):
        output = spoken_output(["开局收集木材把营地暖起来", "温度稳定以后大家才能安心生活"])
        for value in ("同一游戏同一人物", "接着介绍增加新的信息", "入坑经历转向木材用途"):
            evidence = output["transitions"][0]["content_evidence"]
            evidence.update(entity_flow=value, state_flow=value)
            self.assertIn("transition:1:SPECIFIC_ENTITY_AND_STATE_FLOW_REQUIRED",
                          editing.grounded_transition_errors(output))

    def final_fixture(self):
        output = spoken_output(["开局收集木材把营地暖起来", "温度稳定以后大家才能安心生活"])
        plan = {"planning_policy": editing.POLICY, "outputs": [output]}
        row = {"plan_id": "sample", "output": {"sha256": "encoded-final"},
               "asr": {"text": "".join(unit["text"] for unit in output["segments"])},
               "mix_evidence": {"decision": "pass"}, "speech_preserved": True}
        row["transitions"] = editing.final_transition_evidence(output, row)
        review = {"transitions": [{"plan_id": "sample", "transition_index": 1,
            "continuity_pass": True, "relation_supported": True, "missing_premise": False,
            "reason": "实际成片保留收集木材提高温度的前提，居民安定承接其结果",
            "output_sha256": row["output"]["sha256"],
            "speech_evidence_digest": row["transitions"][0]["speech_evidence_digest"]}]}
        return plan, {"results": [row]}, review

    def test_complete_final_transition_scope_and_real_speech_binding(self):
        plan, evidence, review = self.final_fixture()
        self.assertEqual([], editing.transition_review_errors(plan, review, evidence))
        for key, value in (("relation_supported", False), ("missing_premise", True),
                           ("reason", "同一游戏同一人物"), ("output_sha256", "pre-speed-output"),
                           ("speech_evidence_digest", "old-asr")):
            changed = copy.deepcopy(review);changed["transitions"][0][key] = value
            self.assertTrue(editing.transition_review_errors(plan, changed, evidence))
        for findings in ([], review["transitions"] * 2):
            self.assertTrue(editing.transition_review_errors(plan, {"transitions": findings}, evidence))
        evidence["results"][0]["asr"]["text"] = "changed actual encoded speech"
        self.assertTrue(editing.transition_review_errors(plan, review, evidence))

    def test_v1_plan_and_review_keep_the_original_contract(self):
        fixture_module = auto.module("continuity_v1_fixture", "scripts/autonomous/tests/test_semantic_first.py")
        fixture = fixture_module.SemanticFirstTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture.plan["planning_policy"] = editing.LEGACY_POLICY
        fixture.plan["outputs"][0]["transitions"][0]["content_evidence"].pop("premise_refs")
        index = fixture.source_index();index.pop("planning_policy")
        self.assertEqual([], auto.check_plan(fixture.plan, index, 1, editing.LEGACY_POLICY))
        self.assertTrue(auto.check_plan(fixture.plan, index, 1, editing.POLICY))
        output = fixture.plan["outputs"][0];finding = fixture.findings(output)
        finding.pop("narrative_pass");finding.pop("narrative_reason")
        self.assertEqual([], editing.output_review_errors(output, finding, editing.LEGACY_POLICY))
        self.assertTrue(editing.output_review_errors(output, finding, editing.POLICY))
        self.assertTrue(auto.semantic_first({"planning_policy": editing.LEGACY_POLICY}))


class OpeningV2Tests(unittest.TestCase):
    def make_batch(self, count=5):
        module = auto.module("opening_v2_fixture", "scripts/autonomous/tests/test_batch_review.py")
        plan, index = module.BatchPlanTests().make_batch(count)
        for output in plan["outputs"]:
            output["segments"][0]["text"] = "窗外暴雪来袭，大家急需一个温暖的营地"
            output["segments"][0].pop("semantic_cluster_id", None)
        module.add_analysis(plan, index)
        return plan, index, module

    def test_hard_cap_four_includes_reasonable_reuse_and_cross_source_text(self):
        for count in (4, 5):
            for authorized in (False, True):
                plan, index, module = self.make_batch(count)
                if authorized:
                    module.BatchPlanTests().authorize_reuse(plan, index)
                report = batch.audit(plan, index)
                codes = {row["code"] for row in report["failures"]}
                self.assertEqual(count > 4, "OPENING_REUSE_EXCEEDED" in codes)
                self.assertEqual(report["limits"]["opening_uses"], 4)
                self.assertEqual(report["opening_usage"]["speech"][0]["count"], count)
                self.assertEqual(len(report["opening_usage"]["speech"][0]["plan_ids"]), count)

    def test_split_turns_jitter_and_new_ids_do_not_make_new_openings(self):
        plan, index, _ = self.make_batch()
        for n, output in enumerate(plan["outputs"]):
            original = output["segments"].pop(0)
            left, right = copy.deepcopy(original), copy.deepcopy(original)
            cut = n + 3
            left.update(text=original["text"][:cut], source_out_frame_exclusive=280, segment_id=f"new-left-{n}")
            right.update(text=original["text"][cut:], source_in_frame=280, segment_id=f"new-right-{n}")
            output["segments"][:0] = [left, right]
        report = batch.audit(plan, index)
        self.assertIn("OPENING_REUSE_EXCEEDED", {row["code"] for row in report["failures"]})
        self.assertEqual(5, report["opening_usage"]["speech"][0]["count"])
        for output in plan["outputs"]:
            output["segments"][0]["source_in_frame"] += 1
            output["segments"][1]["source_out_frame_exclusive"] += 1
        self.assertEqual(5, batch.audit(plan, index)["opening_usage"]["speech"][0]["count"])

    def test_visual_cap_is_four_even_with_reuse_authorization(self):
        for count in (4, 5):
            plan, index, module = self.make_batch(count)
            for output in plan["outputs"]:
                output["visual_shots"][0]["visual_family_id"] = "shared-continuous-camera"
            module.add_analysis(plan, index)
            module.BatchPlanTests().authorize_reuse(plan, index)
            report = batch.audit(plan, index)
            self.assertEqual(count > 4, "OPENING_VISUAL_FAMILY_REUSE_EXCEEDED" in {row["code"] for row in report["failures"]})

    def test_missing_inventory_shortage_and_downgraded_review_fail(self):
        plan, index, module = self.make_batch(4)
        opening_sources = {output["segments"][0]["source_sha256"] for output in plan["outputs"]}
        for entry in plan["batch_analysis"]["sources"]:
            if entry["source_sha256"] not in opening_sources:
                for turn in entry["spoken_turns"]:
                    turn["opening_eligible"] = False
        report = batch.audit(plan, index, 40)
        self.assertEqual(36, report["capacity"]["planned_shortfall"])
        self.assertGreater(report["capacity"]["opening_shortfall"], 0)
        review = {"outputs": [{"plan_id": row["plan_id"]} for row in plan["outputs"]]}
        module.add_findings(review, report)
        review["batch_review_policy"] = batch.LEGACY_POLICY
        self.assertTrue(batch.review_errors(report, review))
        plan["batch_analysis"]["sources"][0].pop("spoken_turns")
        self.assertIn("COMPLETE_SPOKEN_TURN_INVENTORY_REQUIRED", {row["code"] for row in batch.audit(plan, index)["failures"]})

    def test_renamed_overlapping_or_non_object_turns_are_rejected(self):
        plan, index, _ = self.make_batch(4)
        entry = plan["batch_analysis"]["sources"][0]
        alias = copy.deepcopy(entry["spoken_turns"][0]);alias["source_in_frame"] += 1
        alias["source_out_frame_exclusive"] += 1;alias["turn_id"] = "invented-new-turn"
        entry["spoken_turns"].append(alias)
        self.assertIn("OVERLAPPING_CANONICAL_SPOKEN_TURNS", {row["code"] for row in batch.audit(plan, index)["failures"]})
        entry["spoken_turns"] = ["not a turn object"]
        self.assertIn("SPOKEN_TURN_INVENTORY_INVALID", {row["code"] for row in batch.audit(plan, index)["failures"]})

    def test_v1_keeps_seven_authorized_openings_and_report_shape(self):
        plan, index, module = self.make_batch(5)
        module.BatchPlanTests().authorize_reuse(plan, index)
        plan["batch_review_policy"] = batch.LEGACY_POLICY
        index.pop("batch_review_policy")
        for entry in plan["batch_analysis"]["sources"]:
            entry.pop("spoken_turns")
        report = batch.audit(plan, index)
        self.assertEqual(report["limits"]["opening_uses"], 7)
        self.assertEqual(report["schema"], batch.LEGACY_POLICY)
        self.assertNotIn("opening_usage", report)
        self.assertNotIn("capacity", report)
        self.assertNotIn("OPENING_REUSE_EXCEEDED", {row["code"] for row in report["failures"]})


class VersionedWorkflowTests(unittest.TestCase):
    def setUp(self):
        module = auto.module("versioned_editing_fixture", "scripts/autonomous/tests/test_semantic_first.py")
        self.fixture = module.SemanticFirstTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.runtime = module.auto

    def final_evidence(self, job, asr):
        a = self.runtime
        with patch.object(a, "load_model"), patch.object(a, "transcribe", return_value=asr), \
                patch.object(a, "continuous_visual", side_effect=self.fixture.mock_continuous):
            a.final_evidence(SimpleNamespace(job_dir=job))

    def test_v1_packaged_completion_and_subtitle_reburn_stay_v1(self):
        fixture, a = self.fixture, self.runtime
        fixture.policy = a.EDITING.LEGACY_POLICY
        job, delivery, pending, asr, packager = fixture.delivery_fixture()
        config = fixture.prepare_subtitles(job, pending, asr, packager)
        a.package(SimpleNamespace(job_dir=job, config=config))
        self.final_evidence(job, asr)
        a.complete(SimpleNamespace(job_dir=job, review=fixture.final_review(job)))
        state = a.state(job)
        self.assertEqual(a.EDITING.LEGACY_POLICY, state["planning_policy"])
        a.reburn(SimpleNamespace(job_dir=job, plan_id="P1", subtitle_txt=delivery / "字幕" / "subtitle-P1.txt"))
        fixture.approve_subtitle(job)
        a.package(SimpleNamespace(job_dir=job, config=Path(a.state(job)["reburn_config"]["path"])))
        self.final_evidence(job, asr)
        evidence = a.read(Path(a.state(job)["final_evidence"]["path"]))
        self.assertEqual(a.EDITING.LEGACY_POLICY, evidence["planning_policy"])
        self.assertNotIn("transitions", evidence["results"][0])
        a.complete(SimpleNamespace(job_dir=job, review=fixture.final_review(job)))
        self.assertEqual(a.EDITING.LEGACY_POLICY, a.state(job)["planning_policy"])

    def test_full_repair_upgrades_v1_and_revokes_old_approvals(self):
        fixture, a = self.fixture, self.runtime
        fixture.policy = a.EDITING.LEGACY_POLICY;fixture.plan = fixture.make_plan()
        _, value, _ = fixture.approval_fixture()
        a.repair(SimpleNamespace(job_dir=fixture.root, reason="semantic continuity", continue_until_complete=False))
        value = a.state(fixture.root)
        self.assertEqual(a.EDITING.POLICY, value["planning_policy"])
        self.assertEqual(a.BATCH.POLICY, value["batch_review_policy"])
        self.assertNotIn("plan_review", value)
        self.assertNotIn("plan", value)

    def test_final_completion_rejects_missing_transition_review(self):
        fixture, a = self.fixture, self.runtime
        job, delivery, pending, asr, packager = fixture.delivery_fixture()
        previous_delivery_hash = a.sha(delivery / "成片" / "P1.mp4")
        config = fixture.prepare_subtitles(job, pending, asr, packager)
        a.package(SimpleNamespace(job_dir=job, config=config))
        self.final_evidence(job, asr)
        path = fixture.final_review(job);review = a.read(path);review.pop("transitions");a.write(path, review)
        with self.assertRaisesRegex(RuntimeError, "codex_final_transition_review"):
            a.complete(SimpleNamespace(job_dir=job, review=path))
        self.assertEqual(previous_delivery_hash, a.sha(delivery / "成片" / "P1.mp4"))

    def test_state_and_source_index_cannot_drop_or_downgrade_policies(self):
        fixture, a = self.fixture, self.runtime
        _, value, _ = fixture.approval_fixture()
        plan_path = Path(value["plan"]["path"])
        plan = a.read(plan_path);plan["planning_policy"] = a.EDITING.LEGACY_POLICY
        a.write(plan_path, plan);value["plan"] = a.ref(plan_path)
        a.write(fixture.root / "autonomous_state.json", value)
        with self.assertRaisesRegex(ValueError, "downgrade"):
            a.state(fixture.root)
        index = fixture.source_index();index.pop("planning_policy")
        self.assertTrue(any("source-index planning" in error for error in a.check_plan(fixture.plan, index, 1, a.EDITING.POLICY)))

    def test_canonical_turn_must_come_from_hash_bound_complete_asr(self):
        fixture, a = self.fixture, self.runtime
        module = a.module("source_turn_fixture", "scripts/autonomous/tests/test_batch_review.py")
        index = fixture.source_index()
        source_asr = fixture.root / "complete-asr.json"
        a.write(source_asr, {"asr": {"text": "".join(unit["text"] for unit in fixture.plan["outputs"][0]["segments"])}})
        index["sources"][0]["asr"] = a.ref(source_asr)
        module.add_analysis(fixture.plan, index)
        self.assertEqual([], a.check_plan(fixture.plan, index, 1, a.EDITING.POLICY, a.BATCH.POLICY))
        fixture.plan["batch_analysis"]["sources"][0]["spoken_turns"][0]["text"] = "不存在于原声的完整介绍和开头"
        self.assertTrue(any("SPOKEN_TURN_NOT_IN_BOUND_SOURCE_ASR" in error
                            for error in a.check_plan(fixture.plan, index, 1, a.EDITING.POLICY, a.BATCH.POLICY)))
        a.write(source_asr, {"asr": {"text": "changed transcript"}})
        self.assertTrue(any("SOURCE_ASR_BINDING_INVALID" in error
                            for error in a.check_plan(fixture.plan, index, 1, a.EDITING.POLICY, a.BATCH.POLICY)))

    def test_new_init_rejects_explicit_legacy_policies_before_side_effects(self):
        a = self.runtime
        path = self.fixture.root / "new-order.json"
        for field, policy in (("planning_policy", a.EDITING.LEGACY_POLICY),
                              ("batch_review_policy", a.BATCH.LEGACY_POLICY)):
            a.write(path, {"schema": a.ORDER_SCHEMA, "sources": [{"path": "unused.mp4"}],
                           "requested_outputs": 1, field: policy})
            with self.assertRaisesRegex(ValueError, "new jobs require"):
                a.init(SimpleNamespace(work_order=path))


class SpokenSpellingV2Tests(unittest.TestCase):
    def test_bounded_variants_are_supported_only_for_v2(self):
        for left,right in [("才能慢慢的在冰雪末日里活下去","才能慢慢地在冰雪末日里活下去"),
                           ("幸存者乌央乌央的来呀","幸存者乌央乌央地来呀"),
                           ("你还愁幸存者不来啊","你还愁幸存者不来呀")]:
            self.assertTrue(auto.v2_spoken_spelling_match(left,right,editing.POLICY))
            self.assertFalse(auto.v2_spoken_spelling_match(left,right,editing.LEGACY_POLICY))
            self.assertFalse(auto.v2_spoken_spelling_match(left,right,None))

    def test_spelling_does_not_allow_word_loss_or_other_meanings(self):
        for left,right in [("慢慢的在冰原生存","慢地在冰原生存"),
                           ("乌央乌央的来呀","乌央乌央地来"),
                           ("资源在地板上","资源在的板上"),
                           ("幸存者不来啊","幸存者都来呀"),
                           ("开局砍树","开局开火")]:
            self.assertFalse(auto.v2_spoken_spelling_match(left,right,editing.POLICY))


if __name__ == "__main__":
    unittest.main()
