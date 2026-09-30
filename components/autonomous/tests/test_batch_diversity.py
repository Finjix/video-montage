from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


diversity = load("diversity_test", "batch_diversity.py")
auto = load("auto_diversity_test", "autonomous_montage.py")


def segment(source, start=0, text=None):
    return {"candidate_id": source, "source_sha256": source, "source_path": source + ".mp4",
            "source_in_frame": start, "speech_end_frame": start + 110,
            "source_out_frame_exclusive": start + 120, "source_fps_num": 60, "source_fps_den": 1,
            "text": text or f"完整口播内容{source}", "audio_gain_db": -3}


def option(name, hook, body):
    return {"plan_id": name, "segments": [segment(hook), segment(body), segment("closing")]}


class DiversityTests(unittest.TestCase):
    def test_three_outputs_use_available_distinct_hooks_not_first_three_options(self):
        pool = [option("a1", "a", "body1"), option("a2", "a", "body2"),
                option("a3", "a", "body3"), option("b1", "b", "body4"), option("c1", "c", "body5")]
        before = copy.deepcopy(pool)
        outputs, report = diversity.optimize(pool, 3)
        self.assertEqual({"a", "b", "c"}, {o["segments"][0]["source_sha256"] for o in outputs})
        self.assertEqual(0, report["opening_repeat_pairs"])
        self.assertEqual(before, pool)
        self.assertEqual(3, len({o["plan_id"] for o in outputs}))
        self.assertEqual(-3, outputs[0]["segments"][0]["audio_gain_db"])

    def test_fifty_outputs_balance_limited_hooks_and_reuse_all_coherent_variants(self):
        pool = [option(f"{hook}-{body}", hook, f"body-{hook}-{body}")
                for hook in ("a", "b", "c") for body in (1, 2)]
        outputs, report = diversity.optimize(pool, 50)
        counts = Counter(o["segments"][0]["source_sha256"] for o in outputs)
        self.assertEqual(50, len(outputs))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        usage = [r["count"] for r in report["option_usage"]]
        self.assertTrue(all(usage))
        self.assertLessEqual(max(usage) - min(usage), 1)
        self.assertGreater(report["opening_repeat_pairs"], 0)
        self.assertFalse(report["global_optimum_claimed"])
        self.assertFalse(report["quality_approval"])
        self.assertEqual(outputs, diversity.optimize(pool, 50)[0])

    def test_id_changes_frame_jitter_and_extended_take_do_not_disguise_same_hook(self):
        original = option("first", "a", "body")
        changed = copy.deepcopy(original)
        changed["plan_id"] = "renamed"
        for s in changed["segments"]:
            s["candidate_id"] += "-renamed"
            for key in ("source_in_frame", "speech_end_frame", "source_out_frame_exclusive"):
                s[key] += 1
            s["text"] += "。"
        report = diversity.evaluate([original, changed])
        self.assertEqual(1, report["opening_repeat_pairs"])
        self.assertEqual(1, report["duplicate_sequence_pairs"])
        extended = copy.deepcopy(original)
        extended["segments"][0]["source_out_frame_exclusive"] += 600
        extended["segments"][0]["text"] += "接着继续说另一段内容"
        self.assertTrue(diversity.compare(diversity.features(original), diversity.features(extended))["same_opening"])
        self.assertFalse(diversity.compare(diversity.features(original), diversity.features(extended))["duplicate_sequence"])

    def test_copied_speech_across_files_is_repeated_and_body_reordering_is_distinct(self):
        a, b = option("a", "source1", "body1"), option("b", "source2", "body2")
        a["segments"][0]["text"] = b["segments"][0]["text"] = "今天带你看看我的冰雪生存体验"
        self.assertEqual(1, diversity.evaluate([a, b])["opening_repeat_pairs"])
        reordered = copy.deepcopy(a)
        reordered["plan_id"] = "reordered"
        reordered["segments"][1:] = list(reversed(reordered["segments"][1:]))
        self.assertEqual(0, diversity.evaluate([a, reordered])["duplicate_sequence_pairs"])

    def test_secondary_objective_reduces_shared_body_when_hooks_are_distinct(self):
        pool = [option("a", "a", "shared"), option("b-shared", "b", "shared"),
                option("b-fresh", "b", "fresh")]
        outputs, _ = diversity.optimize(pool, 2)
        self.assertEqual({"shared", "fresh"}, {o["segments"][1]["source_sha256"] for o in outputs})

    def test_one_usable_option_can_fill_requested_scope_without_diversity_rejection(self):
        outputs, report = diversity.optimize([option("only", "a", "body")], 50)
        self.assertEqual(50, len(outputs))
        self.assertEqual(1225, report["duplicate_sequence_pairs"])
        self.assertEqual("best_effort", report["policy"])

    def test_selected_plan_still_binds_source_scope_and_diversity_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"original")
            s = segment(auto.sha(source))
            s["source_path"] = str(source)
            index = root / "index.json"
            auto.write(index, {"sources": [{"source": auto.ref(source),
                       "video": {"fps_num": 60, "fps_den": 1, "frames": 500}}]})
            order = root / "order.json"
            auto.write(order, {"schema": auto.ORDER_SCHEMA, "requested_outputs": 3})
            options = root / "options.json"
            auto.write(options, {"schema": auto.PLAN_SCHEMA, "outputs": [{"plan_id": "only", "segments": [s]}]})
            auto.write(root / "autonomous_state.json", {"schema": auto.STATE_SCHEMA,
                       "review_mode": "codex_asr_pcm", "phase": "prepared", "repair_round": 0,
                       "work_order": auto.ref(order), "source_index": auto.ref(index)})
            plan_path = root / "selected.json"
            args = SimpleNamespace(job_dir=root, options=options, plan=plan_path)
            auto.diversify_plan(args)
            plan = auto.read(plan_path)
            self.assertEqual([], auto.check_plan(plan, auto.read(index), 3))
            state = auto.state(root)
            report_ref = auto.record_diversity(root, state, plan_path, plan)
            state["plan"] = auto.ref(plan_path)
            evidence = {"batch_diversity": report_ref}
            self.assertEqual(3, auto.require_diversity(state, evidence)["output_count"])
            # Editing a plan invalidates the report even if the edits only rename IDs.
            plan["outputs"][0]["plan_id"] = "other"
            auto.write(plan_path, plan)
            with self.assertRaisesRegex(ValueError, "changed|mismatch"):
                auto.require_diversity(state, evidence)
            auto.record_diversity(root, state, plan_path, plan)
            self.assertNotIn("diversity_selection", state)
            bad = auto.read(options)
            bad["outputs"][0]["segments"][0]["source_sha256"] = "foreign-source"
            auto.write(options, bad)
            with self.assertRaisesRegex(ValueError, "source not in work order"):
                auto.diversify_plan(args)


if __name__ == "__main__":
    unittest.main()
