"""Nameplates cut to their visible size and placed clear of faces (face detection stubbed)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HAVE_IMAGING = importlib.util.find_spec("numpy") and importlib.util.find_spec("PIL")
WU, XU = "person:wu-yue", "person:xu-jun-cong"


def plate_png(folder: Path, size=(1440, 2560), box=(79, 391, 364, 1299)) -> dict:
    from PIL import Image
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((30, 90, 200, 255), box)
    path = folder / f"plate-{size[0]}.png"
    image.save(path)
    return {"path": str(path), "sha256": "test"}


@unittest.skipUnless(HAVE_IMAGING, "needs numpy and Pillow")
class Plates(unittest.TestCase):
    def setUp(self):
        from montage.packaging import nameplates
        self.N = nameplates
        self.temp = tempfile.TemporaryDirectory(prefix="vm-plates-")
        self.folder = Path(self.temp.name)
        self.plate = nameplates.trim(plate_png(self.folder), WU, self.folder / "nameplates")

    def tearDown(self):
        self.temp.cleanup()

    def test_trim_keeps_the_visible_plate_and_its_designed_position(self):
        from PIL import Image
        self.assertEqual((self.plate["x"], self.plate["y"], self.plate["width"], self.plate["height"]), (79, 391, 285, 908))
        self.assertEqual(Path(self.plate["path"]).name, "wu-yue.png")
        with Image.open(self.plate["path"]) as image:
            self.assertEqual(image.size, (285, 908))
        small = self.N.trim(plate_png(self.folder, (1080, 1920), (60, 300, 270, 975)), XU, self.folder / "nameplates")
        # drawn for 1080 wide: cut there, then scaled with its position to the 1440 frame
        self.assertEqual((small["x"], small["y"], small["width"], small["height"]), (80, 400, 280, 900))

    def test_spots_mirror_the_margin_and_end_above_the_subtitles(self):
        spots = self.N.spots(self.plate)
        self.assertEqual(spots["upper-left"], (79, 391))
        self.assertEqual(spots["upper-right"], (1440 - 79 - 285, 391))
        self.assertEqual(spots["lower-right"][1] + 908, self.N.LOWER_BOTTOM)
        self.assertEqual(self.N.preference(self.plate), ["upper-left", "upper-right", "lower-left", "lower-right"])

    def test_the_designed_spot_wins_unless_it_covers_a_face(self):
        opaque = self.N.mask(self.plate)
        centre = {0: [(520, 500, 920, 1000)]}
        self.assertEqual(self.N.place(self.plate, opaque, centre, [0])[0], "upper-left")
        left = {0: [(0, 500, 500, 1000)], 12: [(40, 520, 540, 1020)]}
        spot, cover = self.N.place(self.plate, opaque, left, [0, 12])
        self.assertEqual(spot, "upper-right")
        self.assertGreater(cover["upper-left"], .3)
        high = {0: [(0, 380, 400, 700), (1000, 380, 1440, 700)]}       # two faces high on both sides
        self.assertEqual(self.N.place(self.plate, opaque, high, [0])[0], "lower-left")

    def test_with_faces_everywhere_the_least_covered_spot_is_taken(self):
        opaque = self.N.mask(self.plate)
        everywhere = {0: [(0, 300, 1440, 1700)]}
        spot, cover = self.N.place(self.plate, opaque, everywhere, [0])
        self.assertTrue(all(value > self.N.CLEAR for value in cover.values()))
        self.assertEqual(cover[spot], min(cover.values()))


@unittest.skipUnless(HAVE_IMAGING and importlib.util.find_spec("cv2"), "needs numpy, Pillow and OpenCV")
class PackagePlacement(unittest.TestCase):
    def setUp(self):
        from montage import package
        from montage.packaging import nameplates
        self.P, self.N = package, nameplates
        self.temp = tempfile.TemporaryDirectory(prefix="vm-place-")
        folder = Path(self.temp.name)
        self.plates = {WU: nameplates.trim(plate_png(folder), WU, folder / "nameplates")}
        self.calls = []
        self.original = nameplates.faces

        def faces(video, numbers):
            self.calls.append(video)
            return {n: [(0, 500, 500, 1000)] for n in numbers}            # a face on the left
        nameplates.faces = faces

    def tearDown(self):
        self.N.faces = self.original
        self.temp.cleanup()

    def item(self, position=None, previous=None):
        plate = self.plates[WU]
        return {"plan_id": "P01", "clean": "clean.mp4", "clean_sha256": "c1", "previous": previous,
                "layers": [{"path": plate["path"], "sha256": plate["sha256"], "person_id": WU, "start_frame": 0,
                            "end_frame_exclusive": 120, "position": position}, {"path": "pin.png", "sha256": "p"}]}

    def test_auto_spot_moves_off_the_face_and_is_reused(self):
        item, warnings = self.item(), {}
        self.P.place_nameplates([item], self.plates, warnings)
        layer = item["layers"][0]
        self.assertEqual((layer["position"], layer["x"], layer["y"]), ("upper-right", 1076, 391))
        self.assertEqual(warnings, {})
        again = self.item(previous={"nameplates": item["nameplates"]})
        self.P.place_nameplates([again], self.plates, {})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(again["layers"][0]["position"], "upper-right")

    def test_a_configured_spot_over_a_face_is_kept_with_a_note(self):
        item, warnings = self.item("upper-left"), {}
        self.P.place_nameplates([item], self.plates, warnings)
        self.assertEqual(item["layers"][0]["position"], "upper-left")
        self.assertEqual(item["nameplates"]["rows"][0]["auto"], "upper-right")
        self.assertIn("upper-right is clear", warnings["P01"][0])

    def test_with_no_clear_spot_only_the_automatic_choice_gets_a_note(self):
        self.N.faces = lambda video, numbers: {n: [(0, 300, 1440, 1700)] for n in numbers}   # face everywhere
        auto, configured, warnings = self.item(), self.item("lower-left"), {}
        configured["plan_id"] = "P02"
        self.P.place_nameplates([auto, configured], self.plates, warnings)
        self.assertEqual(list(warnings), ["P01"])
        self.assertIn("touches a face at every spot", warnings["P01"][0])
        self.assertTrue(configured["nameplates"]["rows"][0]["configured"])


if __name__ == "__main__":
    unittest.main()
