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

    def test_edge_window_is_measured_in_time(self):
        self.assertEqual(CATALOG.edge_frames(60), 30)
        self.assertEqual(CATALOG.edge_frames(25), 13)
        first, second = units_over(-25.0, fps=25)
        self.assertTrue(first["out_frames"] and second["in_frames"])


if __name__ == "__main__":
    unittest.main()
