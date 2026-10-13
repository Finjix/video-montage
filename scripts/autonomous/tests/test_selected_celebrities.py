from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("selected_cast_runtime", ROOT / "scripts/autonomous/scripts/autonomous_montage.py")
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
cast, editing = auto.CAST, auto.EDITING


class SelectedCelebrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source identity fixture")
        self.celebrities = []
        for person in ("alpha", "beta", "gamma"):
            image = self.root / f"{person}.png"
            Image.new("RGBA", (9, 16), (120, 40, 10, 100)).save(image)
            self.celebrities.append({"person_id": f"person:{person}", "name": person, "nameplate": auto.ref(image)})
        texts = ["开局先收集木材，再用资源改善营地温度", "用取得的木材升级熔炉，营地的温度就会提高",
                 "温度提高以后，营地就能吸引更多幸存者", "幸存者安定生活，营地终于恢复了生机"]
        people = ["person:alpha", "person:beta", "person:gamma", "person:alpha"]
        segments = [{"source_path": str(self.source), "source_sha256": auto.sha(self.source),
            "source_in_frame": i * 360, "speech_end_frame": (i + 1) * 360 - 3,
            "source_out_frame_exclusive": (i + 1) * 360, "source_fps_num": 60, "source_fps_den": 1,
            "speed": 1, "text": text, "speaker_id": person, "visible_person_ids": [person],
            "purpose_contract": {"function": "mechanism" if i < 3 else "story_resolution",
                "new_claim_ids": [f"claim-{i}"], "non_redundant": True, "narrative_stage": i,
                **({"closing_payoff": True, "closing_quote": text} if i == 3 else {})}}
            for i, (text, person) in enumerate(zip(texts, people))]
        shots = [{"render_segment_index": 1, "source_sha256": auto.sha(self.source),
            "source_in_frame": i * 360, "source_out_frame_exclusive": (i + 1) * 360,
            "source_visual_shot_id": f"camera-{i}", "visible_person_ids": [person],
            "boundary_kind": "opening" if i == 0 else "source_camera_change"} for i, person in enumerate(people)]
        transitions = [{"relation": "cause_effect", "information_gain": "资源、温度与居民依次建立因果",
            "content_evidence": {"from_quote": left["text"], "to_quote": right["text"],
                "new_information": "后一位讲解者继续说明营地机制的具体结果",
                "entity_flow": "叙述主体是共同讨论的营地机制，讲解者是不同人物",
                "state_flow": "先收集资源改善设施，再改善居民的生存条件",
                "premise_refs": [{"segment_index": i, "quote": left["text"]}],
                "speaker_change": {"from_person_id": left["speaker_id"], "to_person_id": right["speaker_id"],
                    "subject_relation": "shared_topic", "reason": "两位讲解者接续说明营地机制，不把个人账号或经历混成同一主体"}}}
            for i, (left, right) in enumerate(zip(segments, segments[1:]), 1)]
        self.output = {"plan_id": "multi", "cast_policy": cast.POLICY,
            "protagonist_ids": people[:3], "segments": segments, "visual_shots": shots, "transitions": transitions}

    def test_three_celebrities_and_returning_first_person_have_three_intro_windows(self):
        self.assertEqual([], editing.audit_output(self.output))
        summary = editing.summary(self.output)
        self.assertEqual([0, 360, 720], [row["start_frame"] for row in summary["nameplate_windows"]])
        self.assertEqual([192, 552, 912], [row["end_frame_exclusive"] for row in summary["nameplate_windows"]])
        self.assertNotIn("protagonist_id", summary)

    def test_single_and_two_selected_celebrities_use_the_same_contract(self):
        for people in (["person:alpha"] * 4, ["person:alpha", "person:beta", "person:alpha", "person:beta"]):
            output = copy.deepcopy(self.output)
            output["protagonist_ids"] = list(dict.fromkeys(people))
            for segment, shot, person in zip(output["segments"], output["visual_shots"], people):
                segment.update(speaker_id=person, visible_person_ids=[person])
                shot["visible_person_ids"] = [person]
            self.assertEqual([], cast.output_errors(output))
            self.assertEqual(len(set(people)), len(editing.summary(output)["nameplate_windows"]))

    def test_unselected_unknown_duplicate_or_declared_but_unseen_people_are_rejected(self):
        for people in (["person:extra"], ["person:unknown"], [], ["person:alpha", "person:alpha"]):
            output = copy.deepcopy(self.output)
            output["segments"][0]["visible_person_ids"] = people
            self.assertTrue(cast.output_errors(output))
        output = copy.deepcopy(self.output)
        output["protagonist_ids"].append("person:unseen")
        self.assertIn("SELECTED_CAST_NOT_ACTUALLY_VISIBLE", cast.output_errors(output))

    def test_speaker_switch_cannot_claim_a_single_person(self):
        output = copy.deepcopy(self.output)
        change = output["transitions"][0]["content_evidence"]["speaker_change"]
        for key, value in (("subject_relation", "same_person"), ("to_person_id", "person:alpha"), ("reason", "连贯")):
            changed = copy.deepcopy(output)
            changed["transitions"][0]["content_evidence"]["speaker_change"][key] = value
            self.assertTrue(cast.speaker_change_errors(changed))
        change.clear()
        self.assertTrue(cast.speaker_change_errors(output))

    def test_intro_stops_at_person_change_and_short_intro_is_rejected(self):
        output = {"cast_policy": cast.POLICY, "protagonist_ids": ["person:alpha", "person:beta"]}
        shots = [{"visible_person_ids": ["person:alpha"], "output_in_frame": 0, "output_out_frame_exclusive": 120},
                 {"visible_person_ids": ["person:beta"], "output_in_frame": 120, "output_out_frame_exclusive": 480}]
        self.assertEqual(98, cast.nameplate_windows(output, shots)[0]["end_frame_exclusive"])
        shots[0]["output_out_frame_exclusive"] = 56
        shots[1]["output_in_frame"] = 56
        with self.assertRaisesRegex(ValueError, "0.8"):
            cast.nameplate_windows(output, shots)

    def test_blank_gap_uses_final_frames_at_all_speed_rounding_boundaries(self):
        output = {"cast_policy": cast.POLICY, "protagonist_ids": ["person:alpha", "person:beta"]}
        for boundary in range(79, 85):
            shots = [{"visible_person_ids": ["person:alpha"], "output_in_frame": 0, "output_out_frame_exclusive": boundary},
                     {"visible_person_ids": ["person:beta"], "output_in_frame": boundary, "output_out_frame_exclusive": 480}]
            first, second = cast.nameplate_windows(output, shots)
            final = lambda frame: (frame * 5 + 5) // 6
            self.assertGreaterEqual(final(first['end_frame_exclusive']), 48)
            self.assertEqual(18, final(second['start_frame']) - final(first['end_frame_exclusive']))
            self.assertEqual(boundary, second['start_frame'])
        shots[0]['output_out_frame_exclusive'] = shots[1]['output_in_frame'] = 78
        with self.assertRaisesRegex(ValueError, 'blank gap'):
            cast.nameplate_windows(output, shots)

    def test_legacy_cast_timeline_and_summary_are_not_upgraded_by_reburn(self):
        output = {"cast_policy": cast.LEGACY_POLICY, "protagonist_ids": ["person:alpha", "person:beta"]}
        shots = [{"visible_person_ids": ["person:alpha"], "output_in_frame": 0, "output_out_frame_exclusive": 120},
                 {"visible_person_ids": ["person:beta"], "output_in_frame": 120, "output_out_frame_exclusive": 480}]
        self.assertEqual(120, cast.nameplate_windows(output, shots)[0]['end_frame_exclusive'])
        existing = copy.deepcopy(self.output)
        existing['cast_policy'] = cast.LEGACY_POLICY
        self.assertEqual(cast.LEGACY_POLICY, editing.summary(existing)['cast_policy'])

    def test_new_jobs_and_full_repairs_bind_the_blank_gap_policy(self):
        order_path = self.root / 'order.json'
        delivery = self.root / 'work/自动化混剪_20261013_100000'
        job = delivery / '临时文件'
        order = {'schema': auto.ORDER_SCHEMA, 'cast_policy': cast.LEGACY_POLICY,
                 'celebrities': self.celebrities, 'requested_outputs': 40,
                 'sources': [{'path': str(self.source)}], 'asset_root': str(self.root),
                 'output_root': str(delivery.parent)}
        auto.write(order_path, order)
        auto.init(SimpleNamespace(work_order=order_path, job_dir=job))
        value = auto.state(job)
        self.assertEqual(cast.POLICY, value['cast_policy'])
        # Simulate a previously initialized v1 job and perform a full edit repair.
        snapshot = auto.read(Path(value['work_order']['path']))
        snapshot['cast_policy'] = cast.LEGACY_POLICY
        auto.write(Path(value['work_order']['path']), snapshot)
        value['work_order'] = auto.ref(Path(value['work_order']['path']))
        value['cast_policy'] = cast.LEGACY_POLICY
        auto.write(job / 'autonomous_state.json', value)
        auto.repair(SimpleNamespace(job_dir=job, reason='reselect introductions', continue_until_complete=False))
        repaired = auto.state(job)
        self.assertEqual(cast.POLICY, repaired['cast_policy'])
        self.assertEqual(cast.POLICY, auto.read(Path(repaired['work_order']['path']))['cast_policy'])

    def test_personal_accounts_cannot_be_combined_by_shared_topic_or_cause_effect(self):
        output = copy.deepcopy(self.output)
        output['segments'][0]['text'] = '我的账号招来了十个幸存者'
        output['segments'][1]['text'] = '然后我的账号就升级到了三十级'
        self.assertTrue(any('PERSONAL_ACCOUNT' in error for error in cast.speaker_change_errors(output)))
        output['transitions'][0]['content_evidence']['speaker_change']['subject_relation'] = 'parallel_experience'
        self.assertTrue(any('PERSONAL_ACCOUNT' in error for error in cast.speaker_change_errors(output)))
        output['segments'][1]['text'] = '我自己的账号则选择先升级熔炉'
        output['transitions'][0]['relation'] = 'contrast'
        self.assertFalse(any('PERSONAL_ACCOUNT' in error for error in cast.speaker_change_errors(output)))

    def test_registry_and_nameplate_hash_timeline_cannot_change(self):
        order = {"cast_policy": cast.POLICY, "celebrities": self.celebrities}
        registry = cast.registry(order)
        summary = editing.summary(self.output)
        layers = [{**window, **next(row["nameplate"] for row in registry if row["person_id"] == window["person_id"])}
                  for window in summary["nameplate_windows"]]
        cast.verify_nameplates(self.output, summary["visual_shots"], layers, registry)
        for changed in (layers[:-1], layers + [layers[0]], [dict(layers[0], start_frame=1), *layers[1:]]):
            with self.assertRaises(ValueError):
                cast.verify_nameplates(self.output, summary["visual_shots"], changed, registry)
        Path(registry[0]["nameplate"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            cast.registry(order)

    def test_legacy_review_does_not_approve_selected_cast(self):
        finding = {"protagonist_only_pass": True, "protagonist_id": "person:alpha"}
        self.assertFalse(cast.review_cast(self.output, finding))
        self.assertTrue(cast.review_cast(self.output, {"selected_cast_pass": True,
            "protagonist_ids": self.output["protagonist_ids"], "protagonist_primary_pass": True,
            "protagonist_primary_reason": "三个讲解镜头均由所选明星正面讲解，画面注意力跟随各自表演"}))

    def with_supporting_actor(self):
        output = copy.deepcopy(self.output)
        output["supporting_person_ids"] = ["person:extra-01"]
        for row in (output["segments"][0], output["visual_shots"][0]):
            row["visible_person_ids"].append("person:extra-01")
            row["primary_person_ids"] = ["person:alpha"]
        return output

    def test_supporting_actor_beside_selected_subject_does_not_add_nameplate_or_celebrity(self):
        output = self.with_supporting_actor()
        self.assertEqual([], editing.audit_output(output))
        summary = editing.summary(output)
        self.assertEqual(output["protagonist_ids"], summary["protagonist_ids"])
        self.assertEqual(["person:extra-01"], summary["supporting_person_ids"])
        self.assertEqual(output["protagonist_ids"], [w["person_id"] for w in summary["nameplate_windows"]])

    def test_supporting_actor_needs_selected_subject_and_explicit_primary_declaration(self):
        for visible, primary in ((["person:extra-01"], ["person:extra-01"]),
                (["person:alpha", "person:extra-01"], ["person:extra-01"]),
                (["person:alpha", "person:extra-01"], []),
                (["person:alpha", "person:unknown"], ["person:alpha"])):
            output = self.with_supporting_actor()
            output["segments"][0].update(visible_person_ids=visible, primary_person_ids=primary)
            self.assertTrue(cast.output_errors(output))
        output = self.with_supporting_actor()
        output["visual_shots"][0].pop("primary_person_ids")
        self.assertTrue(editing.audit_output(output))
        output = self.with_supporting_actor()
        output["supporting_person_ids"] = ["person:unknown"]
        self.assertTrue(cast.output_errors(output))

    def test_primary_focus_requires_fresh_specific_review_and_exact_binding(self):
        output = self.with_supporting_actor()
        finding = {"selected_cast_pass": True, "visible_person_ids": ["person:alpha", "person:extra-01"],
            "primary_person_ids": ["person:alpha"], "protagonist_primary_pass": True,
            "protagonist_primary_reason": "明星位于前景并正面讲解，旁边群演只作侧身反应且没有遮挡明星"}
        self.assertTrue(cast.review_cast(output, finding, finding["visible_person_ids"], ["person:alpha"]))
        for key, value in (("protagonist_primary_pass", False), ("protagonist_primary_reason", "通过"),
                           ("primary_person_ids", ["person:extra-01"]), ("visible_person_ids", ["person:alpha"])):
            changed = dict(finding, **{key: value})
            self.assertFalse(cast.review_cast(output, changed, finding["visible_person_ids"], ["person:alpha"]))
        whole = {**finding, "protagonist_ids": output["protagonist_ids"],
                 "supporting_person_ids": output["supporting_person_ids"]}
        self.assertTrue(cast.review_cast(output, whole))
        whole.pop("supporting_person_ids")
        self.assertFalse(cast.review_cast(output, whole))

    def test_supporting_entry_exit_does_not_cut_off_star_nameplate(self):
        output = {"cast_policy": cast.POLICY, "protagonist_ids": ["person:alpha", "person:beta"],
                  "supporting_person_ids": ["person:extra-01"]}
        shots = [{"visible_person_ids": ["person:alpha", "person:extra-01"],
                  "primary_person_ids": ["person:alpha"], "output_in_frame": 0, "output_out_frame_exclusive": 60},
                 {"visible_person_ids": ["person:alpha"], "output_in_frame": 60, "output_out_frame_exclusive": 120},
                 {"visible_person_ids": ["person:beta", "person:extra-01"],
                  "primary_person_ids": ["person:beta"], "output_in_frame": 120, "output_out_frame_exclusive": 480}]
        first, second = cast.nameplate_windows(output, shots)
        self.assertEqual(("person:alpha", 0, 98), tuple(first.values()))
        self.assertEqual("person:beta", second["person_id"])
        self.assertEqual(18, (second["start_frame"] * 5 + 5) // 6 - (first["end_frame_exclusive"] * 5 + 5) // 6)

    def test_plan_and_final_review_cannot_omit_primary_focus_check(self):
        output = self.with_supporting_actor()
        plan = {"planning_policy": editing.POLICY, "outputs": [output]}
        focus = {"selected_cast_pass": True, "protagonist_primary_pass": True,
                 "protagonist_primary_reason": "明星在前景正面讲解，旁边群演只做侧身反应，不遮挡或抢占主体"}
        whole = {**focus, "plan_id": "multi", "protagonist_ids": output["protagonist_ids"],
                 "supporting_person_ids": output["supporting_person_ids"], "visual_shot_count": 4,
                 "semantic_pass": True, "visual_shots_pass": True, "narrative_pass": True,
                 "semantic_reason": "从资源获得承接设施升级到居民生活", "cast_reason": "讲解者依次出镜且群演只陪衬",
                 "shot_reason": "四个真实机位覆盖全部区间", "narrative_reason": "资源用于熔炉升级，改善温度后吸引居民，最后说明安定生活"}
        evidence = {"results": [{"plan_id": "multi", "segment_index": i,
            "continuous_visual": {"sha256": f"{i:064x}"}} for i in range(1, 5)]}
        review = {"outputs": [whole],
            "transitions": [{"plan_id": "multi", "transition_index": i,
                "continuity_pass": True, "relation_supported": True, "missing_premise": False,
                "reason": "前段建立的资源与温度条件支持后段介绍的生活结果", "speaker_change_pass": True,
                "speaker_change_reason": "不同讲解者接续讨论公共营地机制，没有合并个人账号或经历"} for i in range(1, 4)],
            "segments": [{**focus, "plan_id": "multi", "segment_index": i,
                "visible_person_ids": row["visible_person_ids"], "primary_person_ids": cast.primary_people(output, row),
                "continuous_visual_sha256": f"{i:064x}", "cast_reason": "讲解明星在前景，群演陪衬且无其他人物"}
                for i, row in enumerate(output["segments"], 1)],
            "visual_shots": [{**focus, "plan_id": "multi", "shot_index": i, "shot_pass": True,
                "source_visual_shot_id": row["source_visual_shot_id"], "visible_person_ids": row["visible_person_ids"],
                "primary_person_ids": cast.primary_people(output, row), "reason": "明星主导画面和表演，群演只是陪衬",
                "boundary_reason": "实际原片机位切换，未拆连续镜头凑数"}
                for i, row in enumerate(output["visual_shots"], 1)]}
        self.assertEqual([], editing.plan_review_errors(plan, evidence, review))
        for scope in ("outputs", "segments", "visual_shots"):
            changed = copy.deepcopy(review)
            changed[scope][0].pop("protagonist_primary_pass")
            self.assertTrue(editing.plan_review_errors(plan, evidence, changed))
        # Completion reuses the whole-edit contract against fresh encoded-frame evidence.
        self.assertEqual([], editing.output_review_errors(output, whole))
        changed = dict(whole, protagonist_primary_pass=False)
        self.assertTrue(editing.output_review_errors(output, changed))

    def test_historical_v2_retains_exclusion_and_blank_gap_contract(self):
        output = self.with_supporting_actor()
        output["cast_policy"] = cast.STRICT_POLICY
        self.assertTrue(cast.output_errors(output))
        output = copy.deepcopy(self.output)
        output["cast_policy"] = cast.STRICT_POLICY
        self.assertEqual([], editing.audit_output(output))
        self.assertTrue(cast.review_cast(output, {"selected_cast_pass": True,
            "protagonist_ids": output["protagonist_ids"]}))

    def test_policy_registry_inventory_speaker_and_shot_bindings_are_required(self):
        plan = {"schema": auto.PLAN_SCHEMA, "planning_policy": editing.POLICY, "cast_policy": cast.POLICY,
                "celebrities": self.celebrities, "outputs": [self.output],
                "batch_analysis": {"sources": [{"source_sha256": auto.sha(self.source),
                    "candidates": [dict(segment, status="eligible") for segment in self.output["segments"]]}]}}
        index = {"planning_policy": editing.POLICY, "cast_policy": cast.POLICY, "celebrities": self.celebrities,
                 "sources": [{"source": auto.ref(self.source), "video": {"fps_num": 60, "fps_den": 1, "frames": 1440}}]}
        self.assertEqual([], auto.check_plan(plan, index, 1, editing.POLICY))
        changed = copy.deepcopy(plan); changed.pop("cast_policy")
        self.assertIn("CAST_POLICY_BINDING_REQUIRED", auto.check_plan(changed, index, 1, editing.POLICY))
        changed = copy.deepcopy(plan); changed["outputs"][0]["segments"][0]["speaker_id"] = "person:beta"
        self.assertIn("SOURCE_CANDIDATE_CAST_AND_SPEAKER_BINDING_REQUIRED", auto.check_plan(changed, index, 1, editing.POLICY))
        changed = copy.deepcopy(plan); changed["outputs"][0]["visual_shots"][0]["visible_person_ids"] = ["person:beta"]
        self.assertTrue(any("SOURCE_SEGMENT_CAST_MISMATCH" in error for error in auto.check_plan(changed, index, 1, editing.POLICY)))
        supported = copy.deepcopy(plan)
        supported["outputs"] = [self.with_supporting_actor()]
        supported["batch_analysis"]["sources"][0]["candidates"] = [
            dict(segment, status="eligible") for segment in supported["outputs"][0]["segments"]]
        self.assertEqual([], auto.check_plan(supported, index, 1, editing.POLICY))
        changed = copy.deepcopy(supported)
        changed["batch_analysis"]["sources"][0]["candidates"][0]["primary_person_ids"] = ["person:extra-01"]
        self.assertIn("SOURCE_CANDIDATE_CAST_AND_SPEAKER_BINDING_REQUIRED", auto.check_plan(changed, index, 1, editing.POLICY))
        changed = copy.deepcopy(supported)
        changed["outputs"][0]["supporting_person_ids"] = ["person:gamma"]
        self.assertIn("REGISTERED_CELEBRITY_CANNOT_BE_SUPPORTING_ACTOR", auto.check_plan(changed, index, 1, editing.POLICY))


if __name__ == "__main__":
    unittest.main()
