from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(value)
    return value


gate = module("frame_gate", "scripts/v20_frame_plan_gate.py")
repair = module("frame_repair", "scripts/frame_range_repair.py")
renderer = module("portable_frame_renderer_for_unit", "scripts/portable_frame_renderer.py")
cut_smoke = module("cut_smoke_for_unit", "scripts/v9_cut_smoke.py")
output_evidence = module("output_evidence_for_unit", "scripts/v9_output_evidence.py")
fail_closed = module("v20_fail_closed_for_unit", "scripts/v20_fail_closed.py")
runtime = module("v9_runtime_frame_contract", "scripts/v9_gate_runtime.py")


class FrameNativeContractTests(unittest.TestCase):
    def test_semantic_evidence_uses_bundled_media_tools(self):
        ffmpeg = ROOT.parents[1] / "dependencies/ffmpeg/bin/ffmpeg.exe"
        ffprobe = ROOT.parents[1] / "dependencies/ffmpeg/bin/ffprobe.exe"
        self.assertEqual(ffmpeg, Path(cut_smoke.FFMPEG))
        self.assertEqual(ffmpeg, Path(output_evidence.FFMPEG))
        self.assertEqual(ffprobe, Path(output_evidence.FFPROBE))
        template = json.loads((ROOT / "references/pipeline-config.template.json").read_text(encoding="utf-8"))
        phase = next(item for item in template["phases"] if item["name"] == "SOURCE_FRAME_EVIDENCE")
        self.assertIn("${package_root}/../../dependencies/ffmpeg/bin/ffmpeg.exe", phase["command"])

    def test_renderer_defaults_to_cpu_encoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["renderer", "--plan", str(root / "plan.json"), "--output", str(root / "output.mp4"), "--evidence", str(root / "evidence.json")]
            with patch.object(sys, "argv", argv), patch.object(renderer, "render", return_value={"render_mode": "source_frame_ranges/v1", "export_path": "output"}) as render:
                self.assertEqual(0, renderer.main())
            self.assertEqual("libx264", render.call_args.args[-1])

    def test_frame_repair_defaults_to_cpu_encoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["repair", "--input", str(root / "input.mp4"), "--output", str(root / "output.mp4"), "--delete-registry", str(root / "delete.json"), "--evidence", str(root / "evidence.json")]
            with patch.object(sys, "argv", argv), patch.object(repair, "render", return_value={"deleted_frame_count": 1}) as render:
                self.assertEqual(0, repair.main())
            self.assertEqual("libx264", render.call_args.args[-1])

    def test_candidate_fingerprint_binds_native_frame_identity(self):
        base = {
            "source_sha256": "a" * 64, "processing_stage": "raw", "source_in": 1.0, "source_out": 2.0,
            "source_in_frame": 60, "speech_end_frame": 114, "source_out_frame_exclusive": 120,
            "source_fps_num": 60, "source_fps_den": 1, "candidate_text_sha256": "b" * 64,
        }
        shifted = dict(base, source_in_frame=61)
        self.assertNotEqual(runtime.candidate_fingerprint(base), runtime.candidate_fingerprint(shifted))

    def test_semantic_lock_copies_all_frame_fields(self):
        source = (ROOT / "scripts/v9_gate_runtime.py").read_text(encoding="utf-8")
        region = source[source.index("resolved.append("):source.index("for resolved_segment in resolved:")]
        for field in ("source_in_frame", "speech_end_frame", "source_out_frame_exclusive", "source_fps_num", "source_fps_den"):
            self.assertIn(field, region)

    def test_seconds_only_plan_is_rejected(self):
        plan = {"segments": [{"source_path": "x.mp4", "source_in": 1.0, "source_out": 2.0}]}
        errors = gate.validate_plan_value(plan)
        self.assertTrue(any("source_in_frame" in error for error in errors))

    def test_frame_bound_plan_passes(self):
        plan = {
            "segments": [
                {
                    "source_path": "x.mp4",
                    "source_in_frame": 60,
                    "speech_end_frame": 118,
                    "source_out_frame_exclusive": 120,
                    "source_fps_num": 60,
                    "source_fps_den": 1,
                    "speed": 1.0,
                }
            ]
        }
        self.assertEqual([], gate.validate_plan_value(plan))

    def test_interleaved_return_cannot_replay_source_frames(self):
        first = {
            "source_path": "first.mp4", "source_sha256": "a" * 64,
            "source_in_frame": 137, "speech_end_frame": 913,
            "source_out_frame_exclusive": 915, "source_fps_num": 60,
            "source_fps_den": 1,
        }
        middle = {
            **first, "source_path": "middle.mp4", "source_sha256": "b" * 64,
            "source_in_frame": 620, "speech_end_frame": 1147,
            "source_out_frame_exclusive": 1154,
        }
        returned = {
            **first, "source_path": "renamed-first.mp4",
            "source_in_frame": 912, "speech_end_frame": 1197,
            "source_out_frame_exclusive": 1202,
        }
        self.assertIn(
            "segment[2]:source_frames_replayed:segment[0]:912-914",
            gate.validate_plan_value({"segments": [first, middle, returned]}),
        )
        self.assertEqual([], gate.validate_plan_value({"segments": [first, middle]}))

    def test_forward_same_source_ranges_remain_valid(self):
        base = {
            "source_path": "source.mp4", "source_sha256": "a" * 64,
            "source_fps_num": 60, "source_fps_den": 1,
        }
        first = {**base, "source_in_frame": 100, "speech_end_frame": 148, "source_out_frame_exclusive": 150}
        adjacent = {**base, "source_in_frame": 148, "speech_end_frame": 198, "source_out_frame_exclusive": 200}
        other = {**base, "source_path": "other.mp4", "source_sha256": "b" * 64,
                 "source_in_frame": 0, "speech_end_frame": 48, "source_out_frame_exclusive": 50}
        later = {**base, "source_in_frame": 210, "speech_end_frame": 258, "source_out_frame_exclusive": 260}
        self.assertEqual([], gate.validate_plan_value({"segments": [first, adjacent, other, later]}))
        renamed_adjacent = {**adjacent, "source_path": "renamed-source.mp4"}
        self.assertTrue(any("source_frames_replayed" in error for error in
                            gate.validate_plan_value({"segments": [first, renamed_adjacent]})))
        different_speed = {**adjacent, "speed": 1.1}
        self.assertTrue(any("source_frames_replayed" in error for error in
                            gate.validate_plan_value({"segments": [first, different_speed]})))
        contained = {**base, "source_in_frame": 120, "speech_end_frame": 138, "source_out_frame_exclusive": 140}
        self.assertTrue(any("source_frames_replayed" in error for error in
                            gate.validate_plan_value({"segments": [first, contained]})))

    def test_source_return_is_marked_for_visual_review(self):
        segments = [
            {"source_path": "first.mp4", "source_sha256": "a" * 64},
            {"source_path": "middle.mp4", "source_sha256": "b" * 64},
            {"source_path": "renamed-first.mp4", "source_sha256": "a" * 64},
        ]
        self.assertIsNone(cut_smoke.prior_source_segment_index(segments, 1))
        self.assertEqual(1, cut_smoke.prior_source_segment_index(segments, 2))

    def test_frame_delete_registry_merges_adjacent_ranges(self):
        value = {"schema": repair.SCHEMA, "fps": 60, "ranges": [[10, 11], [12, 15], [30, 30]], "protected_spoken_ranges_60fps_inclusive": []}
        self.assertEqual([[10, 15], [30, 30]], repair.normalize_ranges(value, 100, 60))

    def test_time_delete_fields_are_rejected(self):
        value = {"schema": repair.SCHEMA, "fps": 60, "ranges": [[10, 11]], "protected_spoken_ranges_60fps_inclusive": [], "time_ranges": [[0.1, 0.2]]}
        with self.assertRaises(ValueError):
            repair.normalize_ranges(value, 100, 60)

    def test_missing_spoken_protection_is_rejected(self):
        value = {"schema": repair.SCHEMA, "fps": 60, "ranges": [[10, 11]]}
        with self.assertRaisesRegex(ValueError, "protected spoken"):
            repair.normalize_ranges(value, 100, 60)

    def test_delete_overlap_with_spoken_frames_is_rejected(self):
        value = {"schema": repair.SCHEMA, "fps": 60, "ranges": [[20, 30]], "protected_spoken_ranges_60fps_inclusive": [[25, 40]]}
        with self.assertRaisesRegex(ValueError, "overlaps protected spoken"):
            repair.normalize_ranges(value, 100, 60)

    def test_renderer_has_no_time_trim_fallback(self):
        source = (ROOT / "scripts/portable_frame_renderer.py").read_text(encoding="utf-8")
        self.assertNotIn(":v]trim=start=", source)
        self.assertIn("select='between(n", source)

    def test_portable_ledger_self_initializes(self):
        script = ROOT / "scripts/winky_ledger.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ledger"
            run = subprocess.run(
                [sys.executable, str(script), "--root", str(root), "checkpoint", "--task-id", "t", "--phase", "p", "--summary", "s"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertTrue((root / "ledger.sqlite").is_file())

    def test_adjacent_overlapping_same_source_is_coalesced(self):
        base = {"source_path": "x.mp4", "source_sha256": "a", "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0}
        segments = [
            {**base, "candidate_id": "tree", "source_in_frame": 403, "speech_end_frame": 752, "source_out_frame_exclusive": 756},
            {**base, "candidate_id": "temperature", "source_in_frame": 752, "speech_end_frame": 1144, "source_out_frame_exclusive": 1152},
        ]
        merged = renderer.coalesce_segments(segments)
        self.assertEqual(1, len(merged))
        self.assertEqual(403, merged[0]["source_in_frame"])
        self.assertEqual(1152, merged[0]["source_out_frame_exclusive"])
        self.assertEqual(["tree", "temperature"], merged[0]["_candidate_ids"])

    def test_noncontiguous_or_reverse_same_source_is_not_coalesced(self):
        base = {"source_path": "x.mp4", "source_sha256": "a", "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0}
        gap = [
            {**base, "candidate_id": "a", "source_in_frame": 100, "speech_end_frame": 150, "source_out_frame_exclusive": 160},
            {**base, "candidate_id": "b", "source_in_frame": 170, "speech_end_frame": 220, "source_out_frame_exclusive": 230},
        ]
        self.assertEqual(2, len(renderer.coalesce_segments(gap)))
        self.assertEqual(2, len(renderer.coalesce_segments(list(reversed(gap)))))

    def test_user_rejected_wz09_frame_range_cannot_be_renamed_and_reused(self):
        registry = json.loads((ROOT / "references/wuzimu-v20-invalid-intervals.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["entries"] if item["entry_id"] == "bad-wz09-short-head-13f")
        candidate = {"candidate_id": "renamed-safe-looking", "source_path": "能打三十个_Aco_终版.mp4", "source_sha256": entry["source_sha256"][0], "source_in": 6.716666666667, "source_out": 19.2, "source_in_frame": 403, "source_out_frame_exclusive": 1152}
        self.assertTrue(fail_closed.interval_hits(candidate, entry))


if __name__ == "__main__":
    unittest.main()
