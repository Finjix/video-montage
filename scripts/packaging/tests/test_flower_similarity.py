"""Keep the 95% floor on procedurally generated text and final burned video."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("flower_similarity", ROOT / "tools/compare_subtitle_flower.py")
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


class FlowerSimilarityTests(unittest.TestCase):
    def test_all_three_styles_exceed_95_percent_in_production_video(self):
        with tempfile.TemporaryDirectory() as temp:
            for style_id in ("fire1", "ice1", "ice2"):
                with self.subTest(style=style_id):
                    reference = ROOT / "assets/packaging/flowers/references" / f"{style_id}.png"
                    report = comparison.compare(reference, Path(temp) / style_id, style_id)
                    self.assertGreaterEqual(report["score"], .95)
                    self.assertGreaterEqual(report["video_roundtrip_score"], .95)
                    self.assertTrue(report["pass"])
                    self.assertTrue(report["video_roundtrip_pass"])


if __name__ == "__main__":
    unittest.main()
