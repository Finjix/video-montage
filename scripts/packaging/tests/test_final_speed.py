"""Exercise the actual FFmpeg speed-up, audio pitch and subtitle timeline."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


packager = module("speed_packager", "scripts/packaging/scripts/package_video.py")
auto = module("speed_auto", "scripts/autonomous/scripts/autonomous_montage.py")
controller = module("speed_controller", "scripts/controller/scripts/ffmpeg_controller.py")


def presentation(subtitles, flowers=None):
    cues = packager.parse_srt(subtitles, 2000)
    settings = []
    for i, c in enumerate(cues, 1):
        z = {"index": i, "text": c["text"], "color": "yellow", "entrance": {"effect": "none"}}
        if flowers and i <= 2:
            word = "无尽冬日" if i == 1 else "冰雪世界"
            start = c["text"].index(word)
            z["spans"] = [{"start": start, "end": start+len(word), "flower": flowers[i-1]}]
        settings.append(z)
    return {"subtitle_sha256": packager.sha(subtitles), "font": "w8", "game_names": [],
            "reason": "Test explicit timeline and flower ranges", "cues": settings}


class FinalSpeedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source.mp4"
        subprocess.run([str(packager.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=black:s=1440x2560:r=60:d=2", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=2", "-af", "volume=0.08",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(self.source)], check=True, capture_output=True)

    def test_clean_speed_preserves_pitch_duration_and_original_input(self):
        original_hash = packager.sha(self.source)
        row = packager.speed_clean(self.source, self.root / "final.mp4")
        self.assertEqual(120, row["input_frames"])
        self.assertEqual(100, row["output_spec"]["frames"])
        self.assertEqual(original_hash, packager.sha(self.source))
        audio = auto.pcm(Path(row["output_path"]))
        spectrum = np.abs(np.fft.rfft(audio[2000:18000]))
        frequency = np.argmax(spectrum)  # One-second window at 16 kHz.
        self.assertLessEqual(abs(frequency - 440), 2)
        proof = auto.delivery_mix_evidence(audio, row)
        self.assertEqual("pass", proof["decision"], proof)
        altered = audio.copy()
        altered[5000:6000] += 0.05
        self.assertEqual("reject", auto.delivery_mix_evidence(altered, row)["decision"])
        with self.assertRaisesRegex(ValueError, "separate"):
            packager.speed_clean(self.source, self.source)

    def test_packaged_subtitle_tail_and_looped_music_follow_final_timeline(self):
        subtitles = self.root / "subtitle.txt"
        subtitles.write_text("1\n00:00:01,200 --> 00:00:01,800\n测试字幕\n", encoding="utf-8")
        music = self.root / "music.wav"
        subprocess.run([str(packager.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=220:sample_rate=48000:duration=0.4", "-af", "volume=0.01",
            str(music)], check=True, capture_output=True)
        config = self.root / "config.json"
        packager.atomic(config, {"schema": packager.SCHEMA, "outputs": [{"plan_id": "P1",
            "input_path": str(self.source), "subtitle_txt": str(subtitles), "subtitle_design": presentation(subtitles),
            "bgm": {"path": str(music), "gain_db": -18}}]})
        row = packager.render_one(packager.prepared_rows(config)[0], self.root / "packaged")
        self.assertEqual(100, row["output_spec"]["frames"])
        self.assertEqual((1000, 1500), tuple(packager.delivery_cues(row)[0][key] for key in ("start_ms", "end_ms")))
        for frame_number, visible in ((55, False), (75, True), (95, False)):
            target = self.root / f"frame-{frame_number}.png"
            subprocess.run([str(packager.FFMPEG), "-v", "error", "-i", row["output_path"],
                "-vf", f"select=eq(n\\,{frame_number})", "-frames:v", "1", str(target)],
                check=True, capture_output=True)
            pixels = np.asarray(Image.open(target).convert("RGB"))
            yellow = (pixels[:, :, 0] > 150) & (pixels[:, :, 1] > 100) & (pixels[:, :, 2] < 80)
            self.assertEqual(visible, bool(yellow.any()))
        proof = auto.delivery_mix_evidence(auto.pcm(Path(row["output_path"])), row)
        self.assertEqual("pass", proof["decision"], proof)

    def test_fractional_output_frame_count_keeps_audio_and_video_aligned(self):
        for count in (61, 119):
            with self.subTest(input_frames=count):
                source = self.root / f"source-{count}.mp4"
                subprocess.run([str(packager.FFMPEG), "-v", "error", "-y", "-i", str(self.source),
                    "-frames:v", str(count), "-t", f"{count / 60:.9f}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(source)], check=True, capture_output=True)
                row = packager.speed_clean(source, self.root / f"final-{count}.mp4")
                self.assertEqual(packager.final_frames(count), row["output_spec"]["frames"])
                info = packager.probe(Path(row["output_path"]))
                audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
                self.assertLessEqual(abs(float(audio["duration"]) - row["output_frames"] / 60), 1 / 60)
                proof = auto.delivery_mix_evidence(auto.pcm(Path(row["output_path"])), row)
                self.assertEqual("pass", proof["decision"], proof)

    def test_flower_selection_visibility_and_gaps_follow_final_timeline(self):
        subtitles = self.root / "flower-subtitle.txt"
        subtitles.write_text("1\n00:00:00,200 --> 00:00:00,700\n玩无尽冬日啊\n\n"
                             "2\n00:00:00,900 --> 00:00:01,300\n冰雪世界\n\n"
                             "3\n00:00:01,400 --> 00:00:01,800\n普通字幕\n", encoding="utf-8")
        config = self.root / "flower-config.json"
        music = self.root / "flower-music.wav"
        subprocess.run([str(packager.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:sample_rate=48000:duration=0.4", "-af", "volume=0.01",
                        str(music)], check=True, capture_output=True)
        packager.atomic(config, {"schema": packager.SCHEMA,
                                 "outputs": [{"plan_id": "FLOWER",
            "input_path": str(self.source), "subtitle_txt": str(subtitles), "subtitle_design": presentation(subtitles, ["ice1", "ice2"]), "subtitle_flower_seed": 4,
            "bgm": {"path": str(music), "gain_db": -18}}]})
        row = packager.render_one(packager.prepared_rows(config)[0], self.root / "flower-packaged")
        self.assertEqual(["ice1", "ice2"], [c["spans"][0]["flower"] for c in row["design"]["cues"][:2]])
        self.assertEqual(4, row["subtitle_flower_seed"])
        self.assertEqual(100, row["output_spec"]["frames"])
        # Evaluate frames before, during and after each effect, including an empty gap.
        for frame_number, visible in ((5, False), (10, True), (20, True), (35, False),
                                      (44, False), (45, True), (55, True), (65, False), (80, False), (95, False)):
            target = self.root / f"flower-frame-{frame_number}.png"
            subprocess.run([str(packager.FFMPEG), "-v", "error", "-i", row["output_path"],
                "-vf", f"select=eq(n\\,{frame_number})", "-frames:v", "1", str(target)], check=True, capture_output=True)
            pixels = np.asarray(Image.open(target).convert("RGB")).astype(np.int16)
            blue = (pixels[:, :, 2] > 140) & (pixels[:, :, 2] > pixels[:, :, 0] + 25) & (pixels[:, :, 1] > 60)
            self.assertEqual(visible, bool(blue.any()), frame_number)

    def test_legacy_final_speed_receipt_and_double_speed_guard(self):
        render = self.root / "render.json"
        plan = self.root / "plan.json"
        packager.atomic(plan, {})
        packager.atomic(render, {"actual_output_frames": 120, "render_mode": "source_frame_ranges/v1",
            "plan_sha256": packager.sha(plan), "segments": [
                {"expected_output_frames": 36}, {"expected_output_frames": 84}]})
        output = self.root / "legacy"
        output.mkdir()
        item = {"plan_id": "P1", "export_path": str(self.source), "render_evidence_path": str(render),
                "render_evidence_sha256": packager.sha(render)}
        row = controller.finalize_one(item, output, 1440, 2560, 60, "libx264", True)
        self.assertEqual(100, packager.video_spec(Path(row["output_path"]))["frames"])
        self.assertEqual(row, controller.finalize_one(item, output, 1440, 2560, 60, "libx264", True))
        with self.assertRaisesRegex(RuntimeError, "matching receipt"):
            controller.finalize_one(item, output, 1440, 2560, 60, "libx264")
        manifest = self.root / "delivery.json"
        validation = self.root / "validation.json"
        packager.atomic(manifest, {"schema": "ffmpeg-controller-delivery/v260928", "output_count": 1, "results": [row]})
        packager.atomic(validation, {})
        with self.assertRaisesRegex(ValueError, "twice"):
            packager.render(self.root / "unused.json", self.root / "out", self.root / "manifest.json", manifest, validation)
        locked = self.root / "locked.json"
        packager.atomic(locked, {"plans": [{"plan_id": "P1", "path": str(plan), "sha256": packager.sha(plan)}]})
        cuts = []
        def extract(video, pid, index, kind, boundary, total, directory):
            cuts.append((kind, boundary, total))
            return {}
        with patch.object(controller.evidence_module, "extract_boundary", side_effect=extract), \
             patch("sys.argv", ["post_encode_evidence.py", "--delivery-manifest", str(manifest),
                "--locked-index", str(locked), "--output-dir", str(self.root / "evidence"),
                "--output", str(self.root / "evidence.json")]):
            self.assertEqual(0, controller.evidence_module.main())
        self.assertEqual([("output_start", 0, 100), ("concat_cut", 30, 100), ("output_end", 100, 100)], cuts)


if __name__ == "__main__":
    unittest.main()
