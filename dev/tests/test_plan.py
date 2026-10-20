"""Plan resolution: cut solver, edit shape, cast, nameplates and source minimum."""
import unittest

import helpers
from montage import plan as PLAN


def clips(*pairs):
    return [{"source": s, "units": u} for s, u in pairs]


GOOD = clips(("S01", "U01"), ("S01", "U02"), ("S02", "U01-U02"), ("S03", "U01"), ("S03", "U02"))


class PlanTests(unittest.TestCase):
    def resolve(self, outputs, sources=None, decisions=None):
        directory, root, state = helpers.job(sources or helpers.standard_sources(), len(outputs), decisions)
        self.addCleanup(directory.cleanup)
        return PLAN.resolve({"outputs": outputs}, root, state)

    def output(self, clip_list, pid="P01"):
        return {"plan_id": pid, "clips": clip_list, "transitions": helpers.transitions(len(clip_list) - 1)}

    def test_good_plan_resolves_inside_cut_windows(self):
        outputs, errors, _ = self.resolve([self.output(GOOD)])
        self.assertEqual(errors, {})
        result = outputs[0]
        self.assertTrue(PLAN.MIN_FRAMES <= result["frames"] <= PLAN.MAX_FRAMES)
        lengths = [shot["frames"] for shot in result["shots"]]
        self.assertGreaterEqual(min(lengths), PLAN.MIN_SHOT)
        self.assertLessEqual(max(lengths) - min(lengths), PLAN.MAX_SPREAD)
        sources = {s["id"]: s for s in helpers.standard_sources()}
        for clip in result["clips"]:
            units = {u["id"]: u for u in sources[clip["source"]]["units"]}
            first, last = units[clip["units"][0]], units[clip["units"][-1]]
            self.assertTrue(first["in_frames"][0][0] <= clip["in"] <= first["in_frames"][0][1])
            self.assertTrue(last["out_frames"][0][0] <= clip["out"] <= last["out_frames"][0][1])
        self.assertEqual(result["clips"][0]["out_in"], 0)
        self.assertEqual(result["clips"][-1]["out_out"], result["frames"])

    def test_too_short_is_explained(self):
        _, errors, _ = self.resolve([self.output(clips(("S01", "U01"), ("S02", "U01"), ("S03", "U02")))])
        self.assertTrue(any("needs >= 1020" in e for e in errors["P01"]), errors)

    def test_jump_cut_inside_one_camera_shot(self):
        sources = helpers.standard_sources()
        sources[0]["cuts"] = []
        sources[0]["shots"] = [{**sources[0]["shots"][0], "end": 6000}]
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("jump cut" in e for e in errors["P01"]), errors)

    def test_source_minimum_and_user_waiver(self):
        two = clips(("S01", "U01-U02"), ("S02", "U01-U02"), ("S01", "U03-U04"))
        sources = helpers.standard_sources()
        sources[0]["beats"][3]["role"] = "payoff"
        _, errors, _ = self.resolve([self.output(two)], sources)
        self.assertTrue(any("needs >= 3" in e for e in errors["P01"]), errors)
        _, errors, _ = self.resolve([self.output(two)], sources, decisions={"min_sources_per_output": {"value": 2, "instruction": "两个也行"}})
        self.assertNotIn("P01", errors)

    def test_unit_that_cannot_start_a_clip(self):
        sources = helpers.standard_sources()
        sources[1]["units"][0]["in_frames"] = []
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("cannot start a clip" in e for e in errors["P01"]), errors)

    def test_nameplates_follow_first_solo_appearance(self):
        sources = helpers.standard_sources()
        for shot in sources[1]["shots"]:
            shot["people"] = ["person:lin"]
        outputs, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertEqual(errors, {})
        plates = outputs[0]["nameplates"]
        self.assertEqual([p["person_id"] for p in plates], ["person:wu-yue", "person:lin"])
        self.assertEqual(plates[0]["start_frame"], 0)
        self.assertEqual(plates[1]["start_frame"], outputs[0]["clips"][2]["out_in"])
        self.assertLessEqual(plates[0]["end_frame_exclusive"], plates[1]["start_frame"] - PLAN.NAMEPLATE_GAP)

    def test_shared_first_appearance_rejected(self):
        sources = helpers.standard_sources()
        sources[0]["shots"][0]["people"] = ["person:wu-yue", "person:lin"]
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("first appear together" in e for e in errors["P01"]), errors)

    def test_supporting_only_shot_rejected(self):
        sources = helpers.standard_sources()
        sources[2]["shots"][1]["people"] = ["person:crew"]
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("no registered celebrity" in e for e in errors["P01"]), errors)

    def test_request_count_and_ids(self):
        _, errors, _ = self.resolve([self.output(GOOD), self.output(GOOD, "p01")])
        self.assertTrue(any("unique" in e for e in errors["plan"]), errors)

    def test_spread_violation_is_explained(self):
        sources = helpers.standard_sources()
        long_unit = helpers.unit("U05", "这是一个非常非常长的句子用来测试镜头时长的均衡规则对不对呀。", 17.0, 26.0)
        sources[2]["units"].append(long_unit)
        sources[2]["beats"].append(helpers.beat("B05", ["U05"], "payoff"))
        bad = clips(("S01", "U01"), ("S01", "U02"), ("S02", "U01-U02"), ("S03", "U01"), ("S03", "U05"))
        _, errors, _ = self.resolve([self.output(bad)], sources)
        self.assertTrue(any("frame band" in e for e in errors["P01"]), errors)

    def test_clip_must_be_whole_beats(self):
        sources = helpers.standard_sources()
        sources[0]["beats"] = [helpers.beat("B01", ["U01", "U02"], "hook"), helpers.beat("B02", ["U03", "U04"])]
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("cuts through beat B01" in e for e in errors["P01"]), errors)
        whole = clips(("S01", "U01-U02"), ("S02", "U01-U02"), ("S03", "U01"), ("S03", "U02"))
        _, errors, _ = self.resolve([self.output(whole)], sources)
        self.assertNotIn("P01", errors)

    def test_units_outside_beats_cannot_be_planned(self):
        sources = helpers.standard_sources()
        sources[1]["beats"] = sources[1]["beats"][1:]
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("S02 U01 belong to no beat" in e for e in errors["P01"]), errors)
        sources[1]["beats"] = []
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("S02 has no beats" in e for e in errors["P01"]), errors)

    def test_scene_beats_cannot_be_planned(self):
        sources = helpers.standard_sources()
        outputs, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertEqual(errors, {})
        self.assertRegex(PLAN.describe(outputs[0]), r"talk 100%")
        sources[2]["beats"][0]["kind"] = "scene"
        _, errors, _ = self.resolve([self.output(GOOD)], sources)
        self.assertTrue(any("acted scene" in e for e in errors["P01"]), errors)

    def test_role_order_and_ending(self):
        wrong = clips(("S01", "U01"), ("S03", "U04"), ("S02", "U01-U02"), ("S03", "U01"))
        _, errors, _ = self.resolve([self.output(wrong)])
        self.assertTrue(any("body beat after a cta beat" in e for e in errors["P01"]), errors)
        flat = clips(("S01", "U02"), ("S02", "U01-U02"), ("S03", "U01"))
        _, errors, _ = self.resolve([self.output(flat)])
        self.assertTrue(any("must end on a payoff or cta" in e for e in errors["P01"]), errors)

    def test_hook_only_opens_and_clip_limit(self):
        many = clips(("S01", "U02"), ("S01", "U03"), ("S02", "U01"), ("S02", "U02"), ("S03", "U01"), ("S03", "U02"))
        _, errors, _ = self.resolve([self.output(many)])
        self.assertTrue(any("at most 5" in e for e in errors["P01"]), errors)
        late = clips(("S02", "U01"), ("S01", "U01"), ("S02", "U02"), ("S03", "U01"), ("S03", "U02"))
        _, errors, _ = self.resolve([self.output(late)])
        self.assertTrue(any("hook" in e for e in errors["P01"]), errors)

    def test_script_lists_words_beats_and_links(self):
        outputs, errors, _ = self.resolve([self.output(GOOD)])
        self.assertEqual(errors, {})
        lines = PLAN.script(outputs[0])
        self.assertTrue(any(line.startswith("## P01") for line in lines))
        self.assertTrue(any("B01 talk/hook" in line for line in lines))
        self.assertTrue(any(line.startswith("   -> progression:") for line in lines))
        self.assertTrue(any("玩无尽冬日最重要的是什么" in line for line in lines))

    def test_solver_spread_on_real_numbers(self):
        options = [[{"in": 0, "out": 100, "total": t, "pieces": [t], "edges": [0, 100], "cost": c} for t, c in ((150, 0), (230, 1))]
                   for _ in range(5)]
        choice = PLAN.solve(options)
        self.assertIsNotNone(choice)
        self.assertGreaterEqual(sum(o["total"] for o in choice), PLAN.MIN_FRAMES)


if __name__ == "__main__":
    unittest.main()
