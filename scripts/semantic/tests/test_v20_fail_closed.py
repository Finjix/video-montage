from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from v20_fail_closed import audit_request_value


def write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V20FailClosedTests(unittest.TestCase):
    def make(self):
        temp = Path(tempfile.mkdtemp())
        task = temp / "task"
        task.mkdir()
        source = task / "source.mp4"
        source.write_bytes(b"source")
        candidate = {
            "candidate_id": "SAFE", "source_path": str(source), "source_sha256": sha(source),
            "candidate_text": "测试完整话轮",
            "source_content_fingerprint": "content-1", "source_in": 3.333, "source_out": 6.667,
            "source_in_frame": 100, "source_out_frame_exclusive": 200, "source_fps_num": 30, "source_fps_den": 1,
        }
        for field in ("actual_asr_path", "identity_evidence_path", "semantic_cluster_review_path", "opening_cast_evidence_path", "boundary_window_evidence_path"):
            path = write(task / f"{field}.json", {"task_id": "t", "candidate_id": "SAFE"})
            candidate[field] = str(path)
            candidate[field.replace("_path", "_sha256")] = sha(path)
        opening_context = write(task / "segment_start_action_context.json", {
            "schema": "segment-start-action-context-evidence/v260928", "task_id": "t", "candidate_id": "SAFE",
            "original_source_sha256": "a" * 64, "pre_context_seconds": 1.0, "opening_review_seconds": 1.5,
            "reviewed_original_frame_set_sha256": "b" * 64, "candidate_begins_inside_source_shot": False,
            "head_is_complete_independent_action": True, "orphan_head_shot": False,
            "user_rejected_action_match": False, "opening_action_complete": True, "opening_semantic_complete": True,
            "alignment_basis": "expected_transcript_forced_alignment", "raw_waveform_energy_used_as_speech_start": False,
            "first_aligned_token": "测", "sample_rate": 48000, "candidate_in_sample": 160000,
            "first_aligned_token_start_sample": 162400,
        })
        candidate["segment_start_action_context_evidence_path"] = str(opening_context)
        candidate["segment_start_action_context_evidence_sha256"] = sha(opening_context)
        frames = []
        for index in range(72):
            path = task / f"f{index}.png"
            path.write_bytes(bytes([index]))
            frames.append({"source_frame_number": 88 + index, "path": str(path), "sha256": sha(path)})
        pcm = task / "a.wav"
        pcm.write_bytes(b"pcm")
        bundle = {
            "schema": "candidate-boundary-evidence/v260928", "task_id": "t", "candidate_id": "SAFE",
            "source_sha256": sha(source), "source_content_fingerprint": "content-1",
            "source_in_frame": 100, "source_out_frame_exclusive": 200, "source_fps_num": 30, "source_fps_den": 1,
            "generator": {"id": "generator", "authority": "evidence_only"}, "generator_decision": "pending_review",
            "decision": "pending_review", "reviewer": None, "audio_alignment": {"decision": "pending_review"}, "dense_windows": {},
        }
        for side, boundary in (("in", 100), ("out", 200)):
            start = 64 if side == "in" else 164
            side_frames = []
            for index in range(72):
                original = frames[index]["path"]
                side_frames.append({"source_frame_number": start + index, "path": original, "sha256": sha(Path(original))})
            bundle["dense_windows"][side] = {
                "boundary_frame": boundary, "boundary_index": 36, "window_start_frame": start,
                "expected_frame_count": 72, "actual_frame_count": 72, "stable_run_required_frames": 60, "frames": side_frames,
                "pcm_path": str(pcm), "pcm_sha256": sha(pcm),
            }
        bundle_path = write(task / "bundle.json", bundle)
        candidate["v20_evidence_bundle_path"] = str(bundle_path)
        candidate["v20_evidence_bundle_sha256"] = sha(bundle_path)
        rate = 48000
        alignment = {
            "schema": "forced-boundary-alignment/v260928", "task_id": "t", "candidate_id": "SAFE", "source_sha256": sha(source),
            "sample_rate": rate, "selected_start_sample": 160000, "selected_end_sample_exclusive": 320000,
            "first_voiced_sample": 162400, "last_voiced_sample_exclusive": 317600,
            "leading_clean_gap_samples": 2400, "trailing_clean_gap_samples": 2400,
            "leading_residual_tokens": [], "trailing_residual_tokens": [], "non_speech_transients": [], "final_syllable_complete": True,
            "alignment_basis": "expected_transcript_forced_alignment", "raw_waveform_energy_used_as_speech_start": False,
            "first_aligned_token": "测", "first_aligned_token_start_sample": 162400,
        }
        alignment_path = write(task / "alignment.json", alignment)
        visual = {"schema": "video-montage-boundary-signal-scan/v260928", "decision": "pass", "bundle_sha256": sha(bundle_path), "candidate_id": "SAFE", "failures": []}
        visual_path = write(task / "visual.json", visual)
        authority = {"schema": "video-montage-independent-review-authority/v260928", "decision": "authorized", "task_id": "t", "candidate_id": "SAFE", "reviewer": {"role": "independent_boundary_reviewer", "review_id": "r1"}}
        authority_path = write(task / "authority.json", authority)
        review = {
            "schema": "candidate-independent-boundary-review/v260928", "decision": "pass", "task_id": "t", "candidate_id": "SAFE",
            "pending_bundle_sha256": sha(bundle_path), "alignment_sha256": sha(alignment_path), "visual_signal_scan_sha256": sha(visual_path),
            "review_authority_path": str(authority_path), "review_authority_sha256": sha(authority_path),
            "reviewer": {"role": "independent_boundary_reviewer", "review_id": "r1"},
        }
        review_path = write(task / "review.json", review)
        for field, path in (("v20_alignment_path", alignment_path), ("v20_visual_signal_scan_path", visual_path), ("v20_independent_review_path", review_path)):
            candidate[field] = str(path)
            candidate[field.replace("_path", "_sha256")] = sha(path)
        inventory = write(task / "inventory.json", {"candidates": [candidate]})
        work = write(task / "work_order.json", {"task_id": "t", "requested_outputs": 1})
        request = {"work_order_path": str(work), "candidate_inventory_path": str(inventory), "plans": [{"plan_id": "P1", "segments": [{"candidate_id": "SAFE"}]}]}
        return task, candidate, bundle, alignment, request

    def codes(self, request, registry=None):
        return [item["code"] for item in audit_request_value(request, registry or {"entries": []})]

    def save_candidate(self, candidate, request):
        write(Path(request["candidate_inventory_path"]), {"candidates": [candidate]})

    def test_clean_separate_independent_review_passes(self):
        *_, request = self.make()
        self.assertEqual([], audit_request_value(request, {"entries": []}))

    def test_mutated_generator_bundle_pass_rejected(self):
        _, candidate, bundle, _, request = self.make()
        bundle["decision"] = "pass"
        bundle["reviewer"] = {"role": "independent_boundary_reviewer", "review_id": "fake"}
        path = write(Path(candidate["v20_evidence_bundle_path"]), bundle)
        candidate["v20_evidence_bundle_sha256"] = sha(path)
        self.save_candidate(candidate, request)
        self.assertIn("V20_MUTATED_GENERATOR_BUNDLE", self.codes(request))

    def test_missing_separate_review_rejected(self):
        _, candidate, _, _, request = self.make()
        Path(candidate["v20_independent_review_path"]).unlink()
        self.assertIn("V20_CURRENT_TASK_EVIDENCE_REQUIRED", self.codes(request))

    def test_fixed_claimed_gap_cannot_override_samples(self):
        _, candidate, _, alignment, request = self.make()
        alignment["first_voiced_sample"] = alignment["selected_start_sample"] + 566
        alignment["leading_clean_gap_samples"] = 2400
        path = write(Path(candidate["v20_alignment_path"]), alignment)
        candidate["v20_alignment_sha256"] = sha(path)
        review_path = Path(candidate["v20_independent_review_path"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["alignment_sha256"] = sha(path)
        write(review_path, review)
        candidate["v20_independent_review_sha256"] = sha(review_path)
        self.save_candidate(candidate, request)
        codes = self.codes(request)
        self.assertIn("V20_CLAIMED_GAP_NOT_DERIVED", codes)
        self.assertIn("V20_MEASURED_CLEAN_GAP_TOO_SHORT", codes)

    def test_candidate_rename_cannot_bypass_invalid_interval(self):
        _, _, _, _, request = self.make()
        registry = {"entries": [{"entry_id": "bad", "source_name_contains": "source", "source_sha256": [], "start": 3.4, "end": 3.5, "guard": 0.35}]}
        codes = self.codes(request, registry)
        self.assertIn("V20_USER_INVALID_INTERVAL", codes)
        self.assertIn("V20_DEPENDENT_PLAN_INVALIDATED", codes)

    def test_stable_36_frame_orphan_opening_still_rejected(self):
        _, candidate, _, _, request = self.make()
        path = Path(candidate["segment_start_action_context_evidence_path"])
        evidence = json.loads(path.read_text(encoding="utf-8"))
        evidence["candidate_begins_inside_source_shot"] = True
        evidence["head_is_complete_independent_action"] = False
        evidence["orphan_head_shot"] = True
        write(path, evidence)
        candidate["segment_start_action_context_evidence_sha256"] = sha(path)
        self.save_candidate(candidate, request)
        codes = self.codes(request)
        self.assertIn("V20_SEGMENT_START_ORPHAN_ACTION_TAIL", codes)

    def test_raw_waveform_energy_cannot_define_speech_start(self):
        _, candidate, _, _, request = self.make()
        path = Path(candidate["segment_start_action_context_evidence_path"])
        evidence = json.loads(path.read_text(encoding="utf-8"))
        evidence["alignment_basis"] = "waveform_energy_threshold"
        evidence["raw_waveform_energy_used_as_speech_start"] = True
        write(path, evidence)
        candidate["segment_start_action_context_evidence_sha256"] = sha(path)
        self.save_candidate(candidate, request)
        self.assertIn("V20_SEGMENT_START_TEXT_ALIGNMENT_REQUIRED", self.codes(request))

    def test_first_aligned_token_must_match_expected_text(self):
        _, candidate, _, alignment, request = self.make()
        context_path = Path(candidate["segment_start_action_context_evidence_path"])
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["first_aligned_token"] = "嗨"
        write(context_path, context)
        candidate["segment_start_action_context_evidence_sha256"] = sha(context_path)
        alignment["first_aligned_token"] = "嗨"
        alignment_path = write(Path(candidate["v20_alignment_path"]), alignment)
        candidate["v20_alignment_sha256"] = sha(alignment_path)
        review_path = Path(candidate["v20_independent_review_path"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["alignment_sha256"] = sha(alignment_path)
        write(review_path, review)
        candidate["v20_independent_review_sha256"] = sha(review_path)
        self.save_candidate(candidate, request)
        codes = self.codes(request)
        self.assertIn("V20_SEGMENT_START_FIRST_TOKEN_MISMATCH", codes)
        self.assertIn("V20_FIRST_ALIGNED_TOKEN_MISMATCH", codes)

    def test_derived_clean_source_requires_hash_bound_original_lineage(self):
        task, candidate, _, _, request = self.make()
        derived = task / "clean.nut"
        derived.write_bytes(b"derived")
        candidate["source_path"] = str(derived)
        candidate["source_sha256"] = sha(derived)
        candidate["processing_stage"] = "speech-clean-repair"
        self.save_candidate(candidate, request)
        self.assertIn("V20_DERIVED_SOURCE_LINEAGE_REQUIRED", self.codes(request))

    def test_derived_fingerprint_cannot_hide_original_bad_frames(self):
        original_hash = "a" * 64
        candidate = {
            "candidate_id": "RENAMED_CLEAN",
            "source_path": "C:/task/clean/S08_FEATURE_AUDIO_CLEAN_V204.nut",
            "source_sha256": "b" * 64,
            "source_in_frame": 0,
            "source_out_frame_exclusive": 560,
            "source_content_fingerprint": f"v20.2.3:{original_hash}:[769, 1490]:selected:0-560:S08_FEATURE_AUDIO_CLEAN_V204",
        }
        entry = {"source_name_contains": "多场景玩游戏_场景3", "source_sha256": [original_hash], "source_frame_ranges_exclusive": [[768, 771]], "start": 12.8, "end": 12.85}
        from v20_fail_closed import interval_hits
        self.assertTrue(interval_hits(candidate, entry))


if __name__ == "__main__":
    unittest.main()
