"""Behavior, provenance and real transparent burn-in of Codex designs."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
import package_video as pack
import packaging_design as design
import design_renderer as renderer
import subtitle_fonts


def fixture(cues, digest="subtitle", font="smiley"):
    return {"subtitle_sha256": digest, "font": font, "game_names": ["无尽冬日"],
            "reason": "黄字承担主叙述，白字解释，冰字突出游戏名；开头强调后保留阅读段。",
            "cues": [{"index": i, "text": row["text"], "color": "yellow",
                      "entrance": {"effect": "none"}} for i, row in enumerate(cues, 1)]}


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cues = [{"start_ms": 0, "end_ms": 1000, "text": "无尽冬日无尽冬日"},
                     {"start_ms": 1100, "end_ms": 2000, "text": "快来上线"}]

    def prepare(self, value=None, layers=None, cues=None):
        return design.prepare(value or fixture(cues or self.cues), cues or self.cues, "subtitle", layers or [], self.root, 120)

    def test_all_game_occurrences_receive_whole_flower_spans(self):
        value = self.prepare()
        self.assertEqual([(0, 4), (4, 8)], [(s["start"], s["end"]) for s in value["cues"][0]["spans"]])
        self.assertEqual(["ice2", "ice2"], [s["flower"] for s in value["cues"][0]["spans"]])
        self.assertEqual([9, 9], [s["font_size"] for s in value["cues"][0]["spans"]])
        self.assertEqual(8, value["cues"][1]["font_size"])
        broken = [{"start_ms": 0, "end_ms": 500, "text": "无尽"}, {"start_ms": 500, "end_ms": 1000, "text": "冬日"}]
        with self.assertRaisesRegex(ValueError, "split"):
            self.prepare(cues=broken)

    def test_only_editor_sizes_8_9_10_and_complete_game_size_are_allowed(self):
        for invalid in (7, 11, 8.5, True, "9"):
            value = fixture(self.cues); value["cues"][1]["font_size"] = invalid
            with self.assertRaisesRegex(ValueError, "8, 9 or 10"):
                self.prepare(value)
            value = fixture(self.cues); value["cues"][1]["spans"] = [{"start": 0, "end": 2, "font_size": invalid}]
            with self.assertRaises(ValueError):
                self.prepare(value)
        value = fixture(self.cues); value["cues"][0]["layout"] = {"size": 1}
        with self.assertRaisesRegex(ValueError, "removed"):
            self.prepare(value)
        value = fixture(self.cues); value["cues"][0]["spans"] = [{"start": 0, "end": 4, "flower": "ice2", "font_size": 8}]
        with self.assertRaisesRegex(ValueError, "9 or 10"):
            self.prepare(value)
        value = fixture(self.cues); value["game_font_size"] = 10
        self.assertEqual([10, 10], [s["font_size"] for s in self.prepare(value)["cues"][0]["spans"]])

    def test_outline_covers_every_glyph_edge_in_three_fonts(self):
        # Independent 3px neighborhood must be opaque around even W8's sharp corners.
        for key in subtitle_fonts.FONTS:
            for color in ("yellow", "white"):
                image = np.asarray(renderer.text_sprite("实冰感朋友", subtitle_fonts.FONTS[key]["path"], subtitle_fonts.pixels(key, 8), color))
                face = (image[:, :, :3].max(axis=2) > 200) & (image[:, :, 3] > 250)
                neighborhood = cv2.dilate(face.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
                self.assertTrue((image[:, :, 3][neighborhood] == 255).all(), key)

    def test_editor_40_outline_preserves_fractional_calibration(self):
        self.assertEqual(40, subtitle_fonts.EDITOR_OUTLINE_WIDTH)
        self.assertAlmostEqual(12.3, subtitle_fonts.OUTLINE_PIXELS)
        self.assertAlmostEqual(pack.SUBTITLE_ASS["outline"] * 1440 / 1920, subtitle_fonts.OUTLINE_PIXELS)
        mask = Image.new("L", (81, 81)); ImageDraw.Draw(mask).rectangle((30, 30, 50, 50), fill=255)
        alpha = renderer.outlined_mask(mask, subtitle_fonts.OUTLINE_PIXELS)
        self.assertEqual(255, alpha.getpixel((62, 40)))
        self.assertIn(alpha.getpixel((63, 40)), (76, 77))
        self.assertEqual(0, alpha.getpixel((64, 40)))

    def test_flower_outline_bypasses_ordinary_editor_stroke(self):
        for key in subtitle_fonts.FONTS:
            for flower in design.FLOWERS:
                with patch.object(renderer, "outlined_mask", side_effect=AssertionError("flower stroke modified")):
                    image = renderer.text_sprite("无尽冬日", subtitle_fonts.FONTS[key]["path"], subtitle_fonts.pixels(key, 9),
                                                 spans=[{"start": 0, "end": 4, "flower": flower}])
                self.assertIsNotNone(image.getbbox())

    def test_all_three_fixed_sizes_preserve_font_and_span_metrics(self):
        for key in subtitle_fonts.FONTS:
            heights = []
            for size in (8, 9, 10):
                cue = {"start_ms": 0, "end_ms": 1000, "text": "真带劲"}
                value = fixture([cue], font=key); value["game_names"] = []
                value["cues"][0]["font_size"] = size
                prepared = self.prepare(value, cues=[cue])
                track = renderer.prepare_track(self.root, [cue], prepared, subtitle_fonts.FONTS[key]["path"], 60)
                event = track["record"]["events"][0]
                self.assertEqual(size, event["editor_font_size"])
                self.assertEqual(subtitle_fonts.pixels(key, size), event["font_size"])
                self.assertEqual({"color": "#000000", "editor_width": 40, "radius_pixels": 12.3,
                                  "flower_outline": "existing_effect"}, track["record"]["ordinary_outline"])
                heights.append(event["settled_size"][1])
            self.assertTrue(heights[0] < heights[1] < heights[2])

    def test_long_sentence_wraps_without_changing_selected_size(self):
        cue = {"start_ms": 0, "end_ms": 2000, "text": "我也是刷着广告才入坑的"}
        prepared = self.prepare(cues=[cue], value=fixture([cue], font="w8"))
        track = renderer.prepare_track(self.root, [cue], prepared, subtitle_fonts.FONTS["w8"]["path"], 120)
        event = track["record"]["events"][0]
        self.assertEqual(8, event["editor_font_size"])
        self.assertEqual(153, event["font_size"])
        self.assertGreater(event["settled_size"][1], 200)
        with self.assertRaisesRegex(ValueError, "font_size"):
            renderer.fit_sprite(Image.new("RGBA", (2000, 100)), {"x": .5, "y": .69, "max_width": .9})
        value = fixture([cue], font="w8"); value["cues"][0]["line_breaks"] = [7]
        track = renderer.prepare_track(self.root, [cue], self.prepare(value, cues=[cue]), subtitle_fonts.FONTS["w8"]["path"], 120)
        self.assertEqual([7], track["record"]["cues"][0]["line_breaks"])
        value = fixture(self.cues); value["cues"][0]["line_breaks"] = [2]
        with self.assertRaisesRegex(ValueError, "complete"):
            self.prepare(value)

    def test_stale_binding_incomplete_cues_and_long_entrance_are_rejected(self):
        cases = [fixture(self.cues, "stale"), fixture(self.cues), fixture(self.cues)]
        cases[1]["cues"].pop()
        cases[2]["cues"][0]["entrance"] = {"effect": "bounce_up", "duration_ms": 600}
        for value in cases:
            with self.assertRaises(ValueError):
                self.prepare(value)
        for color in ("red", "#FFDE00"):
            value = fixture(self.cues); value["cues"][0]["color"] = color
            with self.assertRaises(ValueError):
                self.prepare(value)



    def test_removed_effects_and_decorative_cards_are_rejected(self):
        for template in ("comic_burst", "repeat_burst", "flame_emotion", "double_title", "info_card", "custom"):
            with self.assertRaisesRegex(ValueError, "removed"):
                self.prepare(layers=[{"template": template}])
        value = fixture(self.cues); value["cues"][0]["entrance"] = {"effect": "zoom_settle", "duration_ms": 250}
        with self.assertRaises(ValueError):
            self.prepare(value)
        for flower in ("comic_orange", "flame_gold"):
            value = fixture(self.cues); value["game_flower"] = flower
            with self.assertRaises(ValueError):
                self.prepare(value)

    def test_task_created_effect_definitions_and_animation_overrides_are_rejected(self):
        for key in ("custom_effects", "effect_definitions", "temporary_resources"):
            value = fixture(self.cues); value[key] = [{"path": "new-effect.png"}]
            with self.assertRaisesRegex(ValueError, "existing library"):
                self.prepare(value)
        for key in ("path", "frames", "shader", "amplitude", "parameters"):
            value = fixture(self.cues)
            value["cues"][0]["entrance"] = {"effect": "bounce_up", "duration_ms": 250, key: "custom"}
            with self.assertRaisesRegex(ValueError, "custom"):
                self.prepare(value)

    def test_refresh_preserves_decisions_and_requires_review_for_changed_words(self):
        value = fixture(self.cues)
        timing_edit = copy.deepcopy(self.cues); timing_edit[0]["end_ms"] = 900
        refreshed = design.refresh(value, timing_edit, "new")
        self.assertEqual(value["cues"], refreshed["cues"])
        self.assertNotIn("needs_design_review", refreshed)
        wording_edit = copy.deepcopy(self.cues); wording_edit[1]["text"] = "立即上线"
        refreshed = design.refresh(value, wording_edit, "new")
        self.assertTrue(refreshed["needs_design_review"])
        with self.assertRaisesRegex(ValueError, "revise"):
            design.prepare(refreshed, wording_edit, "new", [], self.root, 120)

    def test_dense_evidence_includes_accelerated_motion_settle_and_overlaps(self):
        record = {"events": [{"start_frame": 12, "end_frame_exclusive": 100, "entrance_frames": 18},
                             {"start_frame": 50, "end_frame_exclusive": 110, "entrance_frames": 0}]}
        frames = design.evidence_frames(record, 1.2, 100)
        self.assertTrue(set(range(9, 27)).issubset(frames))
        self.assertIn(round(75 / 1.2), frames)
        self.assertFalse(design.review_ok(record, {"design_pass": True}))
        self.assertTrue(design.review_ok(record, {"design_pass": True, "design_reason": "重点清楚，叙述留有稳定阅读段。"}))

    def test_three_fonts_and_flower_styles_render_changed_text_and_both_colors(self):
        for font in subtitle_fonts.FONTS:
            path = subtitle_fonts.FONTS[font]["path"]
            for flower in design.FLOWERS:
                image = renderer.text_sprite("来无尽冬日吧", path, 130, spans=[
                    {"start": 1, "end": 5, "flower": flower}, {"start": 5, "end": 6, "color": "white"}])
                pixels = np.asarray(image)
                self.assertTrue((pixels[:, :, 3] == 0).any())
                self.assertTrue((pixels[:, :, :3].min(axis=2) > 240).any())
                self.assertTrue(((pixels[:, :, 0] > 230) & (pixels[:, :, 1] > 190) & (pixels[:, :, 2] < 40)).any())
                other = renderer.text_sprite("冰雪世界", path, 130, spans=[{"start": 0, "end": 4, "flower": flower}])
                self.assertNotEqual(image.tobytes(), other.tobytes())

    def test_motion_is_transparent_deterministic_and_protected_regions_are_checked(self):
        value = fixture(self.cues)
        value["cues"][0]["entrance"] = {"effect": "bounce_up", "duration_ms": 250}
        value = self.prepare(value)
        track = renderer.prepare_track(self.root, self.cues, value, subtitle_fonts.FONTS["smiley"]["path"], 120)
        self.assertEqual(15, track["record"]["events"][0]["entrance_frames"])
        images = sorted(self.root.glob("design-*.png"))
        with Image.open(images[-1]) as image:
            self.assertEqual("RGBA", image.mode)
            self.assertEqual(0, image.getchannel("A").getextrema()[0])
        value["protected_regions"] = [{"box": [.1, .5, .9, .9]}]
        with self.assertRaisesRegex(ValueError, "protected"):
            renderer.prepare_track(self.root, self.cues, value, subtitle_fonts.FONTS["smiley"]["path"], 120)

    def test_short_cues_and_long_text(self):
        cue = {"start_ms": 0, "end_ms": 100, "text": "夯"}
        value = fixture([cue]); value["game_names"] = []
        value["cues"][0]["entrance"] = {"effect": "fade", "duration_ms": 50}
        prepared = design.prepare(value, [cue], "subtitle", [], self.root, 6)
        track = renderer.prepare_track(self.root, [cue], prepared, subtitle_fonts.FONTS["smiley"]["path"], 6)
        self.assertEqual(3, track["record"]["events"][0]["entrance_frames"])
        long_text = "冰雪世界轻松解压赶快上线" * 3
        cue = {"start_ms": 0, "end_ms": 1500, "text": long_text}
        value = fixture([cue], font="w8"); value["game_names"] = []
        value["cues"][0].update(spans=[{"start": 0, "end": len(long_text), "flower": "ice2"}],
                                entrance={"effect": "shout_wave", "duration_ms": 300})
        prepared = design.prepare(value, [cue], "subtitle", [], self.root, 90)
        with self.assertRaisesRegex(ValueError, "fit|split"):
            renderer.prepare_track(self.root, [cue], prepared, subtitle_fonts.FONTS["w8"]["path"], 90)



class RealBurnTests(unittest.TestCase):
    def test_per_video_fonts_transparent_track_and_reburn(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "clean.mp4"
            pack.run([str(pack.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "color=c=0x406090:s=1440x2560:r=60:d=2",
                      "-f", "lavfi", "-i", "sine=frequency=400:sample_rate=48000:duration=2", "-c:v", "libx264",
                      "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
            subtitle = root / "sub.txt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n无尽冬日\n\n2\n00:00:01,100 --> 00:00:02,000\n快来上线\n", encoding="utf-8")
            cues = pack.parse_srt(subtitle, 2000)
            config = root / "config.json"
            outputs = []
            for i, font in enumerate(("smiley", "fangtang")):
                presentation = fixture(cues, pack.sha(subtitle), font)
                presentation["cues"][0]["entrance"] = {"effect": "bounce_up", "duration_ms": 200}
                presentation["cues"][1]["color"] = "white"
                outputs.append({"plan_id": f"P{i}", "input_path": str(source), "subtitle_txt": str(subtitle),
                                "subtitle_design": presentation, "graphic_layers": []})
            pack.atomic(config, {"schema": pack.SCHEMA, "outputs": outputs})
            output = root / "delivery"; manifest = output / "manifest.json"
            result = pack.render(config, output, manifest)
            self.assertEqual(["smiley", "fangtang"], [r["subtitle_style"]["font_id"] for r in result["results"]])
            for row in result["results"]:
                self.assertEqual(100, row["output_frames"])
                self.assertEqual(0, row["output_spec"]["subtitle_streams"])
            retained = pack.read(Path(result["config_snapshot_path"]))
            self.assertEqual([], retained["outputs"][0]["graphic_layers"])
            self.assertEqual("technical_pass_pending_review", pack.validate(manifest, output / "check.json")["decision"])
            before = [row["output_sha256"] for row in result["results"]]
            reburned = pack.reburn(manifest, "P0", subtitle, output, manifest)
            self.assertEqual(before, [row["output_sha256"] for row in reburned["results"]])
            raw = subprocess.check_output([str(pack.FFMPEG), "-v", "error", "-ss", "0.3", "-i", reburned["results"][0]["output_path"],
                            "-frames:v", "1", "-vf", "crop=20:20:0:1760,format=rgb24", "-f", "rawvideo", "pipe:1"])
            # A pixel inside the track band but outside glyphs retains the scene color.
            pixels = np.frombuffer(raw, np.uint8).reshape(20, 20, 3)
            self.assertLess(float(np.abs(pixels.astype(float) - [64, 96, 144]).max()), 8)
            raw = subprocess.check_output([str(pack.FFMPEG), "-v", "error", "-i", reburned["results"][0]["output_path"],
                            "-vf", r"select=eq(n\,42)+eq(n\,53)+eq(n\,60),crop=1440:400:0:1500,format=rgb24",
                            "-fps_mode", "vfr", "-f", "rawvideo", "pipe:1"])
            samples = np.frombuffer(raw, np.uint8).reshape(3, 400, 1440, 3)
            ink = (np.abs(samples.astype(float) - [64, 96, 144]) > 30).any(axis=3).sum(axis=(1, 2))
            # At final speed: first cue visible at .70s, gap at .883s, second at 1s.
            self.assertGreater(ink[0], 1000)
            self.assertEqual(0, ink[1])
            self.assertGreater(ink[2], 1000)


if __name__ == "__main__":
    unittest.main()
