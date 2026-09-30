from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


base = load_module("v11_test_fixture", ROOT / "tests" / "test_v7_runtime.py")
runtime = base.runtime


class V11LiveActionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = base.Fixture(self.root)
        self.scope_paths: dict[str, Path] = {}

    def tearDown(self):
        self.temp.cleanup()

    def write_evidence(self, name: str, value: dict) -> tuple[str, str]:
        path = self.root / "evidence" / name
        base.write_json(path, value)
        return str(path.resolve()), runtime.sha_file(path)

    def enable_live_action_profile(self, same_person: bool = False, short_reaction: bool = False) -> None:
        fixture = self.fixture
        fixture.work_order["style_contract"] = {
            "profile": "celebrity_live_action_opening/v260928",
            "target_duration_seconds": {"min": 12.0, "max": 22.0},
            "shot_count": {"min": 3, "max": 7},
            "performance_anchor_seconds": {"min": 4.0, "max": 14.0},
            "response_or_detail_seconds": {"min": 2.0, "max": 7.0},
            "short_complete_reaction_seconds": {"min": 0.8, "max": 3.0},
            "max_short_complete_reactions": 2,
            "min_performance_anchors": 1,
            "natural_tail_seconds": {"min": 0.02, "max": 0.25},
            "preferred_natural_tail_seconds": {"min": 0.08, "max": 0.12},
        }
        fixture.work_order["policy"].update({
            "content_scope": "celebrity_live_action_opening",
            "inventory_mode": "witness_sufficient",
            "min_replacement_plan_margin": 0.2,
            "min_actual_asr_text_similarity": 0.82,
            "max_single_speaker_share": None,
            "same_person_adjacency": "allow_with_visual_change_and_information_gain",
            "allowed_same_person_visual_changes": ["scene", "shot_scale", "camera_angle", "costume", "action", "performance_state"],
            "allowed_transition_relations": ["question_answer", "claim_support", "payoff"],
            "require_information_gain": True,
            "min_information_gain_chars": 4,
            "require_proposition_progress": True,
            "allowed_speaker_return_reasons": ["answer", "counterpoint", "round_continuation", "payoff", "escalation"],
            "enforce_live_action_scope_evidence": True,
            "enforce_structured_identity_evidence": True,
        })
        fixture.inventory_status = "witness_sufficient"
        fixture.replacement_plan_capacity = 1
        for source in fixture.sources:
            source["extraction_status"] = "partial"
        if same_person:
            candidate = fixture.candidates[1]
            candidate["primary_person_id"] = "person:alpha"
            candidate["visible_person_ids"] = ["person:alpha"]
            candidate["boundary_open_person_id"] = "person:alpha"
            candidate["boundary_close_person_id"] = "person:alpha"
        if short_reaction:
            candidate = fixture.candidates[1]
            candidate["source_out"] = 22.0
            candidate["speech_end"] = 21.9
            candidate["last_voiced_source_time"] = 21.9
            candidate["candidate_type"] = "complete_short_reaction"
            candidate["allowed_editorial_roles"] = ["short_reaction"]
            fixture.plan["segments"][1]["editorial_role"] = "short_reaction"
        for candidate in fixture.candidates:
            identity = {
                "schema": "canonical-person-identity-evidence/v260928",
                "candidate_id": candidate["candidate_id"],
                "primary_person_id": candidate["primary_person_id"],
                "boundary_open_person_id": candidate["boundary_open_person_id"],
                "boundary_close_person_id": candidate["boundary_close_person_id"],
                "decision": "pass",
            }
            candidate["identity_evidence_path"], candidate["identity_evidence_sha256"] = self.write_evidence(candidate["candidate_id"] + "-identity-v11.json", identity)
            frames = []
            for position in ("in", "mid", "out"):
                frame = candidate["frames"][position]
                frames.append({
                    "position": position,
                    "dominant_domain": "live_action_primary",
                    "dominant_person_id": candidate["primary_person_id"],
                    "fullscreen_game": False,
                    "end_card": False,
                    "visual_cta": False,
                    "frame_path": frame["path"],
                    "frame_sha256": frame["sha256"],
                })
            scope = {
                "schema": "celebrity-live-action-scope-evidence/v260928",
                "candidate_id": candidate["candidate_id"],
                "source_sha256": candidate["source_sha256"],
                "source_in": candidate["source_in"],
                "source_out": candidate["source_out"],
                "first_fullscreen_game_time": None,
                "frames": frames,
                "decision": "pass",
            }
            scope_path, scope_hash = self.write_evidence(candidate["candidate_id"] + "-live-scope.json", scope)
            candidate["live_action_scope_evidence_path"] = scope_path
            candidate["live_action_scope_evidence_sha256"] = scope_hash
            self.scope_paths[candidate["candidate_id"]] = Path(scope_path)
            cta = {
                "schema": "candidate-cta-evidence/v260928",
                "candidate_id": candidate["candidate_id"],
                "spoken_cta": False,
                "visual_cta": False,
                "end_card": False,
                "decision": "pass",
            }
            candidate["cta_evidence_path"], candidate["cta_evidence_sha256"] = self.write_evidence(candidate["candidate_id"] + "-cta.json", cta)
        fingerprints = {candidate["candidate_id"]: runtime.candidate_fingerprint(candidate) for candidate in fixture.candidates}
        for index, transition in enumerate(fixture.plan["transitions"]):
            left = fixture.plan["segments"][index]["candidate_id"]
            right = fixture.plan["segments"][index + 1]["candidate_id"]
            transition["from_candidate_fingerprint"] = fingerprints[left]
            transition["to_candidate_fingerprint"] = fingerprints[right]
            transition["relation"] = "question_answer" if index == 0 else "payoff"
            transition["information_gain"] = "adds a concrete new claim" if index == 0 else "adds the closing benefit"
            transition["from_proposition_id"] = f"proposition:{index + 1}"
            transition["to_proposition_id"] = f"proposition:{index + 2}"
        if same_person:
            path, digest = base.evidence(self.root, "same-person-visual-change.json")
            fixture.plan["transitions"][0]["visual_change"] = {
                "decision": "pass",
                "change_types": ["scene", "performance_state"],
                "evidence_path": path,
                "evidence_sha256": digest,
            }
        fixture.refresh()

    def run_gate(self, suffix: str):
        output = self.root / suffix
        return runtime.validate_and_lock(self.fixture.request_path, output)

    def test_witness_partial_single_star_and_same_person_requires_v20_evidence(self):
        self.enable_live_action_profile(same_person=True)
        passed, report, _ = self.run_gate("pass")
        self.assertFalse(passed)
        self.assertIn("V20_CONTENT_FINGERPRINT_REQUIRED", {item["code"] for item in report["failures"]})

    def test_same_person_without_visual_change_rejected(self):
        self.enable_live_action_profile(same_person=True)
        self.fixture.plan["transitions"][0].pop("visual_change")
        self.fixture.refresh()
        passed, report, _ = self.run_gate("same-person-missing")
        self.assertFalse(passed)
        self.assertIn("SAME_PERSON_VISUAL_CHANGE_MISSING", {item["code"] for item in report["failures"]})

    def test_fullscreen_game_scope_rejected(self):
        self.enable_live_action_profile()
        candidate = self.fixture.candidates[0]
        path = self.scope_paths[candidate["candidate_id"]]
        scope = runtime.load_json(path)
        scope["frames"][1]["dominant_domain"] = "fullscreen_game"
        scope["frames"][1]["fullscreen_game"] = True
        base.write_json(path, scope)
        candidate["live_action_scope_evidence_sha256"] = runtime.sha_file(path)
        self.fixture.refresh()
        passed, report, _ = self.run_gate("gameplay")
        self.assertFalse(passed)
        self.assertIn("NON_LIVE_ACTION_CONTENT", {item["code"] for item in report["failures"]})

    def test_unlisted_semantic_relation_rejected(self):
        self.enable_live_action_profile()
        self.fixture.plan["transitions"][0]["relation"] = "same_product_keyword"
        self.fixture.refresh()
        passed, report, _ = self.run_gate("relation")
        self.assertFalse(passed)
        self.assertIn("INVALID_SEMANTIC_RELATION", {item["code"] for item in report["failures"]})

    def test_complete_short_reaction_requires_v20_evidence(self):
        self.enable_live_action_profile(short_reaction=True)
        passed, report, _ = self.run_gate("short-reaction")
        self.assertFalse(passed)
        self.assertIn("V20_CONTENT_FINGERPRINT_REQUIRED", {item["code"] for item in report["failures"]})

    def test_speaker_return_requires_reason(self):
        candidate = self.fixture.candidates[2]
        candidate["primary_person_id"] = "person:alpha"
        candidate["visible_person_ids"] = ["person:alpha"]
        candidate["boundary_open_person_id"] = "person:alpha"
        candidate["boundary_close_person_id"] = "person:alpha"
        self.enable_live_action_profile()
        passed, report, _ = self.run_gate("return-missing")
        self.assertFalse(passed)
        self.assertIn("UNJUSTIFIED_SPEAKER_RETURN", {item["code"] for item in report["failures"]})

    def test_speaker_return_with_payoff_reason_requires_v20_evidence(self):
        candidate = self.fixture.candidates[2]
        candidate["primary_person_id"] = "person:alpha"
        candidate["visible_person_ids"] = ["person:alpha"]
        candidate["boundary_open_person_id"] = "person:alpha"
        candidate["boundary_close_person_id"] = "person:alpha"
        self.enable_live_action_profile()
        self.fixture.plan["transitions"][1]["speaker_return_reason"] = "payoff"
        self.fixture.refresh()
        passed, report, _ = self.run_gate("return-pass")
        self.assertFalse(passed)
        self.assertIn("V20_CONTENT_FINGERPRINT_REQUIRED", {item["code"] for item in report["failures"]})


if __name__ == "__main__":
    unittest.main()
