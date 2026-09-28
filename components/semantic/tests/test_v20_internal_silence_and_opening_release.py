from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from v20_frame_plan_gate import validate_plan_value
from v20_opening_visual_family_gate import audit


def write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InternalSilenceTests(unittest.TestCase):
    def make(self):
        root = Path(tempfile.mkdtemp())
        evidence = {
            "schema": "internal-silence-alignment-evidence/v260928", "decision": "pass", "source_sha256": "a" * 64,
            "deleted_source_frame_range_inclusive": [303, 398], "previous_token_end_frame": 294, "next_token_start_frame": 402,
            "alignment_basis": "expected_transcript_forced_alignment", "raw_waveform_energy_used_as_speech_start": False,
            "tokens_intersecting_deleted_range": [], "non_speech_transients_in_deleted_range": [],
            "protected_spoken_frame_ranges_inclusive": [[0, 294], [402, 740]],
            "previous_final_syllable_complete": True, "next_first_token_complete": True, "connector_antecedent_preserved": True,
        }
        evidence_path = write(root / "alignment.json", evidence)
        base = {"source_path": "source.nut", "source_sha256": "a" * 64, "speech_end_frame": 294, "source_fps_num": 60, "source_fps_den": 1, "speed": 1.0}
        left = {**base, "segment_id": "s1", "source_in_frame": 0, "source_out_frame_exclusive": 303, "text": "无尽冬日"}
        right = {**base, "segment_id": "s2", "source_in_frame": 399, "speech_end_frame": 740, "source_out_frame_exclusive": 756, "text": "但这一玩就觉得不一般"}
        plan = {
            "segments": [left, right],
            "transitions": [{"relation": "same_source_continuation_after_user_authorized_silence_removal"}],
            "internal_silence_compactions": [{
                "left_segment_id": "s1", "right_segment_id": "s2", "deleted_source_frame_range_inclusive": [303, 398],
                "user_authorized": True, "authorization_reference": "current user feedback", "alignment_evidence_path": str(evidence_path), "alignment_evidence_sha256": sha(evidence_path),
            }],
        }
        return root, evidence, evidence_path, plan

    def test_exact_frame_internal_silence_compaction_passes(self):
        root, _, _, plan = self.make()
        self.assertEqual([], validate_plan_value(plan, root))

    def test_deleted_range_with_token_rejects(self):
        root, evidence, path, plan = self.make()
        evidence["tokens_intersecting_deleted_range"] = ["但"]
        write(path, evidence)
        plan["internal_silence_compactions"][0]["alignment_evidence_sha256"] = sha(path)
        self.assertIn("internal_silence_compactions[0]:deleted_range_contains_token_or_transient", validate_plan_value(plan, root))

    def test_connector_without_antecedent_rejects(self):
        root, evidence, path, plan = self.make()
        evidence["connector_antecedent_preserved"] = False
        write(path, evidence)
        plan["internal_silence_compactions"][0]["alignment_evidence_sha256"] = sha(path)
        self.assertIn("internal_silence_compactions[0]:connector_antecedent_not_preserved", validate_plan_value(plan, root))


class OpeningReleaseTests(unittest.TestCase):
    def make(self, families):
        root = Path(tempfile.mkdtemp())
        results, items = [], []
        for index, family in enumerate(families, 1):
            plan_id = f"P{index}"
            output = root / f"{plan_id}.mp4"; output.write_bytes(f"video-{index}".encode())
            frames = []
            for frame_index in range(60):
                frame = root / plan_id / f"{frame_index:03d}.jpg"; frame.parent.mkdir(parents=True, exist_ok=True); frame.write_bytes(f"{plan_id}-{frame_index}".encode())
                frames.append({"path": str(frame), "sha256": sha(frame)})
            evidence = {
                "schema": "encoded-opening-frame-evidence/v260928", "decision": "pass", "plan_id": plan_id, "output_sha256": sha(output),
                "opening_visual_family_id": family, "reviewed_opening_frame_count": 60, "frames": frames,
                "first_frame_sha256": sha(Path(frames[0]["path"])), "opening_perceptual_signature": f"signature-{family}",
                "independent_visual_review": "pass", "celebrity_present_from_first_frame": True,
            }
            evidence_path = write(root / plan_id / "evidence.json", evidence)
            results.append({"plan_id": plan_id, "output_path": str(output), "output_sha256": sha(output)})
            items.append({"plan_id": plan_id, "opening_visual_family_id": family, "second_visual_family_id": f"second-{index}", "opening_frame_evidence_path": str(evidence_path), "opening_frame_evidence_sha256": sha(evidence_path)})
        manifest = write(root / "delivery.json", {"output_count": len(results), "results": results})
        request = write(root / "request.json", {"schema": "opening-visual-family-release-request/v260928", "delivery_manifest_path": str(manifest), "delivery_manifest_sha256": sha(manifest), "max_opening_visual_family_uses": 3, "max_opening_second_visual_pair_uses": 2, "items": items})
        return request

    def test_three_per_family_passes(self):
        self.assertEqual("pass", audit(self.make(["A", "A", "A", "B"]))["decision"])

    def test_four_per_family_rejects(self):
        report = audit(self.make(["A", "A", "A", "A"]))
        self.assertIn("OPENING_VISUAL_FAMILY_REUSE_EXCEEDED", {item["code"] for item in report["failures"]})


if __name__ == "__main__":
    unittest.main()
