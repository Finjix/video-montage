from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v9_gate_runtime", ROOT / "scripts" / "v9_gate_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runtime)
DELIVER_SPEC = importlib.util.spec_from_file_location("v9_deliver", ROOT / "scripts" / "v9_deliver.py")
deliver = importlib.util.module_from_spec(DELIVER_SPEC)
assert DELIVER_SPEC.loader
DELIVER_SPEC.loader.exec_module(deliver)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evidence(root: Path, name: str, text: str = "evidence") -> tuple[str, str]:
    path = root / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path.resolve()), runtime.sha_file(path)


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        skill_hash = runtime.sha_file(ROOT.parents[1] / "SKILL.md")
        self.work_order = {
            "schema": "semantic-video-work-order/v260928",
            "package_id": runtime.PACKAGE_ID,
            "package_skill_sha256": skill_hash,
            "batch_id": "batch-test",
            "requested_outputs": 1,
            "style_contract": {
                "profile": "performance_led_dialogue_montage/v260928",
                "target_duration_seconds": {"min": 20, "max": 30},
                "shot_count": {"min": 3, "max": 3},
                "performance_anchor_seconds": {"min": 8, "max": 16},
                "response_or_detail_seconds": {"min": 3, "max": 7},
                "min_performance_anchors": 1,
                "natural_tail_seconds": {"min": 0.08, "max": 0.12},
            },
            "output_spec": {"width": 1080, "height": 1920, "fps": 30, "video_codec": "h264", "audio_codec": "aac", "audio_rate": 48000},
            "policy": {
                "content_grounding_contract": {
                    "product_terms": ["无尽冬日"],
                    "product_asr_aliases": ["无尽冬日"],
                    "require_product_attestation": False,
                    "require_opening_cast_attestation": False,
                    "require_boundary_attestation": False,
                    "celebrity_person_ids": ["person:alpha", "person:beta", "person:gamma"]
                },
                "source_reuse": "unlimited",
                "max_single_speaker_share": 1.0,
                "max_candidate_uses": 2,
                "max_semantic_cluster_uses": 3,
                "max_ordered_plan_signature_uses": 1,
                "max_semantic_route_signature_uses": 1,
                "max_pairwise_candidate_overlap": 1,
                "max_pairwise_exact_text_overlap": 1,
                "max_pairwise_trigram_similarity": 1.0,
                "min_actual_asr_text_similarity": 0.9,
                "hard_excluded_connectors": [],
                "hard_excluded_phrases": [],
            },
        }
        self.work_order_path = root / "work_order.json"
        write_json(self.work_order_path, self.work_order)
        self.sources = []
        self.candidates = []
        specs = [
            ("c1", "performance_anchor", 0.0, 10.0, "person:alpha", "无尽冬日完整表演主句表达生存挑战和行动结果", "cluster:anchor"),
            ("c2", "response", 20.0, 25.0, "person:beta", "完整回应补充资源安排和执行细节", "cluster:detail"),
            ("c3", "payoff", 30.0, 35.0, "person:gamma", "完整收束说明最终收益和体验", "cluster:payoff"),
        ]
        for cid, role, source_in, source_out, person, text, cluster in specs:
            source_id = "source-" + cid
            extraction_path, extraction_hash = evidence(root, source_id + "-extraction.json")
            source_sha = runtime.sha_bytes(source_id.encode())
            self.sources.append({"source_id": source_id, "source_path": str(root / (source_id + ".mp4")), "source_sha256": source_sha, "processing_stage": "clean", "extraction_status": "exhausted", "extraction_evidence_path": extraction_path, "extraction_evidence_sha256": extraction_hash})
            asr_path, asr_hash = evidence(root, cid + "-asr.json", text)
            identity_path, identity_hash = evidence(root, cid + "-identity.json")
            cluster_path, cluster_hash = evidence(root, cid + "-cluster.json")
            frame_path, frame_hash = evidence(root, cid + "-frame.jpg")
            self.candidates.append({
                "candidate_id": cid, "candidate_status": "approved", "capacity_input_eligible": True,
                "source_id": source_id, "source_sha256": source_sha, "processing_stage": "clean",
                "source_path": str(root / (source_id + ".mp4")),
                "source_in": source_in, "source_out": source_out, "speech_end": source_out - 0.1,
                "source_in_frame": round(source_in * 30), "speech_end_frame": round((source_out - 0.1) * 30),
                "source_out_frame_exclusive": round(source_out * 30), "source_fps_num": 30, "source_fps_den": 1,
                "candidate_text": text, "candidate_text_sha256": runtime.normalized_text_sha(text),
                "actual_asr_text": text, "actual_asr_path": asr_path, "actual_asr_sha256": asr_hash,
                "first_voiced_source_time": source_in, "last_voiced_source_time": source_out - 0.1,
                "opening_completion_status": "complete", "closing_completion_status": "complete", "boundary_status": "pass",
                "primary_person_id": person, "visible_person_ids": [person], "boundary_open_person_id": person, "boundary_close_person_id": person,
                "opening_frame_visible_person_ids": [person],
                "boundary_cleanliness": {"decision": "pass", "listened_normal_speed": True, "leading_extra_tokens": [], "trailing_extra_tokens": []},
                "identity_evidence_path": identity_path, "identity_evidence_sha256": identity_hash,
                "semantic_cluster_id": cluster, "semantic_cluster_review_path": cluster_path, "semantic_cluster_review_sha256": cluster_hash,
                "candidate_type": "single_speaker_performance_anchor" if role == "performance_anchor" else "single_speaker_support",
                "allowed_editorial_roles": [role], "frames": {key: {"path": frame_path, "sha256": frame_hash} for key in ("in", "mid", "out")},
                "cta_status": "pass", "hard_text_status": "pass",
            })
        self.inventory_path = root / "candidate_inventory.json"
        self.analysis_path = root / "analysis_record.json"
        write_json(self.analysis_path, {"schema": "test-analysis-record/v260928"})
        self.review_paths = []
        for index in (1, 2):
            path, digest = evidence(root, f"transition-{index}.json")
            self.review_paths.append((path, digest))
        fps = {c["candidate_id"]: runtime.candidate_fingerprint(c) for c in self.candidates}
        self.plan = {
            "plan_id": "plan-1", "plan_revision": 1,
            "segments": [
                {"segment_order": 1, "candidate_id": "c1", "editorial_role": "performance_anchor", "purpose_contract": {"function": "product_hook", "new_claim_ids": ["product", "challenge"], "non_redundant": True}},
                {"segment_order": 2, "candidate_id": "c2", "editorial_role": "response", "purpose_contract": {"function": "mechanism", "new_claim_ids": ["execution"], "non_redundant": True}},
                {"segment_order": 3, "candidate_id": "c3", "editorial_role": "payoff", "purpose_contract": {"function": "payoff", "new_claim_ids": ["outcome"], "non_redundant": True}},
            ],
            "transitions": [
                {"from_candidate_id": "c1", "to_candidate_id": "c2", "from_candidate_fingerprint": fps["c1"], "to_candidate_fingerprint": fps["c2"], "plan_revision": 1, "decision": "pass", "reviewer_role": "independent_pre_render", "relation": "answer", "information_gain": "adds execution detail", "content_evidence": {"from_quote": "生存挑战", "to_quote": "资源安排", "new_information": "补充资源执行细节", "entity_flow": "同一游戏行动", "state_flow": "挑战推进到执行"}, "review_artifact_path": self.review_paths[0][0], "review_artifact_sha256": self.review_paths[0][1], "risk_codes": []},
                {"from_candidate_id": "c2", "to_candidate_id": "c3", "from_candidate_fingerprint": fps["c2"], "to_candidate_fingerprint": fps["c3"], "plan_revision": 1, "decision": "pass", "reviewer_role": "independent_pre_render", "relation": "payoff", "information_gain": "adds final outcome", "content_evidence": {"from_quote": "执行细节", "to_quote": "最终收益", "new_information": "说明执行后的体验收益", "entity_flow": "同一行动结果", "state_flow": "执行推进到收益"}, "review_artifact_path": self.review_paths[1][0], "review_artifact_sha256": self.review_paths[1][1], "risk_codes": []},
            ],
        }
        self.plans = [self.plan]
        self.witness_path = root / "witness.json"
        self.request_path = root / "request.json"
        self.inventory_status = "exhausted"
        self.replacement_plan_capacity = None
        self.refresh()

    def refresh(self) -> None:
        write_json(self.work_order_path, self.work_order)
        work_hash = runtime.sha_file(self.work_order_path)
        inventory = {"schema": "semantic-candidate-inventory/v260928", "batch_id": "batch-test", "work_order_sha256": work_hash, "status": self.inventory_status, "sources": self.sources, "candidates": self.candidates}
        write_json(self.inventory_path, inventory)
        inventory_hash = runtime.sha_file(self.inventory_path)
        witness = {"schema": "video-montage-unique-plan-search-witness/v260928", "complete": True, "work_order_sha256": work_hash, "candidate_inventory_sha256": inventory_hash, "unique_plan_capacity": len(self.plans), "plan_ids": [plan["plan_id"] for plan in self.plans]}
        if self.replacement_plan_capacity is not None:
            witness["replacement_plan_capacity"] = self.replacement_plan_capacity
        write_json(self.witness_path, witness)
        request = {"schema": "semantic-batch-lock-request/v260928", "package_id": runtime.PACKAGE_ID, "batch_id": "batch-test", "analysis_snapshot_id": "snapshot-test", "analysis_record_path": str(self.analysis_path.resolve()), "analysis_record_sha256": runtime.sha_file(self.analysis_path), "work_order_path": str(self.work_order_path.resolve()), "work_order_sha256": work_hash, "candidate_inventory_path": str(self.inventory_path.resolve()), "candidate_inventory_sha256": inventory_hash, "unique_plan_witness_path": str(self.witness_path.resolve()), "unique_plan_witness_sha256": runtime.sha_file(self.witness_path), "plans": self.plans}
        write_json(self.request_path, request)


class V7RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def run_gate(self, suffix: str):
        return runtime.validate_and_lock(self.fixture.request_path, self.root / ("out-" + suffix))

    def test_legacy_gate_requires_v20_evidence(self):
        passed, report, index_path = self.run_gate("legacy")
        self.assertFalse(passed)
        self.assertIsNone(index_path)
        self.assertIn("V20_CONTENT_FINGERPRINT_REQUIRED", {item["code"] for item in report["failures"]})

    def test_missing_asr_hash_rejected(self):
        self.fixture.candidates[0].pop("actual_asr_sha256")
        self.fixture.refresh()
        passed, report, _ = self.run_gate("missing-asr")
        self.assertFalse(passed)
        self.assertIn("MISSING_REQUIRED_TRACEABILITY", {item["code"] for item in report["failures"]})

    def test_plan_id_cannot_escape_lock_directory(self):
        self.fixture.plans[0]["plan_id"] = "../escape"
        self.fixture.refresh()
        passed, report, _ = self.run_gate("unsafe-id")
        self.assertFalse(passed)
        self.assertIn("INVALID_PLAN_ID", {item["code"] for item in report["failures"]})
        self.assertFalse((self.root / "escape.json").exists())

    def test_case_only_plan_ids_cannot_lock_colliding_files(self):
        second = copy.deepcopy(self.fixture.plan)
        second["plan_id"] = self.fixture.plan["plan_id"].upper()
        self.fixture.plans.append(second)
        self.fixture.work_order["requested_outputs"] = 2
        self.fixture.refresh()
        passed, report, index_path = self.run_gate("case-collision")
        self.assertFalse(passed)
        self.assertIsNone(index_path)
        self.assertIn("DUPLICATE_PLAN_ID", {row["code"] for row in report["failures"]})

    def test_same_person_boundary_rejected(self):
        self.fixture.candidates[1]["boundary_open_person_id"] = "person:alpha"
        self.fixture.candidates[1]["visible_person_ids"].append("person:alpha")
        self.fixture.refresh()
        passed, report, _ = self.run_gate("same-person")
        self.assertFalse(passed)
        self.assertIn("ADJACENT_SAME_VISIBLE_PERSON", {item["code"] for item in report["failures"]})

    def test_prelock_rejects_interleaved_source_frame_replay(self):
        first, _, returned = self.fixture.candidates
        returned.update({
            "source_id": first["source_id"],
            "source_path": first["source_path"],
            "source_sha256": first["source_sha256"],
            "source_in": 5.0, "speech_end": 9.9, "source_out": 10.0,
            "source_in_frame": 150, "speech_end_frame": 297,
            "source_out_frame_exclusive": 300,
            "first_voiced_source_time": 5.0, "last_voiced_source_time": 9.9,
        })
        self.fixture.plan["transitions"][1]["to_candidate_fingerprint"] = runtime.candidate_fingerprint(returned)
        self.fixture.refresh()
        passed, report, _ = self.run_gate("source-frame-replay")
        self.assertFalse(passed)
        self.assertIn("SOURCE_FRAME_REPLAY", {item["code"] for item in report["failures"]})

    def test_duplicate_plan_sequence_rejected(self):
        duplicate = copy.deepcopy(self.fixture.plan)
        duplicate["plan_id"] = "plan-2"
        self.fixture.plans.append(duplicate)
        self.fixture.work_order["requested_outputs"] = 2
        self.fixture.work_order["policy"]["max_semantic_route_signature_uses"] = 2
        self.fixture.refresh()
        passed, report, _ = self.run_gate("duplicate")
        self.assertFalse(passed)
        self.assertIn("DUPLICATE_ORDERED_PLAN_SEQUENCE", {item["code"] for item in report["failures"]})

    def test_stale_transition_binding_rejected(self):
        self.fixture.plan["transitions"][0]["from_candidate_fingerprint"] = "0" * 64
        self.fixture.refresh()
        passed, report, _ = self.run_gate("stale-transition")
        self.assertFalse(passed)
        self.assertIn("STALE_TRANSITION_BINDING", {item["code"] for item in report["failures"]})

    def test_support_duration_rejected(self):
        self.fixture.candidates[1]["source_out"] = 28.0
        self.fixture.candidates[1]["speech_end"] = 27.9
        self.fixture.candidates[1]["last_voiced_source_time"] = 27.9
        self.fixture.candidates[1]["candidate_text_sha256"] = runtime.normalized_text_sha(self.fixture.candidates[1]["candidate_text"])
        new_fp = runtime.candidate_fingerprint(self.fixture.candidates[1])
        self.fixture.plan["transitions"][0]["to_candidate_fingerprint"] = new_fp
        self.fixture.plan["transitions"][1]["from_candidate_fingerprint"] = new_fp
        self.fixture.refresh()
        passed, report, _ = self.run_gate("duration")
        self.assertFalse(passed)
        self.assertIn("SEGMENT_DURATION_VIOLATION", {item["code"] for item in report["failures"]})

    def test_work_order_hash_drift_rejected(self):
        request = runtime.load_json(self.fixture.request_path)
        request["work_order_sha256"] = "0" * 64
        write_json(self.fixture.request_path, request)
        passed, report, _ = self.run_gate("work-order-drift")
        self.assertFalse(passed)
        self.assertIn("WORK_ORDER_HASH_MISMATCH", {item["code"] for item in report["failures"]})

    def test_state_machine_blocks_missing_artifact(self):
        state = self.root / "state.json"
        runtime.init_state(state, "task-test", "unit-test", self.fixture.work_order_path)
        runtime.transition_state(state, "INVENTORY", "unit-test", {})
        with self.assertRaises(ValueError):
            runtime.transition_state(state, "CANDIDATE_AUDITED", "unit-test", {})


if __name__ == "__main__":
    unittest.main()
