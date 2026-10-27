"""Clean cut windows of speech units: pauses over a music bed and frame-rate independent picture checks."""
import unittest

import numpy as np

from montage import catalog as CATALOG


def units_over(bed_db: float, fps: int = 60) -> list[dict]:
    """Two clauses with a 0.2 s pause between them, speech at -8 dBFS over a constant bed."""
    words = [[{"word": "开局先砍树，", "start": 1.0, "end": 2.0}], [{"word": "再升熔炉。", "start": 2.2, "end": 3.2}]]
    env = np.full(500, bed_db, np.float32)
    for row in words:
        env[int(row[0]["start"] / CATALOG.HOP):int(row[-1]["end"] / CATALOG.HOP)] = -8.0
    clock = {"fps_num": fps, "fps_den": 1, "frames": 5 * fps, "duration": 5.0, "cuts": []}
    series = (np.zeros(5 * fps, np.float32), np.zeros(5 * fps, np.float32))
    return CATALOG.finish_units([{"words": row, "sentence_end": True} for row in words], env, clock, series)


class Edges(unittest.TestCase):
    def test_pause_over_a_quiet_room_is_a_cut(self):
        first, second = units_over(-70.0)
        self.assertTrue(first["out_frames"] and second["in_frames"])

    def test_pause_over_a_music_bed_is_a_cut(self):
        # a bed louder than -35 dBFS used to read as speech, growing both clauses over the pause
        first, second = units_over(-25.0)
        self.assertTrue(first["out_frames"] and second["in_frames"])
        self.assertLess(first["protect"][1], 2.2)
        self.assertGreater(second["protect"][0], 2.0)

    def test_threshold_of_clean_sources_is_unchanged(self):
        env = np.concatenate([np.full(100, -70.0), np.full(400, -15.0)]).astype(np.float32)
        self.assertEqual(CATALOG.threshold(env), -50.0)

    def test_merged_flash_keeps_the_edge_unstable(self):
        # a flash at frame 54 (0.1 s before the first word): masked when it is a camera cut, unstable once merged
        words = [{"word": "开局先砍树，", "start": 1.0, "end": 2.0}]
        env = np.full(500, -70.0, np.float32)
        env[100:200] = -8.0
        adjacent = np.zeros(300, np.float32)
        adjacent[54] = 0.2

        def ins(cuts):
            clock = {"fps_num": 60, "fps_den": 1, "frames": 300, "duration": 5.0, "cuts": cuts}
            series = (adjacent, np.zeros(300, np.float32))
            return CATALOG.expand(CATALOG.finish_units([{"words": list(words), "sentence_end": True}], env, clock, series)[0]["in_frames"])
        self.assertIn(54, ins([54]))
        self.assertFalse([f for f in ins([]) if 24 < f < 54])

    def test_annotation_shapes_are_input_errors(self):
        state = {"sources": [{"id": "S01"}]}
        for row in ({"shots": {"2": True}}, {"shots": {"x": {"people": []}}}, {"cameras": [1, 2]}, {"text": ["U01"]},
                    {"exclude": "U01"}, {"people": "person:wu-yue"}):
            with self.assertRaises(ValueError):
                CATALOG.check_annotations({"sources": {"S01": row}}, state)
        CATALOG.check_annotations({"sources": {"S01": {"shots": {"2": {"merge": True}}, "cameras": [[1, 3]]}}}, state)

    def test_edge_window_is_measured_in_time(self):
        self.assertEqual(CATALOG.edge_frames(60), 30)
        self.assertEqual(CATALOG.edge_frames(25), 13)
        first, second = units_over(-25.0, fps=25)
        self.assertTrue(first["out_frames"] and second["in_frames"])


if __name__ == "__main__":
    unittest.main()
