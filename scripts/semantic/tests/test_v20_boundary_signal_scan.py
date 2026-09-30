from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from v20_boundary_signal_scan import scan_bundle


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BoundarySignalScanTests(unittest.TestCase):
    def bundle(self, short_tail: bool):
        root = Path(tempfile.mkdtemp())
        windows = {}
        for side in ("in", "out"):
            frames = []
            for index in range(48):
                value = 255 if short_tail and side == "out" and index >= 18 else 0
                path = root / f"{side}-{index}.png"
                Image.new("L", (32, 32), value).save(path)
                frames.append({"source_frame_number": 100 + index, "path": str(path), "sha256": sha(path)})
            windows[side] = {"boundary_frame": 124, "boundary_index": 24, "window_start_frame": 100, "expected_frame_count": 48, "actual_frame_count": 48, "frames": frames}
        path = root / "bundle.json"
        path.write_text(json.dumps({"schema": "candidate-boundary-evidence/v260928", "candidate_id": "C", "dense_windows": windows}), encoding="utf-8")
        return path

    def test_clean_window_passes(self):
        self.assertEqual("pass", scan_bundle(self.bundle(False))["decision"])

    def test_six_frame_tail_shot_rejects(self):
        report = scan_bundle(self.bundle(True))
        self.assertEqual("reject", report["decision"])
        self.assertIn("out:SHORT_TAIL_SHOT_BEFORE_OUT", report["failures"])

    def test_thirteen_frame_head_shot_rejects(self):
        root = Path(tempfile.mkdtemp())
        windows = {}
        for side in ("in", "out"):
            frames = []
            for index in range(72):
                value = 255 if side == "in" and index >= 13 else 0
                path = root / f"{side}-13f-{index}.png"
                Image.new("L", (32, 32), value).save(path)
                frames.append({"source_frame_number": 100 + index, "path": str(path), "sha256": sha(path)})
            windows[side] = {"boundary_frame": 100 if side == "in" else 136, "boundary_index": 0 if side == "in" else 36, "window_start_frame": 100, "expected_frame_count": 72, "actual_frame_count": 72, "stable_run_required_frames": 30, "frames": frames}
        bundle = root / "bundle-13f.json"
        bundle.write_text(json.dumps({"schema": "candidate-boundary-evidence/v260928", "candidate_id": "C13", "dense_windows": windows}), encoding="utf-8")
        report = scan_bundle(bundle)
        self.assertEqual("reject", report["decision"])
        self.assertIn("in:SHORT_HEAD_SHOT_AFTER_IN", report["failures"])

    def test_thirty_six_frame_tail_microshot_rejects(self):
        root = Path(tempfile.mkdtemp())
        windows = {}
        for side in ("in", "out"):
            frames = []
            for index in range(144):
                value = 255 if side == "out" and index >= 36 else 0
                path = root / f"{side}-36f-{index}.png"
                Image.new("L", (32, 32), value).save(path)
                frames.append({"source_frame_number": 100 + index, "path": str(path), "sha256": sha(path)})
            windows[side] = {"boundary_frame": 172, "boundary_index": 72, "window_start_frame": 100, "expected_frame_count": 144, "actual_frame_count": 144, "stable_run_required_frames": 60, "frames": frames}
        bundle = root / "bundle-36f.json"
        bundle.write_text(json.dumps({"schema": "candidate-boundary-evidence/v260928", "candidate_id": "C36", "dense_windows": windows}), encoding="utf-8")
        report = scan_bundle(bundle)
        self.assertEqual("reject", report["decision"])
        self.assertIn("out:SHORT_TAIL_SHOT_BEFORE_OUT", report["failures"])


if __name__ == "__main__":
    unittest.main()
