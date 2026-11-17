"""init lists every gap of a work order at once and can replace a job that was never prepared."""
import tempfile
import unittest
from pathlib import Path

from montage import job as JOB

# init reads only the PNG header: a transparent 9:16 plate is 1080x1920
PNG = (bytes.fromhex("89504e470d0a1a0a") + b"\x00\x00\x00\rIHDR" + (1080).to_bytes(4, "big")
       + (1920).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")


class Init(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-init-")
        self.folder = Path(self.temp.name)
        self.sources = []
        for number in range(3):
            source = self.folder / f"source{number}.mp4"
            source.write_bytes(f"fixture {number}".encode())
            self.sources.append(str(source))
        self.pack = self.folder / "pack"
        for name in ("文字钉/pin.png", "免责/disclaimer.png"):
            (self.pack / name).parent.mkdir(parents=True)
            (self.pack / name).write_bytes(PNG)
        (self.folder / "plate.png").write_bytes(PNG)

    def tearDown(self):
        self.temp.cleanup()

    def order(self, **changes):
        order = {"requested_outputs": 3, "sources": self.sources, "asset_root": str(self.pack),
                 "output_root": str(self.folder / "out"), "request": "test",
                 "celebrities": [{"person_id": "person:test", "name": "测试", "nameplate": str(self.folder / "plate.png")}]}
        return {**order, **changes}

    def test_every_gap_in_one_error(self):
        (self.pack / "免责/disclaimer.png").unlink()
        order = self.order(sources=self.sources[:2], celebrities=[{"person_id": "person:test", "name": "测试",
                                                                   "nameplate": str(self.folder / "missing.png")}])
        with self.assertRaises(ValueError) as caught:
            JOB.init(order)
        message = str(caught.exception)
        self.assertIn("exactly one disclaimer", message)
        self.assertIn("1 text pins, 0 disclaimer PNGs", message)
        self.assertIn("nameplate PNG", message)
        self.assertIn("2 source files", message)
        self.assertFalse((self.folder / "out").exists())

    def test_reinit_replaces_an_unprepared_job_only(self):
        job, _ = JOB.init(self.order())
        again, state = JOB.init(self.order(requested_outputs=5), job)
        self.assertEqual(again, job)
        self.assertEqual(state["order"]["requested_outputs"], 5)
        (job / "catalog").mkdir()
        (job / "catalog/catalog.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            JOB.init(self.order(), job)


if __name__ == "__main__":
    unittest.main()
