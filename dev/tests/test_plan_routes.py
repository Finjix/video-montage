"""Plan resolver robustness and the route finder on a synthetic catalog (no media tools needed)."""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from montage import batch, job as JOB, plan as PLAN, routes as ROUTES

FPS = 60
WU, XU = "person:wu-yue", "person:xu-jun-cong"
GAMES = ["无尽冬日"]


def quote(text: str) -> str:
    return re.findall(r"[\u3400-\u9fff]{2,}", text)[0]


def source(sid: str, person: str, lines: list[tuple[str, str]], *, seconds: float = 4.5) -> dict:
    """One sentence per unit and beat (role, text); every unit has a camera cut in its middle (2 shots per unit)."""
    units, beats, cuts, cursor = [], [], [], 0.5
    for number, (role, text) in enumerate(lines, 1):
        start, end = cursor, cursor + seconds
        uid = f"U{number:02d}"
        units.append({"id": uid, "text": text, "words": [{"word": text, "start": start, "end": end}],
                      "start": start, "end": end, "sentence_end": True, "protect": [start - 0.02, end + 0.03],
                      "in_frames": [[round(start * FPS) - 24, round(start * FPS) - 6]],
                      "out_frames": [[round(end * FPS) + 6, round(end * FPS) + 24]],
                      "speaker": person, "excluded": False, "rejected": [], "beat": f"B{number:02d}"})
        beats.append({"id": f"B{number:02d}", "units": [uid], "kind": "talk", "role": role, "gist": f"synthetic beat {number} for tests"})
        cuts.append(round((start + end) / 2 * FPS))
        cursor = end + 0.8
    frames = round(cursor * FPS) + 60
    edges = [0, *cuts, frames]
    shots = [{"number": i, "numbers": [i], "start": a, "end": b, "people": [person], "family": f"{sid}:shot{i}"}
             for i, (a, b) in enumerate(zip(edges, edges[1:]), 1)]
    return {"id": sid, "path": f"D:/footage/{sid}.mp4", "name": f"{sid}.mp4", "sha256": sid * 8, "fps_num": FPS, "fps_den": 1,
            "frames": frames, "duration": frames / FPS, "width": 1080, "height": 1920, "sheet": "", "cuts": cuts,
            "shots": shots, "units": units, "beats": beats, "annotated": True}


def catalog(wu_sources: int = 4) -> dict:
    sources = []
    for index in range(6):
        person = WU if index < wu_sources else XU
        sources.append(source(f"S{index + 1:02d}", person, [
            ("hook", f"你知道无尽冬日第{index}招吗？"), ("body", f"开局先砍树第{index}步，"),
            ("body", f"然后升级第{index}个建筑。"), ("cta", f"点击即玩第{index}号。")]))
    return {"sources": sources, "annotations_digest": "test"}


def state(count: int) -> dict:
    return {"order": {"requested_outputs": count}, "celebrities": [{"person_id": WU}, {"person_id": XU}], "decisions": {}}


class Job:
    def __init__(self, count):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-routes-")
        self.root = Path(self.temp.name)
        for folder in ("catalog", "config", "reports"):
            (self.root / folder).mkdir()
        (self.root / "catalog/catalog.json").write_text(json.dumps(catalog(), ensure_ascii=False), encoding="utf-8")
        library = {"entries": [{"id": "game:wjdr", "category": "game", "text": "无尽冬日"}]}
        (self.root / "config/text_library.json").write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")
        self.state = state(count)


FOUR_CLIPS = [{"source": "S01", "units": "U01"}, {"source": "S02", "units": "U02"}, {"source": "S03", "units": "U03"},
              {"source": "S04", "units": "U04"}]
FOUR_LINKS = [{"relation": "answer", "reason": "the opening step answers the hook question"},
              {"relation": "progression", "reason": "continues the first building step"},
              {"relation": "benefit", "reason": "invites the viewer to play after the steps"}]


class PlanRobustness(unittest.TestCase):
    def test_four_alternating_clips_pass(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        output = {"plan_id": "P01", "clips": FOUR_CLIPS, "transitions": FOUR_LINKS}
        _, errors = PLAN.resolve_output(output, sources, {WU}, GAMES, 3)
        self.assertEqual(errors, [])

    def test_adjacent_clips_from_one_source_are_refused(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        clips = [{"source": "S01", "units": "U01"}, {"source": "S02", "units": "U02"}, {"source": "S02", "units": "U03"},
                 {"source": "S03", "units": "U04"}]
        _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": clips, "transitions": FOUR_LINKS}, sources, {WU}, GAMES, 3)
        self.assertTrue(any("clips 2 and 3 both come from S02" in e for e in errors), errors)

    def test_a_source_may_return_when_not_adjacent(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        clips = [{"source": "S01", "units": "U01"}, {"source": "S02", "units": "U02"}, {"source": "S01", "units": "U03"},
                 {"source": "S03", "units": "U04"}]
        _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": clips, "transitions": FOUR_LINKS}, sources, {WU}, GAMES, 3)
        self.assertEqual(errors, [])

    def test_a_returning_source_may_not_repeat_a_beat(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        clips = [{"source": "S01", "units": "U01-U02"}, {"source": "S02", "units": "U02"}, {"source": "S01", "units": "U02"},
                 {"source": "S03", "units": "U04"}]
        _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": clips, "transitions": FOUR_LINKS}, sources, {WU}, GAMES, 3)
        self.assertTrue(any("clip 3: S01 B02 is already spoken in clip 1" in e for e in errors), errors)

    def test_camera_cuts_inside_a_clip_do_not_count_as_shots(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        clips = [{"source": "S01", "units": "U01-U02"}, {"source": "S02", "units": "U03"}, {"source": "S03", "units": "U04"}]
        resolved, errors = PLAN.resolve_output({"plan_id": "P01", "clips": clips, "transitions": FOUR_LINKS[1:]},
                                               sources, {WU}, GAMES, 3)
        self.assertGreaterEqual(len(resolved["shots"]), 4)
        self.assertTrue(any("only 3 clips" in e for e in errors), errors)

    def test_script_survives_missing_transitions(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        output = {"plan_id": "P01", "clips": [{"source": "S01", "units": "U01-U02"}, {"source": "S02", "units": "U03"},
                                             {"source": "S03", "units": "U04"}], "transitions": []}
        resolved, errors = PLAN.resolve_output(output, sources, {WU}, GAMES, 3)
        self.assertTrue(any("transitions" in e for e in errors))
        self.assertTrue(PLAN.script(resolved))

    def test_non_object_output_is_a_problem_not_a_crash(self):
        with self.assertRaises(PLAN.PlanError):
            PLAN.resolve_output("P01", {}, {WU}, GAMES, 3)

    def test_an_opening_that_leans_on_an_earlier_line_is_refused(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        for text in ("再去砍树就行了。", "为什么呢？因为温度最重要。", "就是六个字：要想富先砍树。",
                     "有啊，试试无尽冬日。", "错错错，在无尽冬日上线点一点。"):
            sources["S01"]["units"][0]["text"] = text
            _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": FOUR_CLIPS, "transitions": FOUR_LINKS}, sources, {WU}, GAMES, 3)
            self.assertTrue(any("leans on an earlier line" in e for e in errors), (text, errors))

    def test_a_clip_that_restates_the_previous_one_is_refused(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        sources["S01"]["units"][1]["text"] = "开局一定要先砍树。"
        sources["S02"]["units"][1]["text"] = "开局一定要砍树、"
        clips = [{"source": "S01", "units": "U01-U02"}, {"source": "S02", "units": "U02-U03"}, {"source": "S03", "units": "U03"},
                 {"source": "S04", "units": "U04"}]
        _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": clips, "transitions": FOUR_LINKS}, sources, {WU}, GAMES, 3)
        self.assertTrue(any("restating the end of clip 1" in e for e in errors), errors)

    def test_too_few_sources_never_tells_the_agent_to_ask(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        output = {"plan_id": "P01", "clips": [{"source": "S01", "units": "U01-U02"}, {"source": "S02", "units": "U03-U04"}],
                  "transitions": [{"relation": "progression", "reason": "clip two continues the first building step"}]}
        _, errors = PLAN.resolve_output(output, sources, {WU}, GAMES, 3)
        problem = next(e for e in errors if "source files" in e)
        self.assertNotIn("ask", problem)


class Routes(unittest.TestCase):
    def test_clips_and_unusable_reasons(self):
        data = catalog()
        data["sources"][0]["units"][1]["in_frames"] = []
        found, unusable = ROUTES.clips(data, {WU, XU})
        keys = {clip["key"] for clip in found}
        self.assertIn("S02:U01-U03", keys)
        self.assertNotIn("S01:U02", keys)
        self.assertTrue(any("S01" in row and "cannot start" in row for row in unusable))
        self.assertFalse(any(clip["key"].startswith("S01:U03") and "hook" in clip["roles"] for clip in found))

    def test_connector_limits_relations(self):
        left = {"text": "开局先砍树。", "last_speaker": WU, "first_speaker": WU}
        right = {"text": "然后升级建筑。", "last_speaker": WU, "first_speaker": WU}
        self.assertEqual(set(ROUTES.relations(left, right)), {"progression", "escalation"})

    def test_explore_then_joins_build_a_batch_that_passes_check(self):
        job = Job(6)
        try:
            lines = ROUTES.run(job.root, job.state, joins=None, mix=False, max_clips=4, seconds=10)
            self.assertIn("routes:", lines[0])
            report = json.loads((job.root / "reports/routes.json").read_text(encoding="utf-8"))
            clips = {clip["key"]: clip for clip in report["clips"]}
            self.assertTrue(report["joins"])
            # Xu has two sources, too few for a video of its own: its clips hand over to or from Wu
            crossing = [j for j in report["joins"] if clips[j["from"]]["cast"] != clips[j["to"]]["cast"]]
            self.assertTrue(crossing)
            self.assertIn("Own clips finish no video for xu-jun-cong", (job.root / "reports/routes.md").read_text(encoding="utf-8"))
            accepted = [{"from": j["from"], "to": j["to"], "relation": ROUTES.relations(clips[j["from"]], clips[j["to"]])[0],
                         "reason": f"{j['from']} ends on {quote(clips[j['from']]['last_text'])}, {j['to']} follows with "
                                   f"{quote(clips[j['to']]['first_text'])}"} for j in report["joins"]]
            path = job.root / "config/joins.json"
            path.write_text(json.dumps({"joins": accepted}, ensure_ascii=False), encoding="utf-8")
            lines = ROUTES.run(job.root, job.state, joins=path, mix=False, max_clips=4, seconds=10)
            draft = json.loads((job.root / "config/plan-draft.json").read_text(encoding="utf-8"))
            self.assertEqual(len(draft["outputs"]), 6, lines)
            outputs, errors, _ = PLAN.resolve(draft, job.root, job.state)
            self.assertEqual(errors, {})
            self.assertEqual(batch.audit(outputs, {})[0], [])
            self.assertTrue(any(XU in output["protagonists"] for output in outputs))
            self.assertTrue(any(line.startswith("cast: ") for line in lines))
            # a re-run keeps every video of the current plan under its plan ID
            (job.root / "config/plan.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            lines = ROUTES.run(job.root, job.state, joins=path, mix=False, max_clips=4, seconds=10)
            again = json.loads((job.root / "config/plan-draft.json").read_text(encoding="utf-8"))
            self.assertEqual({o["plan_id"]: ROUTES.signature(o) for o in again["outputs"]},
                             {o["plan_id"]: ROUTES.signature(o) for o in draft["outputs"]})
            self.assertIn("kept 6 videos of the previous plan under their plan IDs (render and package reuse them)", lines)
        finally:
            job.temp.cleanup()

    def test_explore_keeps_one_celebrity_when_each_finishes_alone(self):
        found, _ = ROUTES.clips(catalog(wu_sources=3), {WU, XU})
        data = catalog(wu_sources=3)
        sources = {s["id"]: s for s in data["sources"]}
        links = lambda a, b: (ROUTES.relations(a, b) or [None])[0]
        verify = ROUTES.verifier(sources, {WU, XU}, GAMES, 3)
        stranded = ROUTES.stranded_casts(found, links, min_sources=3, max_clips=4, verify=verify)
        self.assertEqual(stranded, frozenset())
        verified, _ = ROUTES.search(found, links, min_sources=3, mix=False, max_clips=4, seconds=10, verify=verify,
                                    stranded=stranded)
        self.assertTrue(verified)
        self.assertTrue(all(len({clip["cast"] for clip in row["route"]}) == 1 for row in verified))

    def test_min_cast_keeps_only_routes_featuring_that_many_celebrities(self):
        data = catalog(wu_sources=3)
        found, _ = ROUTES.clips(data, {WU, XU})
        sources = {s["id"]: s for s in data["sources"]}
        links = lambda a, b: (ROUTES.relations(a, b) or [None])[0]
        verified, _ = ROUTES.search(found, links, min_sources=3, mix=False, max_clips=4, seconds=10, min_cast=2,
                                    verify=ROUTES.verifier(sources, {WU, XU}, GAMES, 3, min_cast=2))
        self.assertTrue(verified)
        self.assertTrue(all(set(row["resolved"]["protagonists"]) == {WU, XU} for row in verified))
        _, errors = PLAN.resolve_output({"plan_id": "P01", "clips": FOUR_CLIPS, "transitions": FOUR_LINKS},
                                        sources, {WU, XU}, GAMES, 3, min_cast=3)
        self.assertTrue(any("features 2 celebrities" in error for error in errors))

    def test_min_cast_above_max_clips_is_refused(self):
        with self.assertRaisesRegex(ValueError, "cannot feature 5 celebrities"):
            ROUTES.run(Path("no-job"), dict(state(1), decisions={"min_celebrities_per_output": {"value": 5}}),
                       joins=None, mix=False, max_clips=4, seconds=1)

    def test_three_sources_still_give_four_shot_routes(self):
        data = catalog()
        data["sources"] = data["sources"][:3]
        found, _ = ROUTES.clips(data, {WU, XU})
        sources = {s["id"]: s for s in data["sources"]}
        verified, _ = ROUTES.search(found, lambda a, b: (ROUTES.relations(a, b) or [None])[0], min_sources=3, mix=False,
                                    max_clips=4, seconds=10, verify=ROUTES.verifier(sources, {WU}, GAMES, 3))
        self.assertTrue(verified)
        for row in verified:
            route = [clip["sha256"] for clip in row["route"]]
            self.assertTrue(all(a != b for a, b in zip(route, route[1:])))
            beats = [(clip["sha256"], beat) for clip in row["route"] for beat in clip["beats"]]
            self.assertEqual(len(beats), len(set(beats)))

    def test_fewer_max_clips_than_a_video_needs_is_refused(self):
        with self.assertRaisesRegex(ValueError, "needs >= 4 clips"):
            ROUTES.run(Path("no-job"), state(1), joins=None, mix=False, max_clips=3, seconds=1)

    def test_joins_file_rejects_bad_relations_and_reasons(self):
        found, _ = ROUTES.clips(catalog(), {WU, XU})
        by_key = {clip["key"]: clip for clip in found}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "joins.json"
            path.write_text(json.dumps({"joins": [
                {"from": "S01:U01-U02", "to": "S02:U03-U04", "relation": "contrast", "reason": "synthetic contrast join"},
                {"from": "S01:U01-U02", "to": "S02:U04", "relation": "benefit", "reason": "ok"}]}), encoding="utf-8")
            accepted, problems = ROUTES.joins_file(path, by_key)
        self.assertEqual(accepted, {})
        self.assertEqual(len(problems), 2)

    def test_join_reasons_quote_both_clips_once_each(self):
        found, _ = ROUTES.clips(catalog(), {WU, XU})
        by_key = {clip["key"]: clip for clip in found}
        joins = [{"from": "S01:U01-U02", "to": "S02:U03", "relation": "progression", "reason": "the first step leads to building upgrades"},
                 {"from": "S01:U01-U02", "to": "S02:U03", "relation": "progression", "reason": "开局先砍树 leads to the upgrade"},
                 {"from": "S03:U01-U02", "to": "S04:U03", "relation": "progression", "reason": "开局先砍树 leads to 然后升级"},
                 {"from": "S05:U01-U02", "to": "S06:U03", "relation": "progression", "reason": "开局先砍树 leads to 然后升级"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "joins.json"
            path.write_text(json.dumps({"joins": joins}, ensure_ascii=False), encoding="utf-8")
            accepted, problems = ROUTES.joins_file(path, by_key)
        self.assertIn("nothing quoted from the first and second clip", problems[0])
        self.assertIn("nothing quoted from the second clip", problems[1])
        self.assertIn("joins 3, 4 share one reason", problems[2])
        self.assertEqual(len(problems), 3)

    def test_usage_matches_batch_audit(self):
        sources = {s["id"]: s for s in catalog()["sources"]}
        resolved = []
        for number in range(3):
            output = {"plan_id": f"P{number}", "clips": FOUR_CLIPS, "transitions": FOUR_LINKS}
            row, errors = PLAN.resolve_output(output, sources, {WU}, GAMES, 3)
            self.assertEqual(errors, [])
            resolved.append(row)
        usage = ROUTES.Usage()
        usage.add(ROUTES.Usage.keys(resolved[0]))
        self.assertFalse(usage.fits(ROUTES.Usage.keys(resolved[1])))
        self.assertTrue(batch.audit(resolved[:2], {})[0])


    def test_usage_counts_one_text_from_two_sources_twice(self):
        def profile(n):
            return {"keys": {"text": ["点击即玩", "点击即玩"]}, "texts": [f"t{n}"], "speech": f"q{n:02d}w{n:02d}e{n:02d}"}
        usage = ROUTES.Usage()
        for n in range(batch.LIMITS["text"] // 2):
            self.assertTrue(usage.fits(profile(n)))
            usage.add(profile(n))
        self.assertFalse(usage.fits(profile(99)))

    def test_malformed_join_rows_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "joins.json"
            path.write_text(json.dumps({"joins": [{"from": ["S01"], "to": "S02:U01", "relation": "benefit", "reason": 12345678901234}]}),
                            encoding="utf-8")
            accepted, problems = ROUTES.joins_file(path, {})
        self.assertEqual((accepted, len(problems)), ({}, 1))


class InitChecks(unittest.TestCase):
    def test_png_header_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plate.png"
            path.write_bytes(JOB.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + (1080).to_bytes(4, "big") + (1920).to_bytes(4, "big") + b"\x08\x06")
            self.assertEqual(JOB.png_size(path), (1080, 1920))
            self.assertIsNone(JOB.overlay_problem(path))
            path.write_bytes(b"not a png at all, just some bytes")
            self.assertEqual(JOB.overlay_problem(path), "not a PNG")


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("PIL"), "needs numpy and Pillow")
class CameraCuts(unittest.TestCase):
    def series(self, spikes: dict[int, float], base: float = 0.003, frames: int = 200):
        import numpy as np
        values = np.full(frames, base, np.float32)
        values[0] = 0.0
        for frame, value in spikes.items():
            values[frame] = value
        return values

    def test_soft_isolated_spike_is_a_cut(self):
        from montage import catalog
        # a zoom-in on the same set: 0.056 against ~0.003 neighbours (S01 frame 922 of the 2026-10-26 batch)
        self.assertEqual(catalog.camera_cuts(self.series({100: 0.056, 101: 0.0})), [100])

    def test_motion_and_flicker_are_not_cuts(self):
        from montage import catalog
        burst = {frame: 0.066 + 0.001 * (frame % 3) for frame in range(100, 106)}   # steady fast motion
        self.assertEqual(catalog.camera_cuts(self.series(burst)), [])
        flicker = {frame: 0.035 for frame in range(80, 140, 2)}                     # duplicated frames: motion every other frame
        self.assertEqual(catalog.camera_cuts(self.series(flicker, base=0.0)), [])
        self.assertEqual(catalog.camera_cuts(self.series({100: 0.025})), [])          # too faint to tell from noise
        self.assertEqual(catalog.camera_cuts(self.series({100: 0.2})), [100])

    def test_shot_notes_follow_their_frames_when_cuts_change(self):
        from montage import catalog
        before, after = [[0, 100], [100, 300], [300, 500]], [[0, 100], [100, 200], [200, 300], [300, 450], [450, 500]]
        note = {"people": [WU], "shots": {"2": {"people": [XU]}, "3": {"merge": True, "people": [WU, XU]}}, "cameras": [[1, 3]]}
        moved = catalog.renumber_shots(note, before, after)
        self.assertEqual(moved["shots"], {"2": {"people": [XU]}, "3": {"people": [XU]}, "4": {"merge": True, "people": [WU, XU]},
                                          "5": {"people": [WU, XU]}})
        self.assertEqual(moved["cameras"], [[1, 4, 5]])
        self.assertEqual(moved["people"], [WU])

    def test_prepare_rerun_renumbers_only_sources_whose_cuts_changed(self):
        from montage import catalog
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "config").mkdir()
            notes = {"sources": {"S01": {"shots": {"2": {"people": [XU]}}}, "S02": {"shots": {"2": {"people": [XU]}}}}}
            (job / "config/annotations.json").write_text(json.dumps(notes), encoding="utf-8")
            old = [{"id": sid, "sha256": sid * 8, "frames": 500, "cuts": [100]} for sid in ("S01", "S02")]
            new = [dict(old[0], cuts=[50, 100]), dict(old[1], sha256="other", cuts=[50, 100])]
            catalog.keep_shot_notes(job, old, new)
            result = json.loads((job / "config/annotations.json").read_text(encoding="utf-8"))
            self.assertEqual(result["sources"]["S01"]["shots"], {"3": {"people": [XU]}})
            self.assertEqual(result["sources"]["S02"], notes["sources"]["S02"])   # another file: numbers left as they are


@unittest.skipUnless(importlib.util.find_spec("numpy"), "needs numpy")
class Alignment(unittest.TestCase):
    def test_unmatched_token_does_not_fail_recognition(self):
        from montage import asr
        items = [SimpleNamespace(text="无尽", start_time=0.0, end_time=0.3), SimpleNamespace(text="冬至", start_time=0.3, end_time=0.6),
                 SimpleNamespace(text="好玩", start_time=0.6, end_time=0.9)]
        words = asr.attach_punctuation("无尽冬日，好玩。", items)
        self.assertEqual("".join(w["word"] for w in words).count("好玩"), 1)
        self.assertTrue(words[-1]["word"].endswith("。"))

    def test_tokens_in_another_form_keep_the_text_once(self):
        from montage import asr
        for text, tokens in (("OK吧。", ["ok", "吧"]), ("我２个", ["我", "2", "个"])):
            items = [SimpleNamespace(text=t, start_time=i * .3, end_time=i * .3 + .3) for i, t in enumerate(tokens)]
            self.assertEqual("".join(w["word"] for w in asr.attach_punctuation(text, items)), text)

    def test_batched_alignment_falls_back_to_single_requests(self):
        from montage import asr
        import numpy as np
        calls = []

        class Aligner:
            def align(self, audio, text, language):
                calls.append(len(audio))
                if len(audio) > 1:
                    raise RuntimeError("CUDA out of memory")
                return [SimpleNamespace(items=[SimpleNamespace(text=text[0], start_time=0.1, end_time=0.5)])]
        requests = [(np.zeros(16000, np.float32), "砍树", 2.0), (np.zeros(16000, np.float32), "建造", 5.0)]
        words = asr.align_many(Aligner(), requests)
        self.assertEqual([w[0]["start"] for w in words], [2.1, 5.1])
        self.assertEqual(calls, [2, 1, 1])

    def test_memory_errors_on_the_cpu_are_raised_not_retried(self):
        from montage import asr
        import numpy as np
        calls = []

        class Aligner:
            def align(self, audio, text, language):
                calls.append(len(audio))
                raise RuntimeError("out of memory")
        original = asr.build_aligner
        asr.build_aligner = lambda target=None: Aligner()
        try:
            with self.assertRaises(RuntimeError):
                asr.align_many(Aligner(), [(np.zeros(16000, np.float32), "砍树", 0.0)] * 2)
        finally:
            asr.build_aligner = original
        self.assertEqual(calls, [2, 1, 1])


@unittest.skipUnless(importlib.util.find_spec("PIL"), "needs Pillow")
class SubtitleDrafts(unittest.TestCase):
    def test_a_run_without_a_word_safe_split_is_still_drafted(self):
        from montage.packaging import segmentation
        text = "哈" * 14 + "无尽冬日"
        shot = {"asr": {"segments": [{"text": text, "words": [{"word": text, "start": 0.0, "end": 4.0}]}]}}
        rows = segmentation.split_shot(shot, names=tuple(GAMES), protected_terms=tuple(GAMES))
        self.assertEqual("".join(row["text"] for row in rows), text)
        self.assertTrue(all(segmentation.character_count(row["text"]) <= 10 for row in rows))
        self.assertTrue(any("无尽冬日" in row["text"] for row in rows))

    def test_drafts_keep_numbers_and_idioms_whole(self):
        from montage.packaging import segmentation

        def cues(text, pause_before=None):
            words, start = [], 0.0
            for index, char in enumerate(text):
                start += 0.3 if index == pause_before else 0.0
                words.append({"word": char, "start": start, "end": start + 0.2})
                start += 0.2
            shot = {"asr": {"segments": [{"text": text, "words": words}]}}
            return [row["text"] for row in segmentation.split_shot(shot, names=tuple(GAMES), protected_terms=tuple(GAMES))]
        self.assertEqual(cues("那战力可不得蹭蹭往上涨啊"), ["那战力可不得", "蹭蹭往上涨啊"])
        # an aligner pause before 度 used to give 一开局就是零下七十 / 度的冰原
        self.assertTrue(any("零下七十度" in row for row in cues("一开局就是零下七十度的冰原", pause_before=10)))


if __name__ == "__main__":
    unittest.main()
