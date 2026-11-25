"""Batch delivery preflights the whole order and safely resumes named A/B variants."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from montage.common import sha, write
from montage.deliver_batch import deliver


@unittest.skipUnless(importlib.util.find_spec("numpy"), "requires the managed media environment")
class DeliverBatch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vm-deliver-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.enterContext(patch("montage.runtime.cache", return_value=self.base / "cache"))
        self.job = self.base / "临时文件"
        self.job.mkdir()
        self.output = self.base / "成片"
        for stem in ("A1", "B1", "B2", "尾板", "替换尾板"):
            (self.base / f"{stem}.mp4").write_bytes(stem.encode("utf-8"))
        first = {
            "material_id": "GOFCN0001", "name_fields": ["AI剧情", "设定3.0"], "keyword": "冰湖救人ZJJC",
            "series": "剧情叙事", "a": "A1.mp4", "b": "B1.mp4",
        }
        self.config = {
            "output_dir": "成片", "endcard": "尾板.mp4", "expected_outputs": 2,
            "outputs": [first, {**first, "b": "B2.mp4", "endcard": "替换尾板.mp4", "series": "幸存者管理城建"}],
        }
        self.first = self.output / "GOFCN0001_AI剧情_设定3.0_冰湖救人ZJJC_剧情叙事.mp4"
        self.second = self.output / "GOFCN0001_AI剧情_设定3.0_冰湖救人ZJJC_幸存者管理城建.mp4"

    @staticmethod
    def assemble(a, b, endcard, output, **kwargs):
        output.write_bytes(a.read_bytes() + b.read_bytes() + endcard.read_bytes())
        return {"integrated": {"lufs": -12.0}}

    def test_filename_collision_checked_before_rendering(self):
        self.config["outputs"][1]["series"] = self.config["outputs"][0]["series"]
        self.config["outputs"][1]["material_id"] = "gofcn0001"
        with patch("montage.delivery.assemble") as render:
            with self.assertRaises(ValueError):
                deliver(self.job, self.config, self.base)
            render.assert_not_called()
        self.assertFalse(self.first.exists())

    def test_packaged_plan_coverage_and_legacy_master_paths(self):
        write(self.job / "job.json", {})
        write(self.job / "reports/package.json", {"complete": True})
        masters = [self.base / "A1.mp4", self.base / "B2.mp4"]
        packaged = [{"plan_id": f"P0{i}", "path": str(path), "sha256": sha(path)}
                    for i, path in enumerate(masters, 1)]
        write(self.job / "delivery.json", {"outputs": packaged})
        for row in self.config["outputs"]:
            del row["a"]
            row["plan_id"] = "P01"
        with self.assertRaisesRegex(ValueError, "P02"):
            deliver(self.job, self.config, self.base, check_only=True)
        self.config["outputs"][1]["plan_id"] = "P02"
        self.assertEqual(deliver(self.job, self.config, self.base, check_only=True)["checked"], 2)
        with patch("montage.delivery.assemble", side_effect=self.assemble):
            deliver(self.job, self.config, self.base)
        self.assertEqual(self.second.read_bytes(), "B2B2替换尾板".encode())

    def test_untracked_second_target_protected_before_rendering_first(self):
        self.output.mkdir()
        self.second.write_bytes(b"other owner's file")
        with patch("montage.delivery.assemble") as render:
            with self.assertRaises((ValueError, FileExistsError)):
                deliver(self.job, self.config, self.base)
            render.assert_not_called()
        self.assertFalse(self.first.exists())
        self.assertEqual(self.second.read_bytes(), b"other owner's file")

    def test_same_a_id_multiple_variants_and_resume(self):
        with patch("montage.delivery.assemble", side_effect=self.assemble) as render:
            result = deliver(self.job, self.config, self.base)
            self.assertTrue(result["complete"])
            self.assertEqual(result["target_lufs"], -12)
            self.assertEqual(self.first.read_bytes(), "A1B1尾板".encode())
            self.assertEqual(self.second.read_bytes(), "A1B2替换尾板".encode())
            self.assertEqual(render.call_count, 2)
            render.reset_mock()
            self.config["target_lufs"] = -12.0
            self.assertTrue(deliver(self.job, self.config, self.base)["complete"])
            render.assert_not_called()

    def test_other_target_is_rejected_during_preflight_and_delivery(self):
        self.config["target_lufs"] = -16
        with patch("montage.delivery.assemble") as render:
            for check_only in (True, False):
                with self.subTest(check_only=check_only), self.assertRaisesRegex(ValueError, "-12"):
                    deliver(self.job, self.config, self.base, check_only=check_only)
            render.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unmeasurable_or_off_target_legacy_encodes_are_rebuilt(self):
        with patch("montage.delivery.assemble", side_effect=self.assemble) as render:
            old = deliver(self.job, self.config, self.base)
            old["files"][self.first.name]["qa"]["integrated"]["lufs"] = None
            old["files"][self.second.name]["qa"]["integrated"]["lufs"] = -16.0
            write(self.job / "reports/final-delivery.json", old)
            self.config["outputs"][1]["series"] = "庇护所升级"
            render.reset_mock()
            result = deliver(self.job, self.config, self.base)
        self.assertTrue(result["complete"])
        self.assertEqual(render.call_count, 2)
        self.assertTrue(all(row["qa"]["integrated"]["lufs"] == -12 for row in result["files"].values()))

    def test_revision_retires_only_its_obsolete_name(self):
        with patch("montage.delivery.assemble", side_effect=self.assemble) as render:
            self.assertTrue(deliver(self.job, self.config, self.base)["complete"])
            unrelated = self.output / "其他批次.mp4"
            unrelated.write_bytes(b"keep")
            self.config["outputs"][0]["series"] = "庇护所升级"
            self.assertTrue(deliver(self.job, self.config, self.base)["complete"])
            self.assertEqual(render.call_count, 2)
        replacement = self.output / "GOFCN0001_AI剧情_设定3.0_冰湖救人ZJJC_庇护所升级.mp4"
        self.assertEqual(replacement.read_bytes(), "A1B1尾板".encode())
        self.assertFalse(self.first.exists())
        self.assertTrue(self.second.is_file())
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_old_output_used_as_new_source_is_preserved_and_released(self):
        with patch("montage.delivery.assemble", side_effect=self.assemble):
            deliver(self.job, self.config, self.base)
            row = self.config["outputs"][0]
            original = self.first.read_bytes()
            row.update(series="庇护所升级", a=str(self.first))
            self.assertTrue(deliver(self.job, self.config, self.base)["complete"])
            self.assertEqual(original, self.first.read_bytes())
            row.update(series="剧情叙事", a="A1.mp4")
            with self.assertRaises((ValueError, FileExistsError)):
                deliver(self.job, self.config, self.base)
            self.assertEqual(original, self.first.read_bytes())

    def test_revision_can_change_only_letter_case_without_losing_video(self):
        with patch("montage.delivery.assemble", side_effect=self.assemble):
            deliver(self.job, self.config, self.base)
            self.config["outputs"][0]["material_id"] = "gofcn0001"
            self.assertTrue(deliver(self.job, self.config, self.base)["complete"])
        renamed = self.output / self.first.name.replace("GOFCN", "gofcn")
        self.assertEqual(renamed.read_bytes(), "A1B1尾板".encode())
        self.assertEqual({p.name for p in self.output.glob("*.mp4")}, {renamed.name, self.second.name})
