from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package_video.py"
SPEC = importlib.util.spec_from_file_location("package_video_test", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(packaging)
REFERENCE_FONT_SHA256 = packaging.SUBTITLE_FONT_SHA256


class PackagingContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.font = self.root / "font.otf"
        self.font.write_bytes(b"font fixture")
        for name, value in (("DEFAULT_SUBTITLE_FONT_PATH", self.font),
                            ("SUBTITLE_FONT_SHA256", packaging.sha(self.font))):
            mocked = patch.object(packaging, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        font_mock = patch.dict(packaging.subtitle_fonts.FONTS, {"w8": {
            **packaging.subtitle_fonts.FONTS["w8"], "path": str(self.font), "sha256": packaging.sha(self.font)}})
        font_mock.start()
        self.addCleanup(font_mock.stop)
        choice_mock = patch.object(packaging.subtitle_fonts.secrets, "choice", return_value="w8")
        choice_mock.start()
        self.addCleanup(choice_mock.stop)
        self.video = self.root / "clean.mp4"
        self.video.write_bytes(b"clean video")
        self.srt = self.root / "subtitle-P1.txt"
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n已修订字幕\n", encoding="utf-8")
        self.pin = self.root / "pin.png"
        image = Image.new("RGBA", (9, 16), (0, 0, 0, 0))
        image.putpixel((4, 4), (255, 255, 255, 255))
        image.save(self.pin)
        self.config = self.root / "packaging.json"
        self.row = {"plan_id": "P1", "input_path": self.video.name, "input_sha256": packaging.sha(self.video),
                    "subtitle_txt": self.srt.name, "subtitle_sha256": packaging.sha(self.srt),
                    "text_pins": [{"path": self.pin.name, "sha256": packaging.sha(self.pin),
                                   "start_frame": 5, "end_frame_exclusive": 20}]}
        self.spec = {"frames": 60, "duration": 1.0, "width": 1440, "height": 2560,
                     "video_codec": "h264", "audio_codec": "aac", "video_streams": 1,
                     "audio_streams": 1, "subtitle_streams": 0}
        self.write_config()

    def write_config(self):
        try:
            cues = packaging.parse_srt(self.srt, 1000)
        except ValueError:
            cues = []
        self.row["subtitle_design"] = {"subtitle_sha256": packaging.sha(self.srt), "font": "w8", "game_names": [],
            "game_flower": "ice2", "reason": "Test reviewed explicit layout",
            "cues": [{"index": i, "text": c["text"], "color": "yellow", "entrance": {"effect": "none"}}
                     for i, c in enumerate(cues, 1)]}
        self.config.write_text(json.dumps({"schema": packaging.SCHEMA, "outputs": [self.row]}, ensure_ascii=False), encoding="utf-8")

    def prepare(self):
        with patch.object(packaging, "video_spec", return_value=self.spec):
            return packaging.prepared_rows(self.config)

    def test_in_place_reburn_keeps_clean_inputs_and_updates_the_existing_manifest(self):
        first = self.root / "自动化混剪_20260930_153000_123456"
        manifest = packaging.delivery_category(first, "manifests") / "packaging_manifest.json"

        def encode(row, root):
            target = packaging.delivery_category(root, "video") / f"{row['plan_id']}.mp4"
            target.write_bytes(b"packaged video")
            return {"plan_id": row["plan_id"], "input": row["input"],
                    "input_frames": 60, "output_path": str(target), "output_sha256": packaging.sha(target),
                    "subtitles": row["subtitles"], "text_pins": row["text_pins"]}

        with patch.object(packaging, "video_spec", return_value=self.spec), \
             patch.object(packaging, "render_one", side_effect=encode):
            result = packaging.render(self.config, first, manifest)
            self.assertEqual({"成片", "混剪（无包装）", "字幕", "临时文件"},
                             {p.name for p in first.iterdir()})
            self.assertEqual({"config", "manifests"}, {p.name for p in (first / "临时文件").iterdir()})
            self.assertEqual(first / "临时文件" / "manifests", manifest.parent)
            self.assertEqual(self.video.read_bytes(), (first / "混剪（无包装）" / "P1.mp4").read_bytes())
            self.assertEqual(str(first / "混剪（无包装）" / "P1.mp4"), result["results"][0]["input"]["path"])
            marker = first / "字幕" / "修改字幕后让AI重新烧录"
            self.assertTrue(marker.is_file())
            marker.write_text("用户保留的说明", encoding="utf-8")
            packaging.ensure_delivery_layout(first)
            self.assertEqual("用户保留的说明", marker.read_text(encoding="utf-8"))
            edited = first / "字幕" / "subtitle-P1.txt"
            edited.write_text("1\n00:00:00,000 --> 00:00:00,900\n已修订字幕\n", encoding="utf-8")
            self.video.unlink()  # Reburn must use the delivered clean copy.
            previous_hash = packaging.sha(manifest)
            second = first
            new_manifest = manifest
            updated = packaging.reburn(manifest, "P1", edited, second, new_manifest)
            self.assertEqual(edited.read_bytes(), (second / "字幕" / edited.name).read_bytes())
            self.assertTrue((second / "字幕" / marker.name).is_file())
            reopened = packaging.prepared_rows(second / "临时文件" / "config" / "reburn_config.json")
            self.assertEqual(str(second / "混剪（无包装）" / "P1.mp4"), reopened[0]["input"]["path"])
            self.assertEqual(packaging.sha(edited), updated["results"][0]["subtitle_snapshot"]["sha256"])
            self.assertEqual(b"packaged video", (first / "成片" / "P1.mp4").read_bytes())
            self.assertTrue(updated["reburn_source"]["overwritten_in_place"])
            self.assertEqual(previous_hash, updated["reburn_source"]["manifest_sha256"])
            self.assertNotEqual(previous_hash, packaging.sha(manifest))
            self.assertEqual([first], list(self.root.glob("自动化混剪_*")))

    def test_missing_asset_and_wrong_hash_rejected(self):
        self.pin.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.pin.write_bytes(b"changed image")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.prepare()

    def test_case_insensitive_plan_ids_cannot_share_output_files(self):
        packaging.atomic(self.config, {"schema": packaging.SCHEMA,
            "outputs": [self.row, {**self.row, "plan_id": "p1"}]})
        with self.assertRaisesRegex(ValueError, "duplicate plan ID"):
            self.prepare()

    def test_second_encoder_failure_preserves_entire_previous_batch(self):
        output = self.root / "自动化混剪_20261008_120000"
        packaging.ensure_delivery_layout(output)
        manifest = output / "临时文件" / "manifests" / "packaging_manifest.json"
        manifest.write_bytes(b"previous manifest")
        packaging.atomic(self.config, {"schema": packaging.SCHEMA,
            "outputs": [self.row, {**self.row, "plan_id": "P2"}]})
        for pid in ("P1", "P2"):
            (output / "成片" / f"{pid}.mp4").write_bytes(f"previous {pid}".encode())
        def encode(row, directory):
            if row["plan_id"] == "P2":
                raise RuntimeError("second encoder failed")
            target = directory / "P1.mp4"
            target.write_bytes(b"replacement")
            return {"plan_id": "P1", "output_path": str(target)}
        with patch.object(packaging, "video_spec", return_value=self.spec), \
             patch.object(packaging, "render_one", side_effect=encode):
            with self.assertRaisesRegex(RuntimeError, "second encoder failed"):
                packaging.render(self.config, output, manifest, overwrite=True)
        self.assertEqual(b"previous manifest", manifest.read_bytes())
        for pid in ("P1", "P2"):
            self.assertEqual(f"previous {pid}".encode(), (output / "成片" / f"{pid}.mp4").read_bytes())

    def test_delivery_rejects_records_outside_temporary_subdirectory(self):
        output = self.root / "work" / "自动化混剪_20260930_153000_123456"
        inside = output / "成片" / "validation.json"
        with self.assertRaisesRegex(ValueError, "records inside the delivery must be in"):
            packaging.render(self.config, output, inside)
        self.assertFalse(output.exists())
        manifest = packaging.delivery_category(output, "manifests") / "packaging_manifest.json"
        packaging.atomic(manifest, {"output_count": 1, "results": [{"plan_id": "P1",
            "output_path": str(output / "成片" / "P1.mp4"),
            "subtitles": {"path": str(self.srt), "sha256": packaging.sha(self.srt), "cue_count": 1}}]})
        with self.assertRaisesRegex(ValueError, "reports inside the delivery must be in"):
            packaging.validate(manifest, inside)
        self.assertFalse(inside.exists())

        report = packaging.delivery_category(output, "reports") / "validation.json"
        # A missing-media failure should still write the report in the job's temp directory.
        result = packaging.validate(manifest, report)
        self.assertTrue(report.is_file())
        self.assertEqual("reject", result["decision"])

    def test_out_of_range_and_missing_text_pin_interval_rejected(self):
        self.row["text_pins"][0]["end_frame_exclusive"] = 61
        self.write_config()
        with self.assertRaisesRegex(ValueError, "invalid output-frame interval"):
            self.prepare()
        del self.row["text_pins"][0]["start_frame"]
        self.write_config()
        with self.assertRaisesRegex(ValueError, "text pin interval"):
            self.prepare()

    def test_editing_subtitle_requires_updated_hash(self):
        self.prepare()
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n再次修订\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.prepare()
        self.row["subtitle_sha256"] = packaging.sha(self.srt)
        self.write_config()
        self.assertEqual("再次修订", self.prepare()[0]["cues"][0]["text"])

    def test_duplicate_plan_and_partial_delivery_rejected(self):
        value = {"schema": packaging.SCHEMA, "outputs": [self.row, dict(self.row)]}
        self.config.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate plan ID"):
            self.prepare()
        self.write_config()
        with patch.object(packaging, "video_spec", return_value=self.spec):
            with self.assertRaisesRegex(ValueError, "complete delivery"):
                packaging.prepared_rows(self.config, {"P1": packaging.sha(self.video), "P2": "anything"})

    def test_delivery_manifest_alone_cannot_claim_complete_montage(self):
        delivery = self.root / "delivery.json"
        delivery.write_text(json.dumps({"schema": "ffmpeg-controller-delivery/v260928", "results": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "both clean delivery and controller validation"):
            packaging.render(self.config, self.root / "out", self.root / "manifest.json", delivery)

    def test_existing_output_is_not_overwritten(self):
        row = self.prepare()[0]
        output = self.root / "out"
        output.mkdir()
        (output / "P1.mp4").write_bytes(b"keep")
        with self.assertRaisesRegex(FileExistsError, "refusing overwrite"):
            packaging.render_one(row, output)
        self.assertEqual(b"keep", (output / "P1.mp4").read_bytes())

    def test_packaged_streams_exclude_soft_subtitles(self):
        self.assertTrue(packaging.packaged_streams_ok(self.spec))
        with_subtitle_track = {**self.spec, "subtitle_streams": 1}
        self.assertFalse(packaging.packaged_streams_ok(with_subtitle_track))

    def test_w8_subtitle_style_and_single_line_ass(self):
        style = self.prepare()[0]["subtitle_style"]
        self.assertEqual({"path": str(self.font), "sha256": packaging.sha(self.font)}, style["font"])
        self.assertEqual(8, style["capcut_reference"]["font_size"])
        self.assertEqual(164, style["capcut_reference"]["scale_percent"])
        self.assertEqual(-1300, style["capcut_reference"]["y"])
        self.assertEqual(2357, style["ass"]["position_y"])
        self.assertEqual("无尽冬日", style["emphasis"]["text"])
        self.assertEqual(9, style["emphasis"]["capcut_font_size"])
        self.assertEqual(230, style["emphasis"]["ass_font_size"])
        self.assertEqual("dynamic_font_shader_v2", style["emphasis"]["effect"])
        self.assertEqual("random_ice", style["emphasis"]["selection"])
        self.assertEqual({"fire1", "ice1", "ice2"}, set(style["emphasis"]["effects"]))
        ass = self.root / "sample.ass"
        packaging.write_ass(ass, [{"start_ms": 0, "end_ms": 900, "text": "是兄弟就来"}], style)
        content = ass.read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1920\nPlayResY: 3414", content)
        self.assertIn("WenYue XinQingNianTi J W8,204,&H0000DEFF", content)
        self.assertIn(",1,16.4,0,5,0,0,0,1", content)
        self.assertIn(r"{\pos(960,2357)}是兄弟就来", content)
        with self.assertRaisesRegex(ValueError, "one line"):
            packaging.write_ass(ass, [{"start_ms": 0, "end_ms": 900, "text": "第一行\n第二行"}], style)

    def test_every_game_title_occurrence_uses_size_9(self):
        style = self.prepare()[0]["subtitle_style"]
        ass = self.root / "emphasis.ass"
        occurrences = packaging.write_ass(ass, [
            {"start_ms": 0, "end_ms": 900, "text": "玩无尽冬日，再玩无尽冬日"},
            {"start_ms": 900, "end_ms": 1350, "text": "普通字幕"},
            {"start_ms": 1350, "end_ms": 1800, "text": "无尽冬日"},
        ], style)
        content = ass.read_text(encoding="utf-8-sig")
        base_lines = [line for line in content.splitlines() if line.startswith("Dialogue: 0,")]
        self.assertEqual(3, len(base_lines))
        enlarged = r"{\fs230\alpha&HFF&}无尽冬日{\fs204\alpha&H00&}"
        self.assertEqual(3, "\n".join(base_lines).count(enlarged))
        gap = r"{\fs164\fscx280}\h{\fs204\fscx100}"
        self.assertIn(f"玩{gap}{enlarged}{gap}，再玩{gap}{enlarged}", base_lines[0])
        self.assertIn("普通字幕", base_lines[1])
        self.assertIn(enlarged, base_lines[2])
        self.assertEqual(3, content.count("Dialogue: "))
        self.assertNotIn(r"\clip(", content)
        self.assertEqual(3, len(occurrences))
        self.assertEqual({"ice1", "ice2"}, {item["style_id"] for item in occurrences})
        self.assertEqual([0, 1, 0], [item["occurrence_index"] for item in occurrences])
        self.assertEqual([0, 0, 2], [item["cue_index"] for item in occurrences])

    def test_real_flower_outlines_clear_adjacent_subtitles(self):
        import cv2
        import numpy as np
        font_dir = self.root / "fonts"
        font_dir.mkdir()
        (font_dir / "font.otf").write_bytes((packaging.ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf").read_bytes())
        style = self.prepare()[0]["subtitle_style"]
        style["font"]["path"] = str(font_dir / "font.otf")
        for mode in ("ice1", "ice2", "fire1"):
            style["emphasis"]["selection"] = mode
            for text in ["玩无尽冬日啊", "才入坑的无尽冬日"]:
                ass = self.root / "full.ass"
                occurrences = packaging.write_ass(ass, [dict(start_ms=0, end_ms=100, text=text)], style)
                track = packaging.flower_effects.prepare_track(packaging.FFMPEG, self.root, ass, occurrences, style,
                                                               font_dir, 100)
                # White outline exposes the normally black stroke against the black test canvas.
                (self.root / "ordinary.ass").write_text(ass.read_text(encoding="utf-8-sig").replace("&H00000000", "&H00FFFFFF"), encoding="utf-8-sig")
                subprocess.run([str(packaging.FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=black:s=1440x2560:d=0.04", "-vf", "ass=ordinary.ass:fontsdir=fonts", "-frames:v", "1", "ordinary.png"],
                    cwd=self.root, check=True, capture_output=True)
                ordinary = np.max(cv2.imread(str(self.root / "ordinary.png")), axis=2) > 16
                flower = np.zeros(ordinary.shape, bool)
                alpha = np.asarray(Image.open(self.root / "flower-cue-0.png"))[:, :, 3] > 16
                flower[track["y"]:track["y"] + alpha.shape[0]] = alpha
                self.assertTrue(ordinary.any() and flower.any())
                self.assertFalse(np.any(ordinary & flower))
                distance = cv2.distanceTransform((~ordinary).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
                self.assertGreaterEqual(float(distance[flower].min()), 8, (mode, text))

    def test_random_selection_is_reproducible_and_excludes_fire(self):
        flower = packaging.flower_effects
        choices = flower.choose("random_ice", 100, 42)
        self.assertEqual({"ice1", "ice2"}, set(choices))
        self.assertEqual(choices, flower.choose("random_ice", 100, 42))
        self.assertNotEqual(choices, flower.choose("random_ice", 100, 43))
        for mode in ("ice1", "ice2", "fire1"):
            self.assertEqual([mode] * 3, flower.choose(mode, 3, 42))
        self.assertEqual({"pixel_format": "yuv420p", "crf": 18, "overlay_format": "auto"}, flower.video_encoding(choices))
        self.assertEqual({"pixel_format": "yuv444p", "crf": 8, "overlay_format": "rgb"}, flower.video_encoding(["fire1"]))
        with self.assertRaises(ValueError):
            packaging.subtitle_style({"subtitle_flower": "old_effect"}, self.root)
        self.row["subtitle_flower_seed"] = 42
        self.write_config()
        self.assertEqual(42, self.prepare()[0]["subtitle_flower_seed"])
        self.row["subtitle_flower_seed"] = True
        self.write_config()
        with self.assertRaisesRegex(ValueError, "64-bit"):
            self.prepare()

    def test_flower_effect_hash_and_dynamic_transparency(self):
        from unittest.mock import patch
        flower = packaging.flower_effects
        assets = flower.effect_styles()
        font = str(packaging.ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf")
        for spec in assets["styles"].values():
            for text in ("无尽冬日", "冰雪世界", "新", "全新挑战ABC"):
                with patch.object(Image, "open", side_effect=AssertionError("fixed artwork must not be read")):
                    image, bounds = flower.dynamic_flowers.render_text(text, spec, font)
                self.assertEqual("RGBA", image.mode)
                self.assertEqual((0, 255), image.getchannel("A").getextrema())
                self.assertEqual(0, image.getpixel((0, 0))[3])
                self.assertGreater(bounds[2], bounds[0])
            reference, _ = flower.dynamic_flowers.render_text("无尽冬日", spec, font)
            changed, _ = flower.dynamic_flowers.render_text("冰雪世界", spec, font)
            self.assertNotEqual(reference.tobytes(), changed.tobytes())
        with patch.object(flower, "sha", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "effect changed"):
                flower.effect_styles()

    def test_custom_keywords_and_whole_line_use_actual_text(self):
        config = {"subtitle_flower_texts": ["冰雪", "冰雪世界", "全新挑战"], "subtitle_flower": "ice2"}
        style = packaging.subtitle_style(config, self.root)
        ass = self.root / "custom.ass"
        occurrences = packaging.write_ass(ass, [dict(start_ms=0, end_ms=900, text="来冰雪世界玩全新挑战")], style)
        self.assertEqual(["冰雪世界", "全新挑战"], [item["text"] for item in occurrences])
        self.assertTrue(all(item["style_id"] == "ice2" for item in occurrences))
        self.assertNotIn("无尽冬日", ass.read_text(encoding="utf-8-sig"))
        style = packaging.subtitle_style({"subtitle_flower_scope": "all", "subtitle_flower_texts": []}, self.root)
        occurrences = packaging.write_ass(ass, [dict(start_ms=0, end_ms=900, text="开启新世界！")], style)
        self.assertEqual(["开启新世界！"], [item["text"] for item in occurrences])
        for texts in ([], "冰雪世界", ["冰雪", "冰雪"], [""], ["一\n二"], [{}]):
            with self.assertRaisesRegex(ValueError, "subtitle_flower_texts"):
                packaging.subtitle_style({"subtitle_flower_texts": texts}, self.root)

    def test_missing_design_old_subtitle_alias_and_changed_font_are_rejected(self):
        self.row.pop("subtitle_design")
        packaging.atomic(self.config, {"schema": packaging.SCHEMA, "outputs": [self.row]})
        with self.assertRaisesRegex(ValueError, "requires subtitle_design"):
            self.prepare()
        self.write_config()
        self.row["subtitle_srt"] = self.row.pop("subtitle_txt")
        packaging.atomic(self.config, {"schema": packaging.SCHEMA, "outputs": [self.row]})
        with self.assertRaisesRegex(ValueError, "subtitle_srt is no longer supported"):
            self.prepare()
        self.row["subtitle_txt"] = self.row.pop("subtitle_srt")
        self.write_config()
        self.font.write_bytes(b"changed font")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare()

    def test_retired_random_font_and_flower_fields_are_rejected(self):
        for key in ("subtitle_font", "subtitle_font_path", "subtitle_flower", "subtitle_flower_scope", "subtitle_flower_texts"):
            for in_output in (False, True):
                with self.subTest(key=key, in_output=in_output):
                    value = {"schema": packaging.SCHEMA, "outputs": [dict(self.row)]}
                    (value["outputs"][0] if in_output else value)[key] = "random"
                    packaging.atomic(self.config, value)
                    with self.assertRaisesRegex(ValueError, "legacy font/flower"):
                        self.prepare()

    def test_overlong_cue_is_rejected_and_multiple_lines_are_sequential(self):
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n" + "中" * 15 + "\n", encoding="utf-8")
        self.row["subtitle_sha256"] = packaging.sha(self.srt)
        self.write_config()
        with self.assertRaisesRegex(ValueError, "too wide"):
            self.prepare()
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n第一行\n第二行\n", encoding="utf-8")
        self.row["subtitle_sha256"] = packaging.sha(self.srt)
        self.write_config()
        self.assertEqual(["第一行", "第二行"], [cue["text"] for cue in self.prepare()[0]["cues"]])
        self.assertEqual([(0, 450), (450, 900)],
                         [(cue["start_ms"], cue["end_ms"]) for cue in self.prepare()[0]["cues"]])

    def test_subtitle_lines_must_be_visible_at_60_fps(self):
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,003\nA\nB\nC\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "too short"):
            packaging.parse_srt(self.srt, 1000)
        self.srt.write_text("1\n00:00:01,000 --> 00:00:01,020\nA\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside video"):
            packaging.parse_srt(self.srt, 1000)
        lines = packaging.timed_lines(["A", "B", "C"], 3, 63)
        self.assertEqual([(3, 23), (23, 43), (43, 63)], [(start, end) for start, end, _ in lines])
        self.assertTrue(all(packaging.ass_time(start) != packaging.ass_time(end) for start, end, _ in lines))

    def test_long_draft_text_is_split_into_timed_single_lines(self):
        chunks = packaging.single_line_chunks("中" * 29)
        self.assertEqual(["中" * 14, "中" * 14, "中"], chunks)
        self.assertEqual(["中" * 12, "无尽冬日后续"], packaging.single_line_chunks("中" * 12 + "无尽冬日后续"))
        self.assertTrue(all(packaging.display_width(line) <= 28 for line in chunks))
        timed = packaging.timed_lines(chunks, 0, 900)
        self.assertEqual(0, timed[0][0])
        self.assertEqual(900, timed[-1][1])
        self.assertTrue(all(a[1] == b[0] for a, b in zip(timed, timed[1:])))

    def test_draft_preserves_english_word_spaces(self):
        self.assertEqual(["This is a test"], packaging.single_line_chunks("This   is a test"))

    def test_draft_keeps_emphasis_word_together_across_time_limit(self):
        words = [{"start": 0.0, "end": 3.4, "word": "前"},
                 {"start": 3.4, "end": 3.45, "word": "无"},
                 {"start": 3.45, "end": 3.6, "word": "尽"},
                 {"start": 3.6, "end": 3.7, "word": "冬"},
                 {"start": 3.7, "end": 3.8, "word": "日"}]
        asr = types.SimpleNamespace(
            build_model=lambda *args: (None, "cpu", None),
            transcribe=lambda *args: ({"segments": [{"words": words}]}, None))
        spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        output = self.root / "draft"
        with patch.object(packaging, "video_spec", return_value={"duration": 5.0}), \
             patch.object(packaging.importlib.util, "spec_from_file_location", return_value=spec), \
             patch.object(packaging.importlib.util, "module_from_spec", return_value=asr):
            packaging.draft_one(self.video, "P1", output)
        cues = packaging.parse_srt(output / "subtitles" / "subtitle-P1.txt", 5000)
        self.assertTrue(any("无尽冬日" in cue["text"] for cue in cues))

    def test_ffmpeg_font_fallback_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "did not select"):
            packaging.require_subtitle_font("fontselect: (WenYue XinQingNianTi J W8, 400, 0) -> ArialMT, 0, ArialMT")

    def test_real_font_burn_matches_design_geometry(self):
        real_font = packaging.ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf"
        self.assertTrue(real_font.is_file())
        self.assertEqual(REFERENCE_FONT_SHA256, packaging.sha(real_font))
        subprocess.run([str(packaging.FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=white:s=1440x2560:r=60:d=0.1", "-f", "lavfi",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-frames:v", "6",
                        "-t", "0.1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(self.video)], check=True, capture_output=True)
        self.srt.write_text("1\n00:00:00,000 --> 00:00:00,100\n是兄弟就来\n", encoding="utf-8")
        self.row.update(input_sha256=packaging.sha(self.video), subtitle_sha256=packaging.sha(self.srt), text_pins=[])
        self.write_config()
        with patch.object(packaging, "DEFAULT_SUBTITLE_FONT_PATH", real_font), \
             patch.object(packaging, "SUBTITLE_FONT_SHA256", REFERENCE_FONT_SHA256), \
             patch.dict(packaging.subtitle_fonts.FONTS, {"w8": {
                 **packaging.subtitle_fonts.FONTS["w8"], "path": str(real_font), "sha256": REFERENCE_FONT_SHA256}}):
            row = packaging.prepared_rows(self.config)[0]
            output_dir = self.root / "rendered"
            result = packaging.render_one(row, output_dir)
        self.assertEqual({"P1.mp4"}, {path.name for path in output_dir.iterdir()})
        self.assertEqual((1, 1, 0), tuple(result["output_spec"][key] for key in
                                           ("video_streams", "audio_streams", "subtitle_streams")))
        frame = self.root / "rendered.png"
        subprocess.run([str(packaging.FFMPEG), "-hide_banner", "-loglevel", "error", "-i", result["output_path"],
                        "-frames:v", "1", str(frame)], check=True, capture_output=True)
        with Image.open(frame) as image:
            pixels = image.convert("RGB").load()
            yellow = [(x, y) for y in range(1640, 1890) for x in range(400, 1040)
                      if pixels[x, y][0] > 150 and pixels[x, y][1] > 100 and pixels[x, y][2] < 80]
        self.assertTrue(yellow)
        left, top = min(x for x, _ in yellow), min(y for _, y in yellow)
        right, bottom = max(x for x, _ in yellow), max(y for _, y in yellow)
        self.assertLessEqual(abs((left + right) / 2 - 720), 5)
        self.assertLessEqual(abs((top + bottom) / 2 - 1768), 5)
        self.assertTrue(630 <= right - left + 1 <= 650)
        self.assertTrue(130 <= bottom - top + 1 <= 145)

    def test_render_keeps_subtitle_as_named_txt(self):
        output = self.root / "rendered"
        output.mkdir()
        manifest = output / "packaging_manifest.json"
        with patch.object(packaging, "video_spec", return_value=self.spec), patch.object(packaging, "render_one", return_value={"plan_id": "P1", "subtitles": {"path": str(self.srt), "sha256": packaging.sha(self.srt)}}):
            result = packaging.render(self.config, output, manifest)
            reopened = packaging.prepared_rows(output / "config" / "packaging.json")
        self.assertEqual(self.srt.read_bytes(), (output / "subtitles" / "subtitle-P1.txt").read_bytes())
        self.assertEqual(str(output / "subtitles" / "subtitle-P1.txt"), reopened[0]["subtitles"]["path"])
        self.assertEqual(packaging.sha(self.video), reopened[0]["input"]["sha256"])
        self.assertEqual(packaging.sha(self.pin), reopened[0]["text_pins"][0]["sha256"])
        self.assertEqual(str(output / "subtitles" / "subtitle-P1.txt"), result["results"][0]["subtitle_snapshot"]["path"])
        self.assertEqual(str(output / "config" / "packaging.json"), result["config_snapshot_path"])
        self.assertEqual({"config", "packaging_manifest.json", "subtitles"},
                         {path.name for path in output.iterdir()})
        self.assertFalse(list(output.glob("*.srt")))

    def test_render_moves_config_and_subtitle_into_categories(self):
        output = self.root
        manifest = output / "packaging_manifest.json"
        with patch.object(packaging, "video_spec", return_value=self.spec), patch.object(packaging, "render_one", return_value={"plan_id": "P1", "subtitles": {"path": str(self.srt), "sha256": packaging.sha(self.srt)}}):
            result = packaging.render(self.config, output, manifest)
        self.assertEqual(str(output / "config" / "packaging.json"), result["config_snapshot_path"])
        self.assertEqual(str(output / "subtitles" / "subtitle-P1.txt"), result["results"][0]["subtitle_snapshot"]["path"])
        self.assertFalse((output / "packaging_config_snapshot.json").exists())
        self.assertTrue((output / "subtitles").is_dir())

    def test_render_rejects_colliding_output_paths_before_encoding(self):
        output = self.root / "collision-output"
        with patch.object(packaging, "video_spec", return_value=self.spec), \
             patch.object(packaging, "render_one") as encode:
            with self.assertRaisesRegex(ValueError, "paths collide"):
                packaging.render(self.config, output, output / "config" / self.config.name)
            encode.assert_not_called()
            with self.assertRaisesRegex(ValueError, "paths collide"):
                packaging.render(self.config, output, output / "subtitles" / "subtitle-P1.txt")
            encode.assert_not_called()

    def test_reburn_requires_review_when_text_changes(self):
        previous = self.root / "previous.json"
        packaging.atomic(previous, {"schema": "video-montage-packaging-delivery/v1",
            "config_snapshot_path": str(self.config), "config_snapshot_sha256": packaging.sha(self.config),
            "results": [{"plan_id": "P1", "input": {"path": str(self.video), "sha256": packaging.sha(self.video)},
                         "input_frames": 60, "subtitle_snapshot": {"path": str(self.srt), "sha256": packaging.sha(self.srt)}}]})
        edited = self.root / "edited.txt"
        edited.write_text("1\n00:00:00,000 --> 00:00:00,900\n新字幕\n", encoding="utf-8")
        with patch.object(packaging, "render") as render, self.assertRaisesRegex(ValueError, "revise retained subtitle_design"):
            packaging.reburn(previous, "P1", edited, self.root / "new", self.root / "new/manifest.json")
        render.assert_not_called()

    def test_failed_overwrite_encoding_preserves_existing_video(self):
        row = self.prepare()[0]
        row["_overwrite"] = True
        output = self.root / "work" / "自动化混剪_20260930_153000_123456"
        packaging.ensure_delivery_layout(output)
        target = output / "成片" / "P1.mp4"; target.write_bytes(b"previous complete video")
        with patch.object(packaging.design_renderer, "prepare_track", return_value={"path": "track.ffconcat", "y": 1500, "record": {}}), patch.object(packaging, "run", side_effect=RuntimeError("encoder failed")) as encode:
            with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                packaging.render_one(row, output)
        self.assertEqual(output / "临时文件", encode.call_args.kwargs["cwd"])
        self.assertEqual(output / "临时文件" / "P1.partial.mp4", Path(encode.call_args.args[0][-1]))
        self.assertEqual(b"previous complete video", target.read_bytes())
        self.assertFalse((output / "P1.partial.mp4").exists())
        self.assertFalse((output / "临时文件" / "P1.partial.mp4").exists())
        self.assertFalse((output / "临时文件" / "P1.ass").exists())

    def test_reburn_old_manifest_is_rejected(self):
        previous = self.root / "old-manifest.json"
        record = {"schema": "video-montage-packaging-delivery/v1", "results": [{"plan_id": "P1"}]}
        packaging.atomic(previous, record)
        with self.assertRaisesRegex(ValueError, "design configuration snapshot"):
            packaging.reburn(previous, "P1", self.srt, self.root / "new", self.root / "new/manifest.json")
        self.row.pop("subtitle_design")
        packaging.atomic(self.config, {"schema": packaging.SCHEMA, "outputs": [self.row]})
        record.update(config_snapshot_path=str(self.config), config_snapshot_sha256=packaging.sha(self.config))
        packaging.atomic(previous, record)
        with self.assertRaisesRegex(ValueError, "legacy configurations are unsupported"):
            packaging.reburn(previous, "P1", self.srt, self.root / "new", self.root / "new/manifest.json")

    def test_changed_config_or_source_invalidates_receipt(self):
        output = self.root / "out"
        output.mkdir()
        packaged = output / "P1.mp4"
        packaged.write_bytes(b"rendered video")
        ass = output / "P1.ass"
        ass.write_bytes(b"subtitle rendering")
        snap = output / "subtitle-P1.txt"
        snap.write_bytes(self.srt.read_bytes())
        config_snap = output / "packaging_config_snapshot.json"
        config_snap.write_bytes(self.config.read_bytes())
        record = {"schema": "video-montage-packaging-delivery/v1", "config_path": str(self.config),
                  "subtitle_style": {"font": {"path": str(self.font), "sha256": packaging.sha(self.font)}},
                  "config_sha256": packaging.sha(self.config), "config_snapshot_path": str(config_snap),
                  "config_snapshot_sha256": packaging.sha(config_snap), "output_count": 1,
                  "results": [{"plan_id": "P1", "input": {"path": str(self.video), "sha256": packaging.sha(self.video)},
                               "input_frames": 60, "output_path": str(packaged), "output_sha256": packaging.sha(packaged),
                               "subtitles": {"path": str(self.srt), "sha256": packaging.sha(self.srt), "cue_count": 1},
                               "subtitle_snapshot": {"path": str(snap), "sha256": packaging.sha(snap)},
                               "ass": {"path": str(ass), "sha256": packaging.sha(ass)},
                               "text_pins": [], "nameplate": None, "disclaimer": None, "bgm": None}]}
        manifest = self.root / "manifest.json"
        packaging.atomic(manifest, record)
        authority = self.root / "review_authority.json"
        packaging.atomic(authority, {"schema": "video-montage-independent-packaging-review-authority/v1",
                                     "decision": "authorized", "reviewer": {"role": "independent_packaging_reviewer",
                                                                             "review_id": "fixture-reviewer"}})
        review = self.root / "review.json"
        packaging.atomic(review, {"schema": "video-montage-packaging-review/v1", "manifest_sha256": packaging.sha(manifest),
                                  "authority_sha256": packaging.sha(authority),
                                  "reviewer_role": "independent_packaging_reviewer", "reviewer_id": "fixture-reviewer",
                                  "results": [{"plan_id": "P1", "output_sha256": packaging.sha(packaged), "visual_pass": True,
                                               "audio_pass": True, "subtitle_pass": True, "overlay_pass": True}]})
        with patch.object(packaging, "video_spec", return_value=self.spec), patch.object(packaging, "run"), patch.object(packaging, "pcm_stats", return_value={"clipped_samples": 0}):
            self.assertEqual("pass", packaging.validate(manifest, self.root / "report.json", review, authority)["decision"])
            original_review = review.read_bytes()
            duplicate = packaging.read(review)
            duplicate["results"].insert(0, {**duplicate["results"][0], "visual_pass": False})
            packaging.atomic(review, duplicate)
            self.assertIn("review_scope", packaging.validate(manifest, self.root / "report.json", review, authority)["failures"])
            review.write_bytes(original_review)
            saved_font = self.font.read_bytes()
            self.font.write_bytes(b"changed font")
            self.assertIn("subtitle_font_changed", packaging.validate(manifest, self.root / "report.json", review, authority)["failures"])
            self.font.write_bytes(saved_font)
            saved_authority = authority.read_bytes()
            authority.write_text("{}", encoding="utf-8")
            self.assertIn("review_authority", packaging.validate(manifest, self.root / "report.json", review, authority)["failures"])
            authority.write_bytes(saved_authority)
            self.config.write_text("{}", encoding="utf-8")
            self.assertIn("config_changed", packaging.validate(manifest, self.root / "report.json", review, authority)["failures"])
            self.config.write_bytes(config_snap.read_bytes())
            self.video.write_bytes(b"changed input")
            failures = packaging.validate(manifest, self.root / "report.json", review, authority)["failures"]
            self.assertIn("P1:input_changed", failures)


if __name__ == "__main__":
    unittest.main()
