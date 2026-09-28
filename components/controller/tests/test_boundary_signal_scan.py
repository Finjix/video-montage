from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from boundary_signal_scan import scan


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BoundarySignalTests(unittest.TestCase):
    def evidence(self, short_run: bool) -> Path:
        root = Path(tempfile.mkdtemp())
        frames = []
        for index in range(72):
            if short_run:
                value = 255 if 24 <= index < 36 else 0
            else:
                value = 255 if index >= 36 else 0
            path = root / f"{index}.jpg"
            Image.new("L", (32, 32), value).save(path)
            frames.append({"output_frame_number": 100 + index, "path": str(path), "sha256": sha(path)})
        pcm = root / "cut.wav"
        with wave.open(str(pcm), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000); handle.writeframes(b"\0\0" * 48000)
        evidence = root / "evidence.json"
        evidence.write_text(json.dumps({"schema": "ffmpeg-post-encode-evidence/v260928", "complete_plan_scope": True, "cuts": [{"plan_id": "P", "cut_index": 1, "boundary_kind": "concat_cut", "frames_before_boundary": 36, "frames_before_cut": 36, "frames": frames, "pcm_path": str(pcm), "cut_sample_index_in_pcm": 24000}]}), encoding="utf-8")
        return evidence

    def test_single_expected_cut_passes(self):
        self.assertEqual("pass", scan(self.evidence(False))["decision"])

    def test_six_frame_insert_before_cut_rejects(self):
        report = scan(self.evidence(True))
        self.assertEqual("reject", report["decision"])
        self.assertIn("EXTRA_VISUAL_TRANSITION_NEAR_CUT", report["failures"][0]["visual"])

    def test_thirteen_frame_output_head_rejects(self):
        root = Path(tempfile.mkdtemp())
        frames = []
        for index in range(72):
            path = root / f"head-{index}.jpg"
            Image.new("L", (32, 32), 255 if index >= 13 else 0).save(path)
            frames.append({"output_frame_number": index, "path": str(path), "sha256": sha(path)})
        pcm = root / "head.wav"
        with wave.open(str(pcm), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000); handle.writeframes(b"\0\0" * 48000)
        evidence = root / "head-evidence.json"
        evidence.write_text(json.dumps({"schema":"ffmpeg-post-encode-evidence/v260928","complete_plan_scope":True,"cuts":[{"plan_id":"P","cut_index":0,"boundary_kind":"output_start","frames_before_boundary":0,"frames":frames,"pcm_path":str(pcm),"cut_sample_index_in_pcm":0}]}),encoding="utf-8")
        report = scan(evidence)
        self.assertEqual("reject", report["decision"])
        self.assertIn("SHORT_OUTPUT_HEAD_SHOT", report["failures"][0]["visual"])

    def test_thirty_six_frame_insert_before_cut_rejects(self):
        root = Path(tempfile.mkdtemp())
        frames = []
        for index in range(144):
            value = 255 if 36 <= index < 72 else 0
            path = root / f"micro-{index}.jpg"
            Image.new("L", (32, 32), value).save(path)
            frames.append({"output_frame_number": 100 + index, "path": str(path), "sha256": sha(path)})
        pcm = root / "micro.wav"
        with wave.open(str(pcm), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000); handle.writeframes(b"\0\0" * 48000)
        evidence = root / "micro-evidence.json"
        evidence.write_text(json.dumps({"schema": "ffmpeg-post-encode-evidence/v260928", "complete_plan_scope": True, "cuts": [{"plan_id": "P", "cut_index": 1, "boundary_kind": "concat_cut", "frames_before_boundary": 72, "frames_before_cut": 72, "frames": frames, "pcm_path": str(pcm), "cut_sample_index_in_pcm": 24000}]}), encoding="utf-8")
        report = scan(evidence)
        self.assertEqual("reject", report["decision"])
        self.assertIn("EXTRA_VISUAL_TRANSITION_NEAR_CUT", report["failures"][0]["visual"])


if __name__ == "__main__":
    unittest.main()
