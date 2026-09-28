from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def token(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--signal-scan", type=Path, required=True)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--review-authority", type=Path, required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = read(args.evidence)
    signal = read(args.signal_scan)
    findings = read(args.findings)
    authority = read(args.review_authority)
    failures = []
    if evidence.get("schema") != "ffmpeg-post-encode-evidence/v260928" or evidence.get("decision") != "pending_independent_review" or evidence.get("complete_plan_scope") is not True:
        failures.append({"scope": "batch", "reason": "immutable_pending_evidence_required"})
    if signal.get("schema") != "ffmpeg-boundary-signal-scan/v260928" or signal.get("decision") != "pass" or signal.get("evidence_sha256") != sha(args.evidence):
        failures.append({"scope": "batch", "reason": "machine_signal_gate"})
    reviewer = authority.get("reviewer") if isinstance(authority.get("reviewer"), dict) else {}
    if authority.get("schema") != "video-montage-independent-post-review-authority/v260928" or authority.get("decision") != "authorized" or reviewer.get("role") != "independent_post_encode_reviewer" or reviewer.get("review_id") != args.review_id:
        failures.append({"scope": "batch", "reason": "review_authority"})
    if findings.get("schema") != "independent-post-encode-findings/v260928" or findings.get("evidence_sha256") != sha(args.evidence) or findings.get("machine_signal_sha256") != sha(args.signal_scan):
        failures.append({"scope": "batch", "reason": "findings_binding"})
    by_key = {(item["plan_id"], int(item["cut_index"])): item for item in findings.get("cuts", [])}
    required = ("all_frames_clean", "motion_continuity_clean", "pcm_clean", "forced_alignment_clean", "final_syllable_complete", "fresh_asr_clean", "complete_turn_clean")
    for cut in evidence.get("cuts", []):
        key = (cut["plan_id"], int(cut["cut_index"]))
        finding = by_key.get(key, {})
        if cut.get("actual_frame_count") != cut.get("expected_frame_count") or len(cut.get("frames", [])) != cut.get("expected_frame_count"):
            failures.append({"key": key, "reason": "dense_frame_count"})
        if finding.get("boundary_kind") != cut.get("boundary_kind"):
            failures.append({"key": key, "reason": "boundary_kind_mismatch"})
        if finding.get("reviewed_frame_count") != cut.get("expected_frame_count") or finding.get("reviewed_frame_set_sha256") != cut.get("frame_set_sha256"):
            failures.append({"key": key, "reason": "original_frame_set_not_reviewed"})
        if not all(finding.get(name) is True for name in required):
            failures.append({"key": key, "reason": "independent_finding"})
        if finding.get("leading_residual_tokens") or finding.get("trailing_residual_tokens") or finding.get("non_speech_transients"):
            failures.append({"key": key, "reason": "residual_or_transient"})
        alignment_path = Path(str(finding.get("alignment_path", "")))
        if not alignment_path.is_file() or sha(alignment_path) != str(finding.get("alignment_sha256", "")).lower():
            failures.append({"key": key, "reason": "output_alignment_missing"})
        else:
            alignment = read(alignment_path)
            fresh_asr_path = Path(str(alignment.get("fresh_asr_path", "")))
            base_invalid = (
                alignment.get("schema") != "output-cut-alignment/v260928"
                or alignment.get("decision") != "pass"
                or alignment.get("plan_id") != key[0]
                or int(alignment.get("cut_index", -1)) != key[1]
                or alignment.get("boundary_kind") != finding.get("boundary_kind")
                or alignment.get("alignment_basis") != "expected_transcript_forced_alignment"
                or alignment.get("raw_waveform_energy_used_as_speech_start") is not False
                or alignment.get("leading_residual_tokens")
                or alignment.get("trailing_residual_tokens")
                or alignment.get("non_speech_transients")
                or alignment.get("banned_residual_tokens")
                or alignment.get("final_syllable_complete") is not True
                or alignment.get("complete_spoken_turn") is not True
                or alignment.get("tail_cut_mid_word") is not False
                or not fresh_asr_path.is_file()
                or sha(fresh_asr_path) != str(alignment.get("fresh_asr_sha256", "")).lower()
            )
            speech_invalid = False
            if cut.get("boundary_kind") in {"output_start", "concat_cut"}:
                expected, matched = token(alignment.get("expected_first_token")), token(alignment.get("matched_first_token"))
                preroll = int(alignment.get("first_token_preroll_frames", -1))
                speech_invalid = not expected or expected != matched or not 0 <= preroll <= 3 or alignment.get("next_first_token_complete") is not True
            if cut.get("boundary_kind") in {"concat_cut", "output_end"} and alignment.get("previous_final_token_complete") is not True:
                speech_invalid = True
            if base_invalid or speech_invalid:
                failures.append({"key": key, "reason": "output_alignment_failed"})
    report = {
        "schema": "ffmpeg-post-encode-qc/v260928",
        "decision": "pass" if not failures else "reject",
        "reviewer": {"role": "independent_post_encode_reviewer", "review_id": args.review_id, "reviewed_at": datetime.now().astimezone().isoformat(timespec="microseconds")},
        "delivery_manifest_sha256": evidence["delivery_manifest_sha256"],
        "exact_cut_evidence": {"path": str(args.evidence.resolve()), "sha256": sha(args.evidence)},
        "machine_signal_gate": {"path": str(args.signal_scan.resolve()), "sha256": sha(args.signal_scan), "decision": signal.get("decision")},
        "independent_findings": {"path": str(args.findings.resolve()), "sha256": sha(args.findings)},
        "review_authority": {"path": str(args.review_authority.resolve()), "sha256": sha(args.review_authority)},
        "checked_cuts": len(evidence.get("cuts", [])),
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
