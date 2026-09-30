from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from v9_gate_runtime import opening_visual_reuse_failures


class OpeningVisualDiversityTests(unittest.TestCase):
    def test_actual_visual_family_not_candidate_id_is_capped_at_three(self):
        families = Counter({"car-brown-shirt": 8, "orange-chair": 3})
        failures = opening_visual_reuse_failures(families, Counter(), 3, 2)
        self.assertIn("OPENING_VISUAL_FAMILY_REUSE_EXCEEDED", {item[0] for item in failures})

    def test_three_uses_passes(self):
        failures = opening_visual_reuse_failures(Counter({"car": 3, "desk": 3}), Counter({("car", "feature"): 2}), 3, 2)
        self.assertEqual([], failures)

    def test_opening_second_visual_pair_is_independently_capped(self):
        failures = opening_visual_reuse_failures(Counter({"car": 3}), Counter({("car", "feature"): 3}), 3, 2)
        self.assertIn("OPENING_SECOND_VISUAL_PAIR_REUSE_EXCEEDED", {item[0] for item in failures})


if __name__ == "__main__":
    unittest.main()
