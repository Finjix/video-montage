"""Font selection persistence and real burn-in coverage for new flower fonts."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
import package_video as packaging
import subtitle_fonts


class FontSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_every_font_asset_is_hash_bound_and_can_be_selected_by_copy(self):
        for key, spec in subtitle_fonts.FONTS.items():
            with self.subTest(font=key):
                style = packaging.subtitle_style({"subtitle_font": key}, self.root)
                self.assertEqual(spec["sha256"], packaging.sha(Path(style["font"]["path"])))
                self.assertEqual(key, style["font_id"])
                self.assertEqual(spec["postscript"], style["font_postscript"])
                copy = self.root / f"{key}.otf"
                shutil.copyfile(spec["path"], copy)
                copied = packaging.subtitle_style({"subtitle_font_path": str(copy)}, self.root)
                self.assertEqual(key, copied["font_id"])
                copy.write_bytes(copy.read_bytes() + b"changed")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    packaging.subtitle_style({"subtitle_font_path": str(copy)}, self.root)
        for invalid in (None, "other", [], "得意"):
            with self.assertRaises(ValueError):
                packaging.subtitle_style({"subtitle_font": invalid}, self.root)
        with self.assertRaisesRegex(ValueError, "different fonts"):
            packaging.subtitle_style({"subtitle_font": "w8", "subtitle_font_path": subtitle_fonts.FONTS["smiley"]["path"]}, self.root)

    def test_random_font_is_chosen_once_for_batch_and_frozen_in_snapshot(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"clean video")
        subtitles = self.root / "subtitle.txt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:00,800\n无尽冬日\n", encoding="utf-8")
        config = self.root / "config.json"
        packaging.atomic(config, {"schema": packaging.SCHEMA, "outputs": [
            {"plan_id": key, "input_path": str(source), "subtitle_txt": str(subtitles)} for key in ("P1", "P2")]})
        for key in subtitle_fonts.FONTS:
            with self.subTest(font=key), patch.object(subtitle_fonts.secrets, "choice", return_value=key) as choice, \
                    patch.object(packaging, "video_spec", return_value={"frames": 60, "duration": 1.0, "width": 1440, "height": 2560}):
                rows = packaging.prepared_rows(config)
                choice.assert_called_once_with(("w8", "smiley", "fangtang"))
                self.assertEqual([key, key], [r["subtitle_style"]["font_id"] for r in rows])
                snapshots = [subtitles, subtitles]
                snapshot = self.root / "frozen.json"
                packaging.atomic(snapshot, packaging.relocated_config(config, rows, snapshots, snapshot))
                choice.reset_mock()
                reopened = packaging.prepared_rows(snapshot)
                choice.assert_not_called()
                self.assertEqual([key, key], [r["subtitle_style"]["font_id"] for r in reopened])

    def test_reburn_keeps_each_font_when_original_external_font_is_missing(self):
        subtitle = self.root / "subtitle.txt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:00,800\n冰雪世界\n", encoding="utf-8")
        for key, spec in subtitle_fonts.FONTS.items():
            previous = self.root / f"previous-{key}.json"
            packaging.atomic(previous, {"schema": "video-montage-packaging-delivery/v1",
                "subtitle_style": {"font": {"path": "missing-external.otf", "sha256": spec["sha256"]}},
                "results": [{"plan_id": "P1", "input": {"path": "clean.mp4", "sha256": "source"},
                             "subtitle_snapshot": {"path": str(subtitle), "sha256": packaging.sha(subtitle)}}]})
            output = self.root / key
            with patch.object(packaging, "render", return_value={}), patch.object(subtitle_fonts.secrets, "choice") as choice:
                packaging.reburn(previous, "P1", subtitle, output, output / "manifest.json")
                config = packaging.read(output / "config/reburn_config.json")
                self.assertEqual(key, config["subtitle_font"])
                style = packaging.subtitle_style(config, output)
                choice.assert_not_called()
                self.assertEqual(spec["sha256"], style["font"]["sha256"])

    def test_fallback_is_rejected_for_every_font(self):
        for spec in subtitle_fonts.FONTS.values():
            good = f"fontselect: ({spec['family']}, 400, 0) -> {spec['postscript']}, 0, {spec['postscript']}"
            subtitle_fonts.require_selected(good, spec["family"], spec["postscript"])
            for log in ("", good.replace(spec["postscript"], "ArialMT"), good + "\nGlyph 0x1F600 not found, selecting one more font"):
                with self.assertRaises(RuntimeError):
                    subtitle_fonts.require_selected(log, spec["family"], spec["postscript"])


class NewFontBurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.source = cls.root / "source.mp4"
        packaging.run([str(packaging.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=black:s=1440x2560:r=60:d=0.6", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "0.6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)])

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_actual_font_heights_and_centers_remain_consistent(self):
        heights = {}
        for key in subtitle_fonts.FONTS:
            directory = self.root / f"metrics-{key}"
            directory.mkdir()
            fonts = directory / "fonts"
            fonts.mkdir()
            style = packaging.subtitle_style({"subtitle_font": key}, ROOT)
            shutil.copyfile(style["font"]["path"], fonts / "font.otf")
            ass = directory / "main.ass"
            occurrences = packaging.write_ass(ass, [dict(start_ms=0, end_ms=100, text="无尽冬日")], style)
            header = ass.read_text(encoding="utf-8-sig").split("Dialogue:", 1)[0]
            boxes, band_y, _ = packaging.flower_effects.measure_layout(packaging.FFMPEG, directory, header, occurrences, style, 1440, 2560, fonts)
            x0, y0, x1, y1 = boxes[0]
            heights[key] = y1 - y0
            self.assertLessEqual(abs((y0 + y1) / 2 + band_y - 1768), 5)
            self.assertLessEqual(abs((x0 + x1) / 2 - 720), 5)
        for key in ("smiley", "fangtang"):
            self.assertLessEqual(abs(heights[key] / heights["w8"] - 1), .08)

    def test_new_fonts_burn_all_three_effects_with_clear_gaps_and_arbitrary_text(self):
        for key in ("smiley", "fangtang"):
            for effect in ("fire1", "ice1", "ice2"):
                with self.subTest(font=key, effect=effect):
                    directory = self.root / f"{key}-{effect}"
                    directory.mkdir()
                    fonts = directory / "fonts"
                    fonts.mkdir()
                    shutil.copyfile(subtitle_fonts.FONTS[key]["path"], fonts / "font.otf")
                    subtitles = directory / "subtitle.txt"
                    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,250\n玩无尽冬日啊\n\n"
                                         "2\n00:00:00,300 --> 00:00:00,600\n冰雪世界ABCxyz\n", encoding="utf-8")
                    config = directory / "config.json"
                    packaging.atomic(config, {"schema": packaging.SCHEMA, "subtitle_font": key,
                        "subtitle_flower": effect, "subtitle_flower_texts": ["无尽冬日", "冰雪世界ABCxyz"],
                        "outputs": [{"plan_id": "P1", "input_path": str(self.source), "subtitle_txt": str(subtitles),
                                     "subtitle_flower_seed": 4}]})
                    row = packaging.prepared_rows(config)[0]
                    style = row["subtitle_style"]
                    ass = directory / "main.ass"
                    occurrences = packaging.write_ass(ass, row["cues"], style)
                    track = packaging.flower_effects.prepare_track(packaging.FFMPEG, directory, ass, occurrences, style, fonts, 600)
                    (directory / "ordinary.ass").write_text(ass.read_text(encoding="utf-8-sig").replace("&H00000000", "&H00FFFFFF"), encoding="utf-8-sig")
                    packaging.run([str(packaging.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=black:s=1440x2560:d=0.04", "-vf", "ass=ordinary.ass:fontsdir=fonts",
                        "-frames:v", "1", "ordinary.png"], cwd=directory)
                    ordinary = cv2.imread(str(directory / "ordinary.png")).max(axis=2) > 16
                    with Image.open(directory / "flower-cue-0.png") as image:
                        alpha = np.asarray(image)[:, :, 3] > 16
                    flower = np.zeros(ordinary.shape, bool)
                    flower[track["y"]:track["y"] + alpha.shape[0]] = alpha
                    distance = cv2.distanceTransform((~ordinary).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
                    self.assertGreaterEqual(float(distance[flower].min()), 8)
                    result = packaging.render_one(row, directory / "video")
                    self.assertEqual(30, result["output_frames"])
                    self.assertEqual(["无尽冬日", "冰雪世界ABCxyz"], [c["text"] for c in result["flower_choices"]])
                    frame = directory / "decoded.png"
                    packaging.run([str(packaging.FFMPEG), "-v", "error", "-y", "-i", result["output_path"], "-frames:v", "1", str(frame)])
                    pixels = cv2.imread(str(frame))
                    x, y, w, h = result["flower_choices"][0]["bounds"]
                    self.assertGreater(int(pixels[y:y+h, x:x+w].max()), 180)
                    sprite, bounds = packaging.flower_effects.dynamic_flowers.render_text("ABCxyz冰雪世界", style["emphasis"]["effects"][effect], style["font"]["path"])
                    self.assertEqual((0, 255), sprite.getchannel("A").getextrema())
                    self.assertGreaterEqual(bounds[0], 18)
                    self.assertGreaterEqual(bounds[1], 18)
                    self.assertGreaterEqual(sprite.width - bounds[2], 17)
                    self.assertGreaterEqual(sprite.height - bounds[3], 17)


if __name__ == "__main__":
    unittest.main()
