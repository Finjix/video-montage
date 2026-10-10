from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


auto = load("decoded_source_auto", "scripts/autonomous/scripts/autonomous_montage.py")
renderer = load("decoded_source_renderer", "scripts/semantic/scripts/portable_frame_renderer.py")


class DecodedSourceFramesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "long-audio.mkv"
        subprocess.run([str(auto.FFMPEG), "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x284:r=60:d=1", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-bf", "0",
                        "-c:a", "pcm_s16le", "-y", str(self.source)], check=True, capture_output=True)

    def test_source_index_counts_decoded_video_frames_instead_of_audio_duration(self):
        info = auto.probe(self.source)
        stream = next(row for row in info["streams"] if row["codec_type"] == "video")
        self.assertNotIn("nb_frames", stream)
        self.assertGreater(float(info["format"]["duration"]), 1.9)
        self.assertEqual(60, auto.video(self.source)["frames"])
        with patch.object(auto.SOURCE_TIMING.subprocess, "run", side_effect=AssertionError("cache missed")):
            self.assertEqual(60, auto.SOURCE_TIMING.require_constant_frame_rate(auto.FFPROBE, self.source, stream))

    def test_source_range_past_last_decoded_frame_is_rejected_before_encoding(self):
        plan = self.root / "plan.json"
        renderer.atomic_json(plan, {"segments": [{"source_path": str(self.source),
            "source_sha256": renderer.sha256(self.source), "source_in_frame": 58,
            "speech_end_frame": 59, "source_out_frame_exclusive": 61,
            "source_fps_num": 60, "source_fps_den": 1}]})
        with self.assertRaisesRegex(ValueError, "source range beyond decoded"):
            renderer.render(plan, self.root / "out.mp4", self.root / "render.json", 160, 284, 60, "libx264")
        self.assertFalse((self.root / "out.mp4").exists())
        self.assertFalse((self.root / "render.json").exists())


if __name__ == "__main__":
    unittest.main()
