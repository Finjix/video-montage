"""Focused behavior and measurement regressions for the independent tool."""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from common import foreground_ssim, EFFECTS, LAB, ROOT
from renderer import Animation, text_sprite


def oracle_ssim(a, b):
    sys.path.insert(0, str(ROOT / "tools"))
    from compare_subtitle_flower import foreground_ssim as oracle
    return oracle(a, b)


class MetricTests(unittest.TestCase):
    def test_roi_matches_existing_metric_including_near_canvas_edge(self):
        rng = np.random.default_rng(7)
        for y, x in [(3, 2), (80, 100)]:
            a, b = np.zeros((240, 320, 3), np.uint8), np.zeros((240, 320, 3), np.uint8)
            a[y:y + 50, x:x + 80] = rng.integers(0, 256, (50, 80, 3), dtype=np.uint8)
            b[y:y + 50, x:x + 80] = rng.integers(0, 256, (50, 80, 3), dtype=np.uint8)
            self.assertAlmostEqual(foreground_ssim(a, b), oracle_ssim(a, b), places=12)

    def test_empty_frame_and_false_appearance(self):
        a = np.zeros((100, 100, 3), np.uint8)
        self.assertEqual(foreground_ssim(a, a), 1.)
        b = a.copy()
        b[40:60, 40:60] = 2  # Below the same threshold: no subtitle in either frame.
        self.assertEqual(foreground_ssim(a, b), 1.)
        b[40:60, 40:60] = 255
        self.assertLess(foreground_ssim(a, b), .1)

    def test_black_padding_cannot_raise_score(self):
        a = np.zeros((100, 100, 3), np.uint8)
        b = a.copy()
        a[30:50, 30:60] = 220
        b[32:52, 32:62] = 220
        score = foreground_ssim(a, b)
        padded_a = cv2.copyMakeBorder(a, 400, 400, 300, 300, cv2.BORDER_CONSTANT)
        padded_b = cv2.copyMakeBorder(b, 400, 400, 300, 300, cv2.BORDER_CONSTANT)
        self.assertAlmostEqual(score, foreground_ssim(padded_a, padded_b), places=12)


class RendererTests(unittest.TestCase):
    def test_replacement_text_is_deterministic_and_never_reads_a_video(self):
        with patch.object(cv2, "VideoCapture", side_effect=AssertionError("runtime read a reference")):
            for effect in EFFECTS:
                animation = Animation(effect, "冰雪世界")
                a = animation.frame(24, 1440, 2560)
                b = animation.frame(24, 1440, 2560)
                np.testing.assert_array_equal(a, b)
                self.assertEqual(a.shape, (2560, 1440, 3))
                self.assertGreater(int(a.max()), 100)

    def test_stable_phrase_is_changed_and_long_text_fits_canvas(self):
        old = Animation("bounce_up", "无尽冬日").frame(43)
        for text in ["冰雪", "全新挑战即将开启", "ABC冰雪2026"]:
            image = Animation("ice_drift", text).frame(43)
            self.assertFalse(np.array_equal(image, old))
            ys, xs = np.where(image.max(axis=2) > 25)
            self.assertTrue(len(xs))
            self.assertGreater(int(xs.min()), 0)
            self.assertLess(int(xs.max()), 1919)
            self.assertGreater(int(ys.min()), 0)
            self.assertLess(int(ys.max()), 3413)

    def test_settled_frames_are_identical_across_effects(self):
        images = [Animation(effect, "冰雪世界").frame(40, 1440, 2560) for effect in EFFECTS]
        for image in images[1:]:
            np.testing.assert_array_equal(images[0], image)

    def test_invalid_text_dimensions_and_indices_are_rejected(self):
        for value in ["", "  ", "一\n二", "一\t二"]:
            with self.assertRaises(ValueError):
                text_sprite(value)
        animation = Animation("bounce_up")
        with self.assertRaises(ValueError):
            animation.frame(60)
        with self.assertRaises(ValueError):
            animation.frame(1, 640, 480)


if __name__ == "__main__":
    unittest.main()
