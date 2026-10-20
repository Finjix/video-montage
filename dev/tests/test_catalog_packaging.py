"""Catalog analysis helpers, render helpers and packaging defaults (no media needed)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from montage import catalog as CATALOG
from montage import package as PACKAGE
from montage import render as RENDER
from montage.packaging import design


def words(text, start, step=0.2):
    return [{"word": ch, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step - 0.02, 3)} for i, ch in enumerate(text)]


class CatalogTests(unittest.TestCase):
    def test_units_split_at_punctuation_and_pauses(self):
        units = CATALOG.split_units(words("你好，", 0.0) + words("世界。", 0.7) + words("再见", 2.0))
        self.assertEqual(["".join(w["word"] for w in u["words"]) for u in units], ["你好，", "世界。", "再见"])
        self.assertEqual([u["sentence_end"] for u in units], [False, True, True])  # the last words end the speech

    def test_camera_cuts_ignore_motion_and_flicker(self):
        adjacent = np.full(400, 0.004, np.float32)
        adjacent[100] = 0.2                       # a real cut
        adjacent[180:280:2] = 0.09                # duplicated-frame motion: spikes every other frame
        adjacent[230] = 0.3                       # a real cut during that motion
        adjacent[300] = 0.15
        adjacent[302] = 0.16                      # two spikes within 6 frames: one cut
        cuts = CATALOG.camera_cuts(adjacent)
        self.assertIn(100, cuts)
        self.assertIn(230, cuts)
        self.assertIn(302, cuts)
        self.assertFalse([c for c in cuts if 190 < c < 270 and c != 230], cuts)

    def test_island_stays_between_neighbour_words(self):
        env = np.full(300, -60.0, np.float32)
        env[100:150] = -10.0                      # speech 1.00-1.50 s
        env[150:175] = -40.0                      # audible decay
        env[178:220] = -12.0                      # next word right after
        lo, hi = CATALOG.island(env, -47.0, 1.0, 1.4, 0.0, 1.78)
        self.assertEqual(lo, 1.0)
        self.assertAlmostEqual(hi, 1.4 + CATALOG.GROW_RIGHT, places=2)  # decay kept up to the growth cap
        lo, hi = CATALOG.island(env, -47.0, 1.0, 1.4, 0.0, 1.6)
        self.assertLessEqual(hi, 1.6)

    def test_ranges_round_trip(self):
        spans = CATALOG.ranges([1, 2, 3, 7, 8, 10])
        self.assertEqual(spans, [[1, 3], [7, 8], [10, 10]])
        self.assertEqual(CATALOG.expand(spans), [1, 2, 3, 7, 8, 10])

    def test_stability_windows(self):
        adjacent = np.full(200, 0.004, np.float32)
        endpoint = np.full(200, 0.01, np.float32)
        adjacent[60] = 0.2
        self.assertFalse(CATALOG.head_stable(adjacent, endpoint, 40))   # cut 20 frames after the start
        self.assertTrue(CATALOG.head_stable(adjacent, endpoint, 60))    # starting on the cut is fine
        self.assertFalse(CATALOG.tail_stable(adjacent, endpoint, 75))
        self.assertTrue(CATALOG.tail_stable(adjacent, endpoint, 60))


class RenderHelperTests(unittest.TestCase):
    def test_video_seek_is_exact_frame_start_rounded_up(self):
        for frame, fps in ((946, (60, 1)), (3000, (60000, 1001)), (200, (25, 1))):
            seek = RENDER.video_seek({"in": frame, "fps_num": fps[0], "fps_den": fps[1]})
            self.assertGreaterEqual(seek, frame * fps[1] / fps[0])
            self.assertLess(seek - frame * fps[1] / fps[0], 2e-9)

    def test_lone_game_name_merges_when_short_or_repeated(self):
        rows = [{"start": 0.0, "end": 1.0, "text": "开局", "clip": 0}, {"start": 1.0, "end": 1.5, "text": "无尽冬日", "clip": 0},
                {"start": 2.0, "end": 3.0, "text": "无尽冬日", "clip": 1}, {"start": 3.0, "end": 4.0, "text": "无尽冬日", "clip": 1}]
        merged = RENDER.merge_lone_names(rows, ["无尽冬日"])
        self.assertEqual([r["text"] for r in merged], ["开局无尽冬日", "无尽冬日无尽冬日"])

    def test_srt_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "subtitle-P01.txt"
            RENDER.write_srt([{"start": 0.1, "end": 1.4, "text": "玩无尽冬日"}, {"start": 1.4, "end": 2.6, "text": "温度最重要"}], path, 3000)
            cues = PACKAGE.parse_srt(path, 3000)
            self.assertEqual([(c["start_ms"], c["end_ms"], c["text"]) for c in cues], [(100, 1400, "玩无尽冬日"), (1400, 2600, "温度最重要")])
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\n这一行字幕太长了超过十个字\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                PACKAGE.parse_srt(path, 3000)


class PackagingDefaultTests(unittest.TestCase):
    def cues(self, *texts, length=1500):
        return [{"start_ms": i * length, "end_ms": (i + 1) * length, "text": t} for i, t in enumerate(texts)]

    def test_default_design_is_valid(self):
        cues = self.cues("玩无尽冬日", "温度最重要", "无尽冬日", "快来玩")
        value = PACKAGE.default_design(cues, ["无尽冬日"], 0, "w8", "ice2")
        prepared = design.prepare(value, cues, 600)
        self.assertEqual(prepared["cues"][0]["entrance"]["effect"], "bounce_up")
        self.assertEqual(prepared["cues"][2]["entrance"]["effect"], "ice_drift")
        self.assertTrue(any(span.get("flower") == "ice2" for span in prepared["cues"][0]["spans"]))

    def test_batch_variety_is_filled_in(self):
        outputs = [self.cues("开局先砍树呀", "冲呀"), self.cues("开局先砍树呀", "好"), self.cues("开局先砍树呀", "来")]
        values = [PACKAGE.default_design(c, [], 0, "w8", "ice2") for c in outputs]
        PACKAGE.balance_entrances(values, outputs)
        prepared = [design.prepare(v, c, 600) for v, c in zip(values, outputs)]
        self.assertEqual(len(design.batch_effects(prepared)["animation_counts"]), 3)

    def test_overrides_apply_and_skip_changed_text(self):
        cues = self.cues("玩无尽冬日", "温度最重要")
        value = PACKAGE.default_design(cues, ["无尽冬日"], 1, "w8", "ice2")
        notes = PACKAGE.apply_overrides(value, [{"index": 2, "y": 0.82, "entrance": "bounce_up"},
                                                {"index": 1, "text": "旧文字", "color": "white"}], cues)
        self.assertEqual(value["cues"][1]["layout"], {"y": 0.82})
        self.assertEqual(value["cues"][1]["entrance"]["effect"], "bounce_up")
        self.assertEqual(value["cues"][0]["color"], "yellow")
        self.assertTrue(notes)

    def test_cues_must_stay_inside_clips(self):
        output = {"clips": [{"out_in": 0, "out_out": 120, "text": "玩无尽冬日"}, {"out_in": 120, "out_out": 240, "text": "温度最重要"}]}
        errors, warnings = PACKAGE.check_cues(self.cues("玩无尽冬日", "温度最重要", length=2000), output, ["无尽冬日"])
        self.assertFalse(errors)
        errors, _ = PACKAGE.check_cues([{"start_ms": 0, "end_ms": 2500, "text": "玩无尽冬日"},
                                        {"start_ms": 2500, "end_ms": 4000, "text": "温度最重要"}], output, ["无尽冬日"])
        self.assertTrue(errors)
        _, warnings = PACKAGE.check_cues(self.cues("玩无尽冬至", "温度最重要", length=2000), output, ["无尽冬日"])
        self.assertTrue(any("differs" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
