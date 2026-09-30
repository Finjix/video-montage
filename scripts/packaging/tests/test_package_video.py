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
                    "subtitles": row["subtitles"], "text_pins": row["text_pins"]}

        with patch.object(packaging, "video_spec", return_value=self.spec), \
             patch.object(packaging, "render_one", side_effect=encode):
            result = packaging.render(self.config, first, manifest)
            self.assertEqual({"成片", "混剪（无包装）", "字幕（可修改）", "临时文件"},
                             {p.name for p in first.iterdir()})
            self.assertEqual({"配置"}, {p.name for p in (first / "临时文件").iterdir()})
            self.assertFalse(manifest.is_relative_to(first))
            self.assertEqual(self.video.read_bytes(), (first / "混剪（无包装）" / "P1.mp4").read_bytes())
            self.assertEqual(str(first / "混剪（无包装）" / "P1.mp4"), result["results"][0]["input"]["path"])
            marker = first / "字幕（可修改）" / "修改字幕后让AI重新烧录"
            self.assertTrue(marker.is_file())
            marker.write_text("用户保留的说明", encoding="utf-8")
            packaging.ensure_delivery_layout(first)
            self.assertEqual("用户保留的说明", marker.read_text(encoding="utf-8"))
            edited = first / "字幕（可修改）" / "subtitle-P1.txt"
            edited.write_text("1\n00:00:00,000 --> 00:00:00,900\n新字幕\n", encoding="utf-8")
            self.video.unlink()  # Reburn must use the delivered clean copy.
            previous_hash = packaging.sha(manifest)
            second = first
            new_manifest = manifest
            updated = packaging.reburn(manifest, "P1", edited, second, new_manifest)
            self.assertEqual(edited.read_bytes(), (second / "字幕（可修改）" / edited.name).read_bytes())
            self.assertTrue((second / "字幕（可修改）" / marker.name).is_file())
            reopened = packaging.prepared_rows(second / "临时文件" / "配置" / "reburn_config.json")
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

    def test_delivery_rejects_manifest_and_validation_report_inside_output(self):
        output = self.root / "work" / "自动化混剪_20260930_153000_123456"
        inside = output / "临时文件" / "报告" / "validation.json"
        with self.assertRaisesRegex(ValueError, "records must be outside"):
            packaging.render(self.config, output, inside)
        self.assertFalse(output.exists())
        manifest = packaging.delivery_category(output, "manifests") / "packaging_manifest.json"
        packaging.atomic(manifest, {"results": [{"output_path": str(output / "成片" / "P1.mp4")}]})
        with self.assertRaisesRegex(ValueError, "reports must be outside"):
            packaging.validate(manifest, inside)
        self.assertFalse(inside.exists())

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

    def test_default_subtitle_style_and_single_line_ass(self):
        style = self.prepare()[0]["subtitle_style"]
        self.assertEqual({"path": str(self.font), "sha256": packaging.sha(self.font)}, style["font"])
        self.assertEqual(-1300, style["capcut_reference"]["y"])
        self.assertEqual(2357, style["ass"]["position_y"])
        self.assertEqual("无尽冬日", style["emphasis"]["text"])
        self.assertEqual(13, style["emphasis"]["capcut_font_size"])
        self.assertEqual(203, style["emphasis"]["ass_font_size"])
        self.assertEqual("pastel_cyan_extrusion_v3", style["emphasis"]["effect"])
        ass = self.root / "sample.ass"
        packaging.write_ass(ass, [{"start_ms": 0, "end_ms": 900, "text": "是兄弟就来"}], style)
        content = ass.read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1920\nPlayResY: 3414", content)
        self.assertIn("WenYue XinQingNianTi J W8,187,&H0000DEFF", content)
        self.assertIn(",1,10,0,5,0,0,0,1", content)
        self.assertIn(r"{\pos(960,2357)}是兄弟就来", content)
        with self.assertRaisesRegex(ValueError, "one line"):
            packaging.write_ass(ass, [{"start_ms": 0, "end_ms": 900, "text": "第一行\n第二行"}], style)

    def test_every_game_title_occurrence_uses_size_13(self):
        style = self.prepare()[0]["subtitle_style"]
        ass = self.root / "emphasis.ass"
        packaging.write_ass(ass, [
            {"start_ms": 0, "end_ms": 900, "text": "玩无尽冬日，再玩无尽冬日"},
            {"start_ms": 900, "end_ms": 1350, "text": "普通字幕"},
            {"start_ms": 1350, "end_ms": 1800, "text": "无尽冬日"},
        ], style)
        content = ass.read_text(encoding="utf-8-sig")
        base_lines = [line for line in content.splitlines() if line.startswith("Dialogue: 0,")]
        self.assertEqual(3, len(base_lines))
        enlarged = r"{\fs203\alpha&HFF&}无尽冬日{\fs187\alpha&H00&}"
        self.assertEqual(3, "\n".join(base_lines).count(enlarged))
        gap = r"{\fs100\fscx280}\h{\fs187\fscx100}"
        self.assertIn(f"玩{gap}{enlarged}{gap}，再玩{gap}{enlarged}", base_lines[0])
        self.assertIn("普通字幕", base_lines[1])
        self.assertIn(enlarged, base_lines[2])
        self.assertEqual(31, content.count("Dialogue: "))
        self.assertIn(r"\clip(1,m ", content)
        self.assertIn(r"{\alpha&H00&}无{\alpha&HFF&}尽", content)

    def test_real_flower_outlines_clear_adjacent_subtitles(self):
        import cv2
        import numpy as np
        font_dir = self.root / "fonts"
        font_dir.mkdir()
        (font_dir / "font.otf").write_bytes((packaging.ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf").read_bytes())
        style = self.prepare()[0]["subtitle_style"]
        for text in ["玩无尽冬日啊", "才入坑的无尽冬日"]:
            ass = self.root / "full.ass"
            packaging.write_ass(ass, [dict(start_ms=0, end_ms=100, text=text)], style)
            content = ass.read_text(encoding="utf-8-sig")
            header, events = content.split("Dialogue:", 1)
            rows = ["Dialogue:" + row for row in events.split("Dialogue:")]
            masks = []
            for name, base in [("ordinary", True), ("flower", False)]:
                selected = [row for row in rows if row.startswith("Dialogue: 0,") == base]
                # White outline exposes the normally black stroke against the black test canvas.
                (self.root / f"{name}.ass").write_text(header.replace("&H00000000", "&H00FFFFFF") + "".join(selected), encoding="utf-8-sig")
                subprocess.run([str(packaging.FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=black:s=1440x2560:d=0.04", "-vf", f"ass={name}.ass:fontsdir=fonts", "-frames:v", "1", f"{name}.png"],
                    cwd=self.root, check=True, capture_output=True)
                masks.append(np.max(cv2.imread(str(self.root / f"{name}.png")), axis=2) > 16)
            self.assertTrue(all(mask.any() for mask in masks))
            self.assertFalse(np.any(masks[0] & masks[1]))
            distance = cv2.distanceTransform((~masks[0]).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
            self.assertGreaterEqual(float(distance[masks[1]].min()), 8, text)

    def test_missing_or_changed_subtitle_font_is_rejected(self):
        value = {"schema": packaging.SCHEMA, "subtitle_font_path": "missing.otf", "outputs": [self.row]}
        self.config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        other = self.root / "other.otf"
        other.write_bytes(b"different font")
        value["subtitle_font_path"] = other.name
        self.config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.prepare()
        other.write_bytes(self.font.read_bytes())
        self.assertEqual(str(other), self.prepare()[0]["subtitle_style"]["font"]["path"])

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

    def test_real_font_burn_matches_reference_geometry(self):
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
             patch.object(packaging, "SUBTITLE_FONT_SHA256", REFERENCE_FONT_SHA256):
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
            yellow = [(x, y) for y in range(1650, 1900) for x in range(400, 1040)
                      if pixels[x, y][0] > 150 and pixels[x, y][1] > 100 and pixels[x, y][2] < 80]
        self.assertTrue(yellow)
        left, top = min(x for x, _ in yellow), min(y for _, y in yellow)
        right, bottom = max(x for x, _ in yellow), max(y for _, y in yellow)
        self.assertLessEqual(abs((left + right) / 2 - 720), 5)
        self.assertLessEqual(abs((top + bottom) / 2 - 1768), 5)
        self.assertTrue(405 <= right - left + 1 <= 435)
        self.assertTrue(84 <= bottom - top + 1 <= 100)

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

    def test_reburn_uses_edited_srt_and_new_config(self):
        previous = self.root / "previous.json"
        old_srt = self.root / "old-subtitle.txt"
        old_srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n旧字幕\n", encoding="utf-8")
        packaging.atomic(previous, {"schema": "video-montage-packaging-delivery/v1",
                                    "subtitle_style": {"font": {"path": str(self.root / "old-external.otf"),
                                                                "sha256": packaging.sha(self.font)}},
                                    "results": [{"plan_id": "P1", "input": {"path": str(self.video), "sha256": packaging.sha(self.video)},
                                                 "subtitle_snapshot": {"path": str(old_srt), "sha256": packaging.sha(old_srt)},
                                                 "nameplate": None, "text_pins": [], "disclaimer": None, "bgm": None}]})
        output = self.root / "new"
        manifest = output / "packaging_manifest.json"
        with patch.object(packaging, "render", return_value={"schema": "video-montage-packaging-delivery/v1"}) as mock_render:
            result = packaging.reburn(previous, "P1", self.srt, output, manifest)
        config = json.loads((output / "config" / "reburn_config.json").read_text(encoding="utf-8"))
        self.assertEqual(str(self.srt), config["outputs"][0]["subtitle_txt"])
        self.assertEqual(packaging.sha(self.srt), config["outputs"][0]["subtitle_sha256"])
        self.assertEqual(str(self.video), config["outputs"][0]["input_path"])
        self.assertNotIn("subtitle_font_path", config)
        self.assertEqual(str(self.font), packaging.subtitle_style(config, output)["font"]["path"])
        self.assertEqual((output / "config" / "reburn_config.json", output, manifest, None, None), mock_render.call_args.args)
        self.assertEqual(packaging.sha(previous), result["reburn_source"]["manifest_sha256"])
        self.assertTrue(mock_render.call_args.kwargs["overwrite"])

    def test_failed_overwrite_encoding_preserves_existing_video(self):
        row = self.prepare()[0]
        row["_overwrite"] = True
        output = self.root / "work" / "自动化混剪_20260930_153000_123456"
        packaging.ensure_delivery_layout(output)
        target = output / "成片" / "P1.mp4"; target.write_bytes(b"previous complete video")
        with patch.object(packaging, "run", side_effect=RuntimeError("encoder failed")) as encode:
            with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                packaging.render_one(row, output)
        self.assertEqual(output / "临时文件", encode.call_args.kwargs["cwd"])
        self.assertEqual(output / "临时文件" / "P1.partial.mp4", Path(encode.call_args.args[0][-1]))
        self.assertEqual(b"previous complete video", target.read_bytes())
        self.assertFalse((output / "P1.partial.mp4").exists())
        self.assertFalse((output / "临时文件" / "P1.partial.mp4").exists())
        self.assertFalse((output / "临时文件" / "P1.ass").exists())

    def test_reburn_old_manifest_uses_new_default_style(self):
        previous = self.root / "old-manifest.json"
        packaging.atomic(previous, {"schema": "video-montage-packaging-delivery/v1",
                                    "results": [{"plan_id": "P1", "input": {"path": str(self.video), "sha256": packaging.sha(self.video)},
                                                 "subtitle_snapshot": {"path": str(self.srt), "sha256": packaging.sha(self.srt)},
                                                 "nameplate": None, "text_pins": [], "disclaimer": None, "bgm": None}]})
        output = self.root / "old-reburn"
        with patch.object(packaging, "render", return_value={"schema": "video-montage-packaging-delivery/v1"}):
            packaging.reburn(previous, "P1", self.srt, output, output / "manifest.json")
        config = packaging.read(output / "config" / "reburn_config.json")
        self.assertNotIn("subtitle_font_path", config)
        self.assertEqual(str(self.font), packaging.subtitle_style(config, output)["font"]["path"])

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
