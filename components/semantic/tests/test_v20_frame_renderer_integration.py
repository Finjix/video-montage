from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import frame_range_repair
import portable_frame_renderer


FFMPEG = portable_frame_renderer.runtime_binary("ffmpeg")
FFPROBE = portable_frame_renderer.runtime_binary("ffprobe")


class FrameRendererIntegrationTests(unittest.TestCase):
    def test_render_then_delete_exact_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                [
                    str(FFMPEG), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x284:rate=60:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(source),
                ],
                check=True,
            )
            source_hash = portable_frame_renderer.sha256(source)
            plan_path = root / "plan.json"
            plan = {
                "schema": "frame-native-locked-plan/v260928",
                "plan_id": "integration",
                "segments": [
                    {
                        "source_path": str(source), "source_sha256": source_hash,
                        "source_in_frame": 10, "speech_end_frame": 67, "source_out_frame_exclusive": 70,
                        "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0,
                    },
                    {
                        "source_path": str(source), "source_sha256": source_hash,
                        "source_in_frame": 80, "speech_end_frame": 137, "source_out_frame_exclusive": 140,
                        "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0,
                    },
                ],
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            rendered = root / "rendered.mp4"
            render_evidence = root / "render.json"
            previous_ffmpeg = os.environ.get("MONTAGE_FFMPEG")
            previous_ffprobe = os.environ.get("MONTAGE_FFPROBE")
            os.environ["MONTAGE_FFMPEG"] = str(FFMPEG)
            os.environ["MONTAGE_FFPROBE"] = str(FFPROBE)
            try:
                evidence = portable_frame_renderer.render(plan_path, rendered, render_evidence, 160, 284, 60, "libx264")
                self.assertEqual(120, evidence["actual_output_frames"])
                registry_path = root / "delete.json"
                alignment_path = root / "alignment.json"
                alignment_path.write_text(json.dumps({"schema": "spoken-alignment-evidence/v260928", "decision": "pass", "final_syllable_complete": True}), encoding="utf-8")
                registry_path.write_text(json.dumps({"schema": frame_range_repair.SCHEMA, "fps": 60, "ranges": [[58, 59], [90, 99]], "protected_spoken_ranges_60fps_inclusive": [], "spoken_alignment_evidence": {"path": str(alignment_path), "sha256": portable_frame_renderer.sha256(alignment_path)}}), encoding="utf-8")
                repaired = root / "repaired.mp4"
                repair_evidence = root / "repair.json"
                value = frame_range_repair.render(rendered, repaired, registry_path, repair_evidence, "libx264")
                self.assertEqual(12, value["deleted_frame_count"])
                self.assertEqual(108, value["output_frames"])
                self.assertEqual(0, value["video_hold"])

                overlap_plan = root / "overlap-plan.json"
                overlap_plan.write_text(json.dumps({
                    "schema": "frame-native-locked-plan/v260928",
                    "plan_id": "overlap",
                    "segments": [
                        {"candidate_id": "a", "source_path": str(source), "source_sha256": source_hash, "source_in_frame": 10, "speech_end_frame": 67, "source_out_frame_exclusive": 70, "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0},
                        {"candidate_id": "b", "source_path": str(source), "source_sha256": source_hash, "source_in_frame": 65, "speech_end_frame": 117, "source_out_frame_exclusive": 120, "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0}
                    ]
                }), encoding="utf-8")
                overlap_output = root / "overlap.mp4"
                overlap_evidence = root / "overlap.json"
                overlap_value = portable_frame_renderer.render(overlap_plan, overlap_output, overlap_evidence, 160, 284, 60, "libx264")
                self.assertEqual(110, overlap_value["actual_output_frames"])
                self.assertEqual(2, overlap_value["source_segment_count"])
                self.assertEqual(1, overlap_value["render_segment_count"])
                self.assertEqual([["a", "b"]], overlap_value["coalesced_adjacent_source_groups"])
            finally:
                if previous_ffmpeg is None:
                    os.environ.pop("MONTAGE_FFMPEG", None)
                else:
                    os.environ["MONTAGE_FFMPEG"] = previous_ffmpeg
                if previous_ffprobe is None:
                    os.environ.pop("MONTAGE_FFPROBE", None)
                else:
                    os.environ["MONTAGE_FFPROBE"] = previous_ffprobe



    def test_fractional_source_fps_locks_audio_and_video_to_integer_output_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-5994.mp4"
            subprocess.run(
                [
                    str(FFMPEG), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x284:rate=60000/1001:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(source),
                ],
                check=True,
            )
            source_hash = portable_frame_renderer.sha256(source)
            plan_path = root / "fractional-plan.json"
            plan_path.write_text(json.dumps({
                "schema": "frame-native-locked-plan/v260928",
                "plan_id": "fractional-av-lock",
                "segments": [
                    {"source_path": str(source), "source_sha256": source_hash, "source_in_frame": 10, "speech_end_frame": 345, "source_out_frame_exclusive": 349, "source_fps_num": 60000, "source_fps_den": 1001, "speed": 1.0},
                    {"source_path": str(source), "source_sha256": source_hash, "source_in_frame": 400, "speech_end_frame": 614, "source_out_frame_exclusive": 618, "source_fps_num": 60000, "source_fps_den": 1001, "speed": 1.0},
                ],
            }), encoding="utf-8")
            output = root / "fractional.mp4"
            evidence_path = root / "fractional.json"
            previous_ffmpeg = os.environ.get("MONTAGE_FFMPEG")
            previous_ffprobe = os.environ.get("MONTAGE_FFPROBE")
            os.environ["MONTAGE_FFMPEG"] = str(FFMPEG)
            os.environ["MONTAGE_FFPROBE"] = str(FFPROBE)
            try:
                evidence = portable_frame_renderer.render(plan_path, output, evidence_path, 160, 284, 60, "libx264")
            finally:
                if previous_ffmpeg is None:
                    os.environ.pop("MONTAGE_FFMPEG", None)
                else:
                    os.environ["MONTAGE_FFMPEG"] = previous_ffmpeg
                if previous_ffprobe is None:
                    os.environ.pop("MONTAGE_FFPROBE", None)
                else:
                    os.environ["MONTAGE_FFPROBE"] = previous_ffprobe
            self.assertEqual(557, evidence["expected_output_frames"])
            self.assertEqual(557, evidence["actual_output_frames"])
            self.assertEqual([339 / 60, 218 / 60], [row["output_duration_seconds"] for row in evidence["segments"]])

if __name__ == "__main__":
    unittest.main()
