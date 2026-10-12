"""Regression cases from the October batch, including actual font geometry."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import subtitle_segmentation as split
import packaging_design as design


def rows(text):
    words, cursor = [], .1
    for char in text:
        if char in ",，。!?！？、":
            words[-1]["word"] += char
        else:
            words.append({"word": char, "start": cursor, "end": cursor + .2})
            cursor += .2
    return split.caption_rows({"segments": [{"text": text, "start": .1, "end": cursor, "words": words}]})


class SemanticReadingTests(unittest.TestCase):
    def test_clause_tail_never_moves_into_partial_next_clause(self):
        result = rows("喝茶盘串那就快乐，现在玩无尽冬日更是快乐，三分钟一局，巨爽巨解压。")
        self.assertFalse(any("快乐现在" in row["text"] for row in result))
        self.assertTrue(any("三分钟一局" in row["text"] for row in result))
        self.assertFalse(any(row["text"] == "一局" for row in result))

    def test_dependent_particle_and_complete_word_heads(self):
        result = rows("一上来就给你几个逃难的幸存者，你得收留他们。")
        self.assertFalse(any(row["text"].startswith("的") for row in result))
        self.assertFalse(any("者你得" in row["text"] for row in result))
        self.assertEqual(["的确很好", "了解规则"], [r["text"] for r in rows("的确很好。了解规则。")])

    def test_complete_sentence_is_not_divided_into_three_fragments(self):
        self.assertEqual(["我收了三十个幸存者"], [r["text"] for r in rows("我收了三十个幸存者。")])
        self.assertEqual(["我收了30个幸存者"], [r["text"] for r in rows("我收了30个幸存者。")])

    def test_quantity_classifier_and_game_name_are_atomic(self):
        result = rows("无尽冬日可是全国两亿人都在玩的冰雪游戏。")
        self.assertEqual(1, sum("两亿人" in r["text"] for r in result))
        self.assertEqual(1, sum("无尽冬日" in r["text"] for r in result))
        self.assertTrue(all(split.fits(r["text"]) for r in result))

    def test_real_width_allows_longer_complete_phrase(self):
        self.assertTrue(split.fits("这是中年人的解压神器"))
        self.assertFalse(split.fits("这是中年人的解压神器而且很好玩"))

    def test_english_spacing_and_word_boundaries(self):
        asr = {"segments": [{"text": "Play this game now.", "start": 0, "end": 3,
                           "words": [{"word": "Play this game now.", "start": 0, "end": 3}]}]}
        result = split.caption_rows(asr)
        self.assertEqual("Play this game now", " ".join(r["text"] for r in result))

    def test_shot_offsets_and_final_speed_reading(self):
        result = split.caption_rows({"shot_observations": [{"output_in_frame": 120,
            "asr": {"segments": [{"text": "幸存者", "start": .1, "end": .8}]}}]})
        self.assertAlmostEqual(2.1, result[0]["start"])
        self.assertAlmostEqual(2.8, result[0]["end"])
        self.assertEqual("short_reading", split.reading_issues([
            {"text": "幸存者", "start_ms": 2100, "end_ms": 2800}])[0]["code"])
        self.assertEqual("dependent_head", split.reading_issues([
            {"text": "的幸存者", "start_ms": 0, "end_ms": 1500}])[0]["code"])

    def test_standalone_game_requires_settled_reading_and_one_occurrence(self):
        def prepare(intervals, entrance=200):
            cues = [{"text": "无尽冬日", "start_ms": start, "end_ms": end} for start, end in intervals]
            value = {"subtitle_sha256": "hash", "font": "w8", "game_names": ["无尽冬日"],
                     "reason": "Full existing flower with settled reading", "reading_policy": split.POLICY,
                     "cues": [{"index": i, "text": cue["text"], "entrance": {
                         "effect": "ice_drift", "duration_ms": entrance}} for i, cue in enumerate(cues, 1)]}
            return design.prepare(value, cues, "hash", [], Path.cwd(), 180)
        self.assertEqual("ice_drift", prepare([(0, 960)])["cues"][0]["entrance"]["effect"])
        with self.assertRaisesRegex(ValueError, "settled"):
            prepare([(0, 960)], 300)
        with self.assertRaisesRegex(ValueError, "800ms"):
            prepare([(0, 900)])
        with self.assertRaisesRegex(ValueError, "at most one"):
            prepare([(0, 1000), (1200, 2200)])


if __name__ == "__main__":
    unittest.main()
