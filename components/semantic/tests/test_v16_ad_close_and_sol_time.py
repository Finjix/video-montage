from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from v15_content_gate import REQUIRED_SOL_CHECKS, audit_plan, audit_sol_review


def contract() -> dict:
    return {
        "content_grounding_contract": {
            "product_terms": ["无尽冬日"],
            "product_asr_aliases": ["无尽冬日"],
            "require_product_attestation": False,
            "require_opening_cast_attestation": False,
            "require_boundary_attestation": False,
            "celebrity_person_ids": ["person:wuyue"],
            "hard_excluded_phrases": ["我也不装了我摊牌了我熔炉满级了"],
            "require_narrative_stage": True,
            "require_closing_payoff": True,
        }
    }


def segment(cid: str, text: str, duration: float, stage: int, function: str, close: bool = False) -> dict:
    purpose = {"function": function, "new_claim_ids": [cid], "non_redundant": True, "narrative_stage": stage}
    if close:
        purpose.update({"closing_payoff": True, "closing_quote": text})
    return {
        "candidate_id": cid,
        "text": text,
        "actual_asr_text": text,
        "duration": duration,
        "opening_frame_visible_person_ids": ["person:wuyue"],
        "purpose_contract": purpose,
        "boundary_cleanliness": {"decision": "pass", "listened_normal_speed": True, "leading_extra_tokens": [], "trailing_extra_tokens": []},
    }


def transition(left: dict, right: dict) -> dict:
    return {
        "relation": "cause_effect",
        "information_gain": "补充下一步具体结果",
        "content_evidence": {
            "from_quote": left["text"][:4],
            "to_quote": right["text"][:4],
            "new_information": "从玩法推进到结果",
            "entity_flow": "同一无尽冬日玩法",
            "state_flow": "叙事阶段向前推进",
        },
    }


class V18AdvertisingCloseTests(unittest.TestCase):
    def test_good_earned_close_passes(self) -> None:
        rows = [
            segment("a", "无尽冬日是冰雪生存游戏", 4.0, 0, "hook"),
            segment("b", "先砍树把资源拿来升级熔炉", 6.0, 2, "mechanism"),
            segment("c", "这还不是轻轻松松", 2.0, 4, "payoff", True),
        ]
        self.assertEqual([], audit_plan("good", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2])], contract()))

    def test_rejects_numeric_and_method_endings(self) -> None:
        numeric = [
            segment("a", "无尽冬日里先砍树升级熔炉", 8.0, 0, "hook"),
            segment("b", "这周我收了十个幸存者", 4.0, 3, "payoff", True),
        ]
        method = [
            segment("a", "无尽冬日是冰雪生存游戏", 4.0, 0, "hook"),
            segment("b", "记住在无尽冬日要想富先砍树把资源拿来升级熔炉", 8.0, 3, "payoff", True),
        ]
        numeric_codes = {item["code"] for item in audit_plan("numeric", numeric, [transition(numeric[0], numeric[1])], contract())}
        method_codes = {item["code"] for item in audit_plan("method", method, [transition(method[0], method[1])], contract())}
        self.assertIn("NUMERIC_STORY_BEAT_AS_ENDING", numeric_codes)
        self.assertIn("METHOD_ONLY_ENDING", method_codes)

    def test_rejects_hard_phrase_and_stage_regression(self) -> None:
        rows = [
            segment("a", "无尽冬日里我熔炉满级战力一个亿", 4.0, 3, "hook"),
            segment("b", "先砍树再升级熔炉", 5.0, 2, "mechanism"),
            segment("c", "我也不装了我摊牌了我熔炉满级了", 4.0, 4, "payoff", True),
        ]
        codes = {item["code"] for item in audit_plan("bad", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2])], contract())}
        self.assertIn("HARD_EXCLUDED_PHRASE", codes)
        self.assertIn("NARRATIVE_STAGE_REGRESSION", codes)

    def test_sol_review_time_and_provenance_are_hard(self) -> None:
        review = {
            "schema": "semantic-sol-output-review/v260928",
            "decision": "pass",
            "model_provenance": {"model": "gpt-5.6-sol", "execution_id": "test-sol-1"},
            "review_started_at": "2026-09-11T13:05:25+08:00",
            "review_completed_at": "2026-09-11T13:05:30+08:00",
            "audio_duration_seconds": 16.0,
            "normal_speed_listened_seconds": 5.0,
            "risk_codes": [],
            "content_checks": {key: True for key in REQUIRED_SOL_CHECKS},
            "segment_findings": [{"decision": "pass", "text": "这还不是轻轻松松", "purpose": "payoff"}],
        }
        codes = {item["code"] for item in audit_sol_review(review, "output:test")}
        self.assertIn("V18_NORMAL_SPEED_REVIEW_TIME_INVALID", codes)
        review["review_completed_at"] = "2026-09-11T13:05:42+08:00"
        review["normal_speed_listened_seconds"] = 16.0
        self.assertEqual([], audit_sol_review(review, "output:test"))


if __name__ == "__main__":
    unittest.main()
