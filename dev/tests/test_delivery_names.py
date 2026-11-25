"""Delivery names carry the A keyword and B series through both numbering schemes."""
import unittest

from montage.delivery_names import delivery_filename


class DeliveryNames(unittest.TestCase):
    def test_celebrity_ad_with_batch_labels(self):
        self.assertEqual(
            delivery_filename("GOFCN69659", ["营销", "高曙光AI", "口播"], "三分钟一局", "庇护所升级"),
            "GOFCN69659_营销_高曙光AI_口播_三分钟一局_庇护所升级.mp4",
        )

    def test_shared_material_id_for_two_b_variants(self):
        variants = [(["AI剧情", "设定2.0"], "载具升级"), (["AI剧情", "设定3.0"], "幸存者管理城建")]
        names = [delivery_filename("TEST0001", labels, "冰湖漏勺救人ZJJC", series) for labels, series in variants]
        self.assertEqual(names, [
            "TEST0001_AI剧情_设定2.0_冰湖漏勺救人ZJJC_载具升级.mp4",
            "TEST0001_AI剧情_设定3.0_冰湖漏勺救人ZJJC_幸存者管理城建.mp4",
        ])

    def test_empty_fields_embedded_separators_and_invalid_windows_names_are_rejected(self):
        for labels in ([""], ["口播_副本"], ["../../其他"], ["D:\\其他"], ["尾板?"], "营销"):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                delivery_filename("GOFCN001", labels, "开局采集", "载具升级")
        for material_id in ("NUL.1", "字" * 255):
            with self.subTest(material_id=material_id), self.assertRaises(ValueError):
                delivery_filename(material_id, [], "开局采集", "载具升级")
