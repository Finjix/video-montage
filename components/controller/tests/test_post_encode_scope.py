from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from post_encode_evidence import validate_plan_scope


class PostEncodeScopeTests(unittest.TestCase):
    def test_partial_delivery_is_rejected(self):
        delivery = {"output_count": 1, "results": [{"plan_id": "P1"}]}
        locked = {"plans": [{"plan_id": "P1"}, {"plan_id": "P2"}]}
        self.assertIn("PARTIAL_BATCH_OR_DEPENDENCY_SCOPE", validate_plan_scope(delivery, locked))

    def test_exact_complete_scope_passes(self):
        delivery = {"output_count": 2, "results": [{"plan_id": "P1"}, {"plan_id": "P2"}]}
        locked = {"plans": [{"plan_id": "P2"}, {"plan_id": "P1"}]}
        self.assertEqual([], validate_plan_scope(delivery, locked))


if __name__ == "__main__":
    unittest.main()
