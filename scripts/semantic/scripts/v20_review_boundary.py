from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from fractions import Fraction
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_sample(frame: int, fps_num: int, fps_den: int, sample_rate: int) -> int:
    return round(Fraction(frame * fps_den * sample_rate, fps_num))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--visual-scan", type=Path, required=True)
    parser.add_argument("--review-authority", type=Path, required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = read(args.bundle)
    alignment = read(args.alignment)
    visual = read(args.visual_scan)
    authority = read(args.review_authority)
    failures = []
    if bundle.get("schema") != "candidate-boundary-evidence/v260928" or bundle.get("generator_decision") != "pending_review" or bundle.get("decision") != "pending_review" or bundle.get("reviewer") is not None:
        failures.append("IMMUTABLE_PENDING_BUNDLE_REQUIRED")
    cid = str(bundle.get("candidate_id"))
    task_id = str(bundle.get("task_id"))
    source_sha = str(bundle.get("source_sha256", "")).lower()
    if authority.get("schema") != "video-montage-independent-review-authority/v260928" or authority.get("decision") != "authorized":
        failures.append("REVIEW_AUTHORITY_INVALID")
    reviewer = authority.get("reviewer") if isinstance(authority.get("reviewer"), dict) else {}
    if reviewer.get("role") != "independent_boundary_reviewer" or reviewer.get("review_id") != args.review_id or authority.get("task_id") != task_id or authority.get("candidate_id") != cid:
        failures.append("REVIEWER_PROVENANCE_MISMATCH")
    if reviewer.get("review_id") == (bundle.get("generator") or {}).get("id"):
        failures.append("GENERATOR_REVIEWER_NOT_INDEPENDENT")
    if visual.get("schema") != "video-montage-boundary-signal-scan/v260928" or visual.get("decision") != "pass" or visual.get("bundle_sha256") != sha(args.bundle) or visual.get("candidate_id") != cid:
        failures.append("VISUAL_SIGNAL_SCAN_FAILED")
    if alignment.get("schema") != "forced-boundary-alignment/v260928" or alignment.get("task_id") != task_id or alignment.get("candidate_id") != cid or str(alignment.get("source_sha256", "")).lower() != source_sha:
        failures.append("ALIGNMENT_BINDING_MISMATCH")
    sample_rate = int(alignment.get("sample_rate", 0) or 0)
    if sample_rate < 8000:
        failures.append("ALIGNMENT_SAMPLE_RATE_INVALID")
        sample_rate = 48000
    fps_num = int(bundle.get("source_fps_num", 0) or 0)
    fps_den = int(bundle.get("source_fps_den", 0) or 0)
    expected_start = expected_sample(int(bundle.get("source_in_frame", 0)), fps_num, fps_den, sample_rate) if fps_num and fps_den else -1
    expected_end = expected_sample(int(bundle.get("source_out_frame_exclusive", 0)), fps_num, fps_den, sample_rate) if fps_num and fps_den else -1
    selected_start = int(alignment.get("selected_start_sample", -2))
    selected_end = int(alignment.get("selected_end_sample_exclusive", -2))
    first_voiced = int(alignment.get("first_voiced_sample", -2))
    last_voiced = int(alignment.get("last_voiced_sample_exclusive", -2))
    if selected_start != expected_start or selected_end != expected_end or not selected_start <= first_voiced < last_voiced <= selected_end:
        failures.append("ALIGNMENT_SAMPLE_RANGE_MISMATCH")
    leading_samples = first_voiced - selected_start
    trailing_samples = selected_end - last_voiced
    minimum_gap = round(sample_rate * 0.04)
    if leading_samples < minimum_gap or trailing_samples < minimum_gap:
        failures.append("MEASURED_CLEAN_GAP_TOO_SHORT")
    claimed_leading = int(alignment.get("leading_clean_gap_samples", -1))
    claimed_trailing = int(alignment.get("trailing_clean_gap_samples", -1))
    if claimed_leading != leading_samples or claimed_trailing != trailing_samples:
        failures.append("CLAIMED_GAP_NOT_DERIVED_FROM_SAMPLES")
    maximum_preroll = expected_sample(3, fps_num, fps_den, sample_rate) if fps_num and fps_den else -1
    if maximum_preroll < 0 or leading_samples > maximum_preroll:
        failures.append("FIRST_EXPECTED_TOKEN_PREROLL_OVER_3_FRAMES")
    expected_token = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(alignment.get("expected_first_token") or ""))
    matched_token = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(alignment.get("first_aligned_token") or ""))
    if alignment.get("alignment_basis") != "expected_transcript_forced_alignment" or alignment.get("raw_waveform_energy_used_as_speech_start") is not False or not expected_token or expected_token != matched_token:
        failures.append("EXPECTED_FIRST_TOKEN_ALIGNMENT_REQUIRED")
    if alignment.get("leading_residual_tokens") or alignment.get("trailing_residual_tokens") or alignment.get("non_speech_transients"):
        failures.append("RESIDUAL_OR_TRANSIENT_DETECTED")
    if alignment.get("final_syllable_complete") is not True:
        failures.append("FINAL_SYLLABLE_INCOMPLETE")
    review = {
        "schema": "candidate-independent-boundary-review/v260928",
        "decision": "pass" if not failures else "reject",
        "task_id": task_id,
        "candidate_id": cid,
        "source_sha256": source_sha,
        "source_content_fingerprint": bundle.get("source_content_fingerprint"),
        "pending_bundle_path": str(args.bundle.resolve()),
        "pending_bundle_sha256": sha(args.bundle),
        "alignment_path": str(args.alignment.resolve()),
        "alignment_sha256": sha(args.alignment),
        "visual_signal_scan_path": str(args.visual_scan.resolve()),
        "visual_signal_scan_sha256": sha(args.visual_scan),
        "review_authority_path": str(args.review_authority.resolve()),
        "review_authority_sha256": sha(args.review_authority),
        "reviewer": {"role": "independent_boundary_reviewer", "review_id": args.review_id, "reviewed_at": datetime.now().astimezone().isoformat(timespec="microseconds")},
        "measurements": {"sample_rate": sample_rate, "selected_start_sample": selected_start, "selected_end_sample_exclusive": selected_end, "first_voiced_sample": first_voiced, "last_voiced_sample_exclusive": last_voiced, "leading_clean_gap_samples": leading_samples, "trailing_clean_gap_samples": trailing_samples},
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": review["decision"], "failures": failures, "output": str(args.output)}, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
