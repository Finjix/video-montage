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

    def test_zoom_after_the_in_point_blocks_the_start(self):
        # speech at 1.0 s; a 4 % punch-in at frame 54 (between the earliest start and the speech) and a slow push
        words = [{"word": "开局先砍树，", "start": 1.0, "end": 2.0}]
        env = np.full(500, -70.0, np.float32)
        env[100:200] = -8.0

        def ins(zoom):
            clock = {"fps_num": 60, "fps_den": 1, "frames": 300, "duration": 5.0, "cuts": []}
            series = (np.zeros(300, np.float32), np.zeros(300, np.float32), zoom)
            return CATALOG.expand(CATALOG.finish_units([{"words": list(words), "sentence_end": True}], env, clock, series)[0]["in_frames"])
        punch = np.zeros(300, np.float32)
        punch[54] = 0.04
        starts = ins(punch)
        self.assertTrue(starts and min(starts) >= 54, starts)
        push = np.zeros(300, np.float32)
        push[56], push[57:65] = 0.006, 0.001      # a quick push: a snap, then easing
        self.assertFalse([f for f in ins(push) if f < 56])
        lean = np.zeros(300, np.float32)
        lean[50:60] = 0.006                       # leaning in over ten frames: no single frame stands out
        self.assertTrue([f for f in ins(lean) if f < 50])

    def test_frame_zoom_reads_scale_and_ignores_local_motion(self):
        rng = np.random.default_rng(1)
        base = rng.random((114, 64)).astype(np.float32)
        smooth = np.cumsum(np.cumsum(base, 0), 1)
        smooth = (smooth - smooth.min()) / (smooth.max() - smooth.min())
        ys, xs = np.mgrid[0:114, 0:64].astype(np.float32)
        pattern = np.sin(xs / 3.0) * np.cos(ys / 4.0) * 0.5 + 0.5 + smooth * 0.2
        cy, cx = 56.5, 31.5
        scale = 1.03
        zoomed = np.zeros_like(pattern)
        sy, sx = cy + (ys - cy) / scale, cx + (xs - cx) / scale
        y0, x0 = np.floor(sy).astype(int).clip(0, 112), np.floor(sx).astype(int).clip(0, 62)
        fy, fx = sy - y0, sx - x0
        zoomed = (pattern[y0, x0] * (1 - fx) * (1 - fy) + pattern[y0, x0 + 1] * fx * (1 - fy)
                  + pattern[y0 + 1, x0] * (1 - fx) * fy + pattern[y0 + 1, x0 + 1] * fx * fy)
        self.assertAlmostEqual(CATALOG.frame_zoom(pattern, zoomed), np.log(scale), delta=0.01)
        moved = pattern.copy()
        moved[70:100, 10:30] = moved[70:100, 14:34]   # a hand moving in a corner
        self.assertLess(abs(CATALOG.frame_zoom(pattern, moved)), 0.003)

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
