from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from post_encode_evidence import frame_set_sha

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_post_encode.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PostEncodeReviewTests(unittest.TestCase):
    def run_case(self, mutate=None):
        root = Path(tempfile.mkdtemp())
        evidence = root / "e.json"
        signal = root / "s.json"
        findings = root / "f.json"
        authority = root / "a.json"
        alignment = root / "alignment.json"
        fresh_asr = root / "fresh-asr.json"
        output = root / "o.json"
        frames = []
        for number in range(72):
            frame = root / f"frame-{number}.jpg"
            frame.write_bytes(f"frame-{number}".encode())
            frames.append({"output_frame_number": number, "path": str(frame), "sha256": sha(frame)})
        pcm = root / "window.wav"
        pcm.write_bytes(b"pcm evidence")
        cut = {"plan_id": "P1", "cut_index": 1, "boundary_kind": "concat_cut", "expected_frame_count": 72, "actual_frame_count": 72, "frames": frames, "frame_set_sha256": frame_set_sha(frames), "pcm_path": str(pcm), "pcm_sha256": sha(pcm)}
        evidence.write_text(json.dumps({"schema": "ffmpeg-post-encode-evidence/v260928", "decision": "pending_independent_review", "complete_plan_scope": True, "delivery_manifest_sha256": "manifest", "cuts": [cut]}), encoding="utf-8")
        signal.write_text(json.dumps({"schema": "ffmpeg-boundary-signal-scan/v260928", "decision": "pass", "evidence_sha256": sha(evidence), "results": [], "failures": []}), encoding="utf-8")
        authority.write_text(json.dumps({"schema": "video-montage-independent-post-review-authority/v260928", "decision": "authorized", "reviewer": {"role": "independent_post_encode_reviewer", "review_id": "r"}}), encoding="utf-8")
        fresh_asr.write_text(json.dumps({"text": "就你们广告里刷到的"}), encoding="utf-8")
        alignment.write_text(json.dumps({"schema": "output-cut-alignment/v260928", "decision": "pass", "plan_id": "P1", "cut_index": 1, "boundary_kind": "concat_cut", "alignment_basis": "expected_transcript_forced_alignment", "raw_waveform_energy_used_as_speech_start": False, "expected_first_token": "就", "matched_first_token": "就", "first_token_preroll_frames": 2, "next_first_token_complete": True, "previous_final_token_complete": True, "leading_residual_tokens": [], "trailing_residual_tokens": [], "non_speech_transients": [], "banned_residual_tokens": [], "final_syllable_complete": True, "complete_spoken_turn": True, "tail_cut_mid_word": False, "fresh_asr_path": str(fresh_asr), "fresh_asr_sha256": sha(fresh_asr)}), encoding="utf-8")
        finding = {"plan_id": "P1", "cut_index": 1, "boundary_kind": "concat_cut", "reviewed_frame_count": 72, "reviewed_frame_set_sha256": cut["frame_set_sha256"], "all_frames_clean": True, "motion_continuity_clean": True, "pcm_clean": True, "forced_alignment_clean": True, "final_syllable_complete": True, "fresh_asr_clean": True, "complete_turn_clean": True, "leading_residual_tokens": [], "trailing_residual_tokens": [], "non_speech_transients": [], "alignment_path": str(alignment), "alignment_sha256": sha(alignment)}
        value = {"schema": "independent-post-encode-findings/v260928", "evidence_sha256": sha(evidence), "machine_signal_sha256": sha(signal), "cuts": [finding]}
        if mutate:
            mutate(value, finding, signal)
        findings.write_text(json.dumps(value), encoding="utf-8")
        run = subprocess.run([sys.executable, str(SCRIPT), "--evidence", str(evidence), "--signal-scan", str(signal), "--findings", str(findings), "--review-authority", str(authority), "--review-id", "r", "--output", str(output)], capture_output=True, text=True)
        return run.returncode, json.loads(output.read_text(encoding="utf-8"))

    def test_clean_hash_bound_review_passes(self):
        code, value = self.run_case()
        self.assertEqual(0, code)
        self.assertEqual("pass", value["decision"])

    def test_changed_frame_after_machine_scan_rejects(self):
        def mutate(_, finding, __):
            root = Path(finding["alignment_path"]).parent
            (root / "frame-0.jpg").write_bytes(b"changed")
        code, value = self.run_case(mutate)
        self.assertEqual(2, code)
        self.assertIn("FRAME_HASH_MISMATCH", [item["reason"] for item in value["failures"]])

    def test_changed_pcm_after_machine_scan_rejects(self):
        def mutate(_, finding, __):
            root = Path(finding["alignment_path"]).parent
            (root / "window.wav").write_bytes(b"changed")
        code, value = self.run_case(mutate)
        self.assertEqual(2, code)
        self.assertIn("PCM_HASH_MISMATCH", [item["reason"] for item in value["failures"]])

    def test_plain_booleans_without_frame_set_reject(self):
        code, value = self.run_case(lambda _, finding, __: finding.pop("reviewed_frame_set_sha256"))
        self.assertEqual(2, code)
        self.assertEqual("reject", value["decision"])

    def test_machine_signal_reject_cannot_be_overridden(self):
        def mutate(value, _, signal_path):
            signal_path.write_text(json.dumps({"schema": "ffmpeg-boundary-signal-scan/v260928", "decision": "reject", "evidence_sha256": value["evidence_sha256"], "results": [], "failures": ["short_run"]}), encoding="utf-8")
            value["machine_signal_sha256"] = sha(signal_path)
        code, value = self.run_case(mutate)
        self.assertEqual(2, code)
        self.assertEqual("reject", value["decision"])

    def test_excess_first_token_preroll_rejects(self):
        def mutate(_, finding, __):
            path = Path(finding["alignment_path"])
            alignment = json.loads(path.read_text(encoding="utf-8"))
            alignment["first_token_preroll_frames"] = 31
            path.write_text(json.dumps(alignment), encoding="utf-8")
            finding["alignment_sha256"] = sha(path)
        code, value = self.run_case(mutate)
        self.assertEqual(2, code)
        self.assertIn("output_alignment_failed", [item["reason"] for item in value["failures"]])

    def test_waveform_only_start_rejects(self):
        def mutate(_, finding, __):
            path = Path(finding["alignment_path"])
            alignment = json.loads(path.read_text(encoding="utf-8"))
            alignment["alignment_basis"] = "waveform_energy_threshold"
            alignment["raw_waveform_energy_used_as_speech_start"] = True
            path.write_text(json.dumps(alignment), encoding="utf-8")
            finding["alignment_sha256"] = sha(path)
        code, value = self.run_case(mutate)
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
