from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package_video.py"
SPEC = importlib.util.spec_from_file_location("package_video_test", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(packaging)


class PackagingContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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

    def test_missing_asset_and_wrong_hash_rejected(self):
        self.pin.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.pin.write_bytes(b"changed image")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.prepare()

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

    def test_render_keeps_subtitle_as_named_txt(self):
        output = self.root / "rendered"
        output.mkdir()
        manifest = self.root / "rendered.json"
        with patch.object(packaging, "video_spec", return_value=self.spec), patch.object(packaging, "render_one", return_value={"plan_id": "P1", "subtitles": {"path": str(self.srt), "sha256": packaging.sha(self.srt)}}):
            packaging.render(self.config, output, manifest)
        self.assertEqual(self.srt.read_bytes(), (output / "subtitles" / "subtitle-P1.txt").read_bytes())
        self.assertFalse(list(output.glob("*.txt")))
        self.assertFalse(list(output.glob("*.srt")))

    def test_reburn_uses_edited_srt_and_new_config(self):
        previous = self.root / "previous.json"
        old_srt = self.root / "old-subtitle.txt"
        old_srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n旧字幕\n", encoding="utf-8")
        packaging.atomic(previous, {"schema": "video-montage-packaging-delivery/v1",
                                    "results": [{"plan_id": "P1", "input": {"path": str(self.video), "sha256": packaging.sha(self.video)},
                                                 "subtitle_snapshot": {"path": str(old_srt), "sha256": packaging.sha(old_srt)},
                                                 "nameplate": None, "text_pins": [], "disclaimer": None, "bgm": None}]})
        output = self.root / "new"
        manifest = self.root / "new_manifest.json"
        with patch.object(packaging, "render", return_value={"schema": "video-montage-packaging-delivery/v1"}) as mock_render:
            result = packaging.reburn(previous, "P1", self.srt, output, manifest)
        config = json.loads((output / "reburn_config.json").read_text(encoding="utf-8"))
        self.assertEqual(str(self.srt), config["outputs"][0]["subtitle_txt"])
        self.assertEqual(packaging.sha(self.srt), config["outputs"][0]["subtitle_sha256"])
        self.assertEqual(str(self.video), config["outputs"][0]["input_path"])
        self.assertEqual((output / "reburn_config.json", output, manifest, None, None), mock_render.call_args.args)
        self.assertEqual(packaging.sha(previous), result["reburn_source"]["manifest_sha256"])
        with self.assertRaisesRegex(FileExistsError, "non-empty"):
            packaging.reburn(previous, "P1", self.srt, output, self.root / "another.json")

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
