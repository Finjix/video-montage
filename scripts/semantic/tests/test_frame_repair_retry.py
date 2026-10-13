from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import frame_range_repair as repair


class FrameRepairRetryTests(unittest.TestCase):
    def test_failed_or_interrupted_encoding_removes_partial_for_retry(self):
        for failure in ("encoder", "probe", "interrupt"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output = root / "source.mp4", root / "output.mp4"
                source.write_bytes(b"original source")
                alignment = root / "alignment.json"
                alignment.write_text(json.dumps({"decision": "pass", "final_syllable_complete": True}), encoding="utf-8")
                registry = root / "registry.json"
                registry.write_text(json.dumps({"schema": repair.SCHEMA, "fps": 60, "ranges": [[1, 1]],
                    "protected_spoken_ranges_60fps_inclusive": [], "spoken_alignment_evidence": {
                        "path": str(alignment), "sha256": repair.sha256(alignment)}}), encoding="utf-8")

                def probe(_, path):
                    if path != source:
                        raise ValueError("encoded video probe failed")
                    return {"streams": [{"codec_type": "video", "avg_frame_rate": "60/1", "nb_frames": "3"},
                                        {"codec_type": "audio"}]}

                def encode(command, **_):
                    Path(command[-1]).write_bytes(b"partial video")
                    if failure == "interrupt":
                        raise KeyboardInterrupt("encoding interrupted")
                    return subprocess.CompletedProcess(command, 1 if failure == "encoder" else 0, "", "encoder failed")

                expected = KeyboardInterrupt if failure == "interrupt" else RuntimeError if failure == "encoder" else ValueError
                with patch.object(repair, "runtime_binary", return_value=root / "ffmpeg.exe"), \
                     patch.object(repair, "probe", side_effect=probe), \
                     patch.object(repair.subprocess, "run", side_effect=encode):
                    with self.assertRaises(expected):
                        repair.render(source, output, registry, root / "evidence.json", "libx264")
                self.assertFalse(output.with_suffix(".partial.mp4").exists())
                self.assertFalse(output.exists())
                self.assertEqual(b"original source", source.read_bytes())

    def test_artifact_collisions_are_rejected_before_media_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, registry = root / "source.mp4", root / "registry.json"
            source.write_bytes(b"source")
            registry.write_text("{}", encoding="utf-8")
            output = root / "output.mp4"
            with patch.object(repair, "runtime_binary", side_effect=AssertionError("media work began")):
                for target, evidence in [(output, output), (output, source), (output, registry),
                                         (source, root / "evidence.json"), (output, output.with_suffix(".partial.mp4"))]:
                    with self.subTest(output=target.name, evidence=evidence.name):
                        with self.assertRaisesRegex(ValueError, "paths collide"):
                            repair.render(source, target, registry, evidence, "libx264")


if __name__ == "__main__":
    unittest.main()
