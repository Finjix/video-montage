"""Plan resolver robustness and the route finder on a synthetic catalog (no media tools needed)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from montage import batch, job as JOB, plan as PLAN, routes as ROUTES

FPS = 60
WU, XU = "person:wu-yue", "person:xu-jun-cong"
GAMES = ["无尽冬日"]


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


def catalog() -> dict:
    sources = []
    for index in range(6):
        person = WU if index < 4 else XU
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
            self.assertFalse(any(clips[j["from"]]["cast"] != clips[j["to"]]["cast"] for j in report["joins"]))
            accepted = [{"from": j["from"], "to": j["to"], "relation": ROUTES.relations(clips[j["from"]], clips[j["to"]])[0],
                         "reason": "synthetic link between the two clips for this test"} for j in report["joins"]]
            path = job.root / "config/joins.json"
            path.write_text(json.dumps({"joins": accepted}, ensure_ascii=False), encoding="utf-8")
            lines = ROUTES.run(job.root, job.state, joins=path, mix=False, max_clips=4, seconds=10)
            draft = json.loads((job.root / "config/plan-draft.json").read_text(encoding="utf-8"))
            self.assertEqual(len(draft["outputs"]), 6, lines)
            outputs, errors, _ = PLAN.resolve(draft, job.root, job.state)
            self.assertEqual(errors, {})
            self.assertEqual(batch.audit(outputs, {})[0], [])
        finally:
            job.temp.cleanup()

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


if __name__ == "__main__":
    unittest.main()
