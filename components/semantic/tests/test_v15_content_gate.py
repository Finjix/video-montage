from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from v15_content_gate import audit_plan, _audit_reusable_semantic_sequence


def policy() -> dict:
    return {
        "content_scope": "celebrity_live_action_opening",
        "content_grounding_contract": {
            "product_terms": ["无尽冬日"],
            "product_asr_aliases": ["无尽冬日"],
            "require_product_attestation": False,
            "require_opening_cast_attestation": False,
            "require_boundary_attestation": False,
            "celebrity_person_ids": ["person:wuyue"],
        },
    }


def segment(cid: str, text: str, duration: float, claims: list[str], *, visible: list[str] | None = None) -> dict:
    return {
        "candidate_id": cid,
        "text": text,
        "actual_asr_text": text,
        "duration": duration,
        "opening_frame_visible_person_ids": visible or ["person:wuyue"],
        "visible_person_ids": visible or ["person:wuyue"],
        "purpose_contract": {"function": "information", "new_claim_ids": claims, "non_redundant": True},
        "boundary_cleanliness": {
            "decision": "pass",
            "listened_normal_speed": True,
            "leading_extra_tokens": [],
            "trailing_extra_tokens": [],
        },
    }


def transition(left: dict, right: dict, relation: str = "claim_support") -> dict:
    return {
        "relation": relation,
        "information_gain": "补充具体玩法信息",
        "content_evidence": {
            "from_quote": left["text"][:4],
            "to_quote": right["text"][:4],
            "new_information": "说明新的具体步骤",
            "entity_flow": "同一游戏与幸存者",
            "state_flow": "从产品介绍推进到玩法",
        },
    }


class ContentGateTests(unittest.TestCase):
    def v19_policy(self) -> dict:
        contract = json.loads((ROOT / "references" / "wuzimu-v19-feedback-contract.json").read_text(encoding="utf-8"))
        contract["require_product_attestation"] = False
        contract["require_opening_cast_attestation"] = False
        contract["require_boundary_attestation"] = False
        contract["require_boundary_window_attestation"] = False
        return {
            "content_scope": "celebrity_live_action_opening",
            "content_grounding_contract": contract,
            "visual_shots": [
                {"duration": 8.0},
                {"duration": 8.0},
                {"duration": 8.0}
            ]
        }

    def test_v19_allows_grounded_cta_and_balanced_long_plan(self) -> None:
        rows = [
            segment("a19", "无尽冬日可是全球三亿人都在玩的冰雪游戏", 8.0, ["product"]),
            segment("b19", "我开局就砍树，再升熔炉，战力蹭蹭涨", 8.0, ["tree", "furnace"]),
            segment("c19", "无尽冬日无需下载点击即玩，现在输入5月666、5月888，上线就能领海量福利", 8.0, ["cta", "benefit"]),
        ]
        rows[1]["purpose_contract"]["function"] = "proof"
        rows[-1]["purpose_contract"].update({"function": "cta", "closing_payoff": True, "closing_quote": rows[-1]["text"]})
        for stage, row in enumerate(rows):
            row["purpose_contract"]["narrative_stage"] = stage
        failures = audit_plan("good-v19", rows, [transition(rows[0], rows[1], "cause_effect"), transition(rows[1], rows[2], "benefit")], self.v19_policy())
        self.assertEqual([], failures)

    def test_v19_rejects_known_feedback_failures(self) -> None:
        rows = [
            segment("bad-open", "我是无尽冬日明星玩家吴樾", 2.5, ["product"]),
            segment("bad-question", "你们这周收了几个幸存者啊", 5.0, ["question"], visible=["person:supporting"]),
            segment("bad-close", "so easy", 1.7, ["echo"]),
        ]
        rows[-1]["purpose_contract"].update({"function": "payoff", "closing_payoff": True, "closing_quote": "so easy"})
        for stage, row in enumerate(rows):
            row["purpose_contract"]["narrative_stage"] = stage
        policy_value = self.v19_policy()
        policy_value["visual_shots"] = [{"duration": 2.5}, {"duration": 5.0}, {"duration": 1.7}]
        codes = {item["code"] for item in audit_plan("bad-v19", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2])], policy_value)}
        self.assertIn("FORBIDDEN_STANDALONE_OPENING", codes)
        self.assertIn("SUPPORTING_CAST_WITHOUT_CELEBRITY", codes)
        self.assertIn("ISOLATED_PAYOFF_TAG", codes)
        self.assertIn("CLOSING_SEGMENT_TOO_SHORT", codes)
        self.assertNotIn("TARGET_DURATION_VIOLATION", codes)
        self.assertIn("VISUAL_SHOT_DURATION_IMBALANCE", codes)

    def test_duration_gate_activates_only_when_current_work_order_sets_it(self) -> None:
        rows = [segment("short", "无尽冬日无需下载点击即玩", 5.0, ["cta"])]
        rows[0]["purpose_contract"].update({"function": "cta", "closing_payoff": True, "closing_quote": rows[0]["text"], "narrative_stage": 0})
        policy_value = self.v19_policy()
        policy_value["content_grounding_contract"]["target_duration_seconds"] = [23.0, 26.0]
        codes = {item["code"] for item in audit_plan("explicit-duration", rows, [], policy_value)}
        self.assertIn("TARGET_DURATION_VIOLATION", codes)

    def test_complete_result_keeps_payoff_tag_in_same_segment(self) -> None:
        contract = self.v19_policy()["content_grounding_contract"]
        rows = [
            segment("mechanism", "砍树获取资源升级熔炉，温度上来幸存者才能活下来", 9.0, ["mechanism"]),
            segment("WZ07_SURVIVOR_BUILD", "幸存者一多，建设末日城镇，称霸冰原，那不是so easy吗", 5.0, ["result"]),
        ]
        rows[0]["purpose_contract"]["function"] = "mechanism"
        rows[1]["purpose_contract"]["function"] = "result"
        failures = _audit_reusable_semantic_sequence("complete-result", rows, [transition(rows[0], rows[1], "cause_effect")], contract)
        self.assertEqual([], failures)

    def test_incomplete_result_and_isolated_payoff_are_rejected(self) -> None:
        contract = self.v19_policy()["content_grounding_contract"]
        result_row = segment("WZ07_SURVIVOR_BUILD", "幸存者一多，建设末日城镇，称霸冰原", 4.0, ["result"])
        result_row["purpose_contract"]["function"] = "result"
        isolated = segment("tag", "so easy", 1.0, ["payoff"])
        isolated["purpose_contract"]["function"] = "payoff"
        codes = {item["code"] for item in _audit_reusable_semantic_sequence("bad-result", [result_row, isolated], [transition(result_row, isolated, "payoff")], contract)}
        self.assertIn("INCOMPLETE_RESULT_SOURCE_UNIT", codes)
        self.assertIn("ISOLATED_PAYOFF_TAG", codes)

    def test_subject_jump_and_unbundled_midroll_benefit_are_rejected(self) -> None:
        contract = self.v19_policy()["content_grounding_contract"]
        rows = [
            segment("survival", "温度蹭蹭往上涨，幸存者才能活下来", 7.0, ["survival"]),
            segment("player", "我收了三十个幸存者", 3.0, ["player-result"]),
            segment("benefit", "上线就能领海量福利", 3.0, ["benefit"]),
            segment("proof", "你看我这熔炉都满级了，战力都一个亿了", 4.0, ["proof"]),
        ]
        rows[0]["purpose_contract"]["function"] = "mechanism"
        rows[1]["purpose_contract"]["function"] = "proof"
        rows[2]["purpose_contract"]["function"] = "benefit"
        rows[3]["purpose_contract"]["function"] = "proof"
        transitions = [transition(rows[0], rows[1]), transition(rows[1], rows[2], "benefit"), transition(rows[2], rows[3])]
        codes = {item["code"] for item in _audit_reusable_semantic_sequence("bad-subject-benefit", rows, transitions, contract)}
        self.assertIn("FIRST_PERSON_ACCOUNT_WITHOUT_SUBJECT_BRIDGE", codes)
        self.assertIn("GENERIC_BENEFIT_NOT_FINAL", codes)
        self.assertIn("GENERIC_BENEFIT_WITHOUT_CONCRETE_BENEFIT", codes)

    def test_good_forward_chain_passes(self) -> None:
        rows = [
            segment("a", "无尽冬日可是全球三亿人都在玩的冰雪游戏", 8.0, ["product", "players"]),
            segment("b", "一上来就给你几个逃难的幸存者安排他们砍树", 5.0, ["survivors", "tree"]),
            segment("c", "用资源点燃熔炉提升营地温度", 5.0, ["furnace", "temperature"]),
        ]
        failures = audit_plan("good", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2], "cause_effect")], policy())
        self.assertEqual([], failures)

    def test_rejects_ambiguous_product_and_duplicate_payoff(self) -> None:
        rows = [
            segment("a", "三亿人你们想想三亿人的含金量是多少", 4.0, ["players"]),
            segment("b", "只要开局升级熔炉再去砍树这不是易如反掌吗", 11.2, ["mechanic", "easy"]),
            segment("c", "那岂不是so easy吗", 1.7, [],),
        ]
        rows[2]["purpose_contract"]["function"] = "echo"
        failures = audit_plan("bad01", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2], "payoff")], policy())
        codes = {item["code"] for item in failures}
        self.assertIn("PRODUCT_MENTION_MISSING", codes)
        self.assertIn("SEMANTIC_EQUIVALENCE_REDUNDANCY", codes)
        self.assertIn("SHORT_SEGMENT_WITHOUT_UNIQUE_FUNCTION", codes)
        self.assertIn("DOMINANT_SEGMENT_TOO_LONG", codes)
        self.assertIn("PLAN_RHYTHM_IMBALANCE", codes)

    def test_group_only_opening_rejected(self) -> None:
        rows = [
            segment("a", "大哥那你呢", 1.3, ["question"], visible=["person:supporting"]),
            segment("b", "无尽冬日里先点燃熔炉", 7.0, ["product", "furnace"]),
            segment("c", "再安排幸存者砍树", 7.0, ["survivors", "tree"]),
        ]
        codes = {item["code"] for item in audit_plan("bad02", rows, [transition(rows[0], rows[1], "question_answer"), transition(rows[1], rows[2])], policy())}
        self.assertIn("GROUP_ONLY_CELEBRITY_OPENING", codes)

    def test_substantive_question_exception_allowed(self) -> None:
        rows = [
            segment("a", "那你在无尽冬日能打多少个", 2.4, ["combat_question"], visible=["person:supporting"]),
            segment("b", "我在无尽冬日现在能打三十个", 6.0, ["product", "combat_answer"]),
            segment("c", "一上来收留幸存者安排他们砍树", 6.0, ["survivors", "tree"]),
            segment("d", "再点燃熔炉提升温度", 3.0, ["furnace"]),
        ]
        rows[0]["opening_question_exception"] = {
            "allowed": True,
            "direct_question_quote": "那你在无尽冬日能打多少个",
            "question_topic": "combat_power",
            "target_person_id": "person:wuyue",
            "answer_candidate_id": "b",
            "answer_claim_id": "combat_answer",
        }
        failures = audit_plan(
            "good-question",
            rows,
            [transition(rows[0], rows[1], "question_answer"), transition(rows[1], rows[2]), transition(rows[2], rows[3], "cause_effect")],
            policy(),
        )
        self.assertEqual([], failures)

    def test_pronoun_time_and_difficulty_branches_rejected(self) -> None:
        rows = [
            segment("a", "无尽冬日可是三亿玩家都玩的游戏这都玩了一个月了那是你们游戏太难了", 7.0, ["product", "players", "difficulty"]),
            segment("b", "安排他们砍树打猎挖矿", 4.0, ["mechanic"]),
            segment("c", "点燃熔炉提升温度", 5.0, ["furnace"]),
        ]
        codes = {item["code"] for item in audit_plan("bad08", rows, [transition(rows[0], rows[1]), transition(rows[1], rows[2])], policy())}
        self.assertIn("UNRESOLVED_SURVIVOR_PRONOUN", codes)
        self.assertIn("NARRATIVE_TIME_REGRESSION", codes)
        self.assertIn("DIFFICULTY_COMPLAINT_UNANSWERED", codes)


if __name__ == "__main__":
    unittest.main()
