"""Machine-checkable editorial rules for one planned output.

These checks validate grounded annotations. The agent must still review the whole
spoken edit: labels and matching keywords do not prove a coherent narrative.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

GENERIC_INFORMATION_GAIN = re.compile(r"^从?cluster:.+推进到cluster:.+并增加下一步信息$")
RELATIONS = {
    "hook_response", "question_answer", "answer", "explanation", "problem_solution",
    "claim_support", "cause_effect", "condition_result", "progression", "proof",
    "benefit", "payoff", "contrast", "counterpoint", "escalation", "punchline",
    "speaker_experience", "story_resolution",
}
CONNECTORS = {
    "但是": {"contrast", "counterpoint"}, "不过": {"contrast", "counterpoint"},
    "但": {"contrast", "counterpoint"},
    "所以": {"cause_effect", "condition_result", "problem_solution", "payoff"},
    "这下": {"cause_effect", "condition_result", "payoff"},
    "然后": {"progression", "escalation"}, "接着": {"progression", "escalation"},
    "随后": {"progression", "escalation"},
}
CLOSING_FUNCTIONS = {"payoff", "benefit", "proof", "punchline", "release_close", "story_resolution", "收束", "结果",
                     "结尾", "result", "consequence", "cta", "gift_benefit"}
MIN_CLOSING_SECONDS = 2.0
NUMERIC_ENDINGS = [r"^(?:这周)?我收了(?:十|三十|[0-9]+)个幸存者$"]
METHOD_ENDINGS = [r"^把资源拿来升级熔炉$", r"^最要紧的是什么点燃熔炉$", r"^.*开局一定要先砍树.*提升温度$",
                  r"^.*要想富先砍树.*升级熔炉$", r"^.*发展建设$"]
REDUNDANT_PHRASES = ["易如反掌", "轻轻松松", "小意思", "so easy"]
PAYOFF_TAGS = ["so easy", "soeasy"]
RESULT_ANCHORS = ["幸存者一多", "建设末日城镇", "称霸冰原", "战力蹭蹭", "熔炉满级"]
RESULT_FUNCTIONS = {"result", "payoff", "proof", "consequence", "结果", "收束", "hook"}
CAUSAL_RELATIONS = {"cause_effect", "condition_result", "problem_solution", "payoff"}
ACQUISITION_BRIDGES = ["招收幸存者", "吸引更多的幸存者", "吸引更多幸存者", "幸存者才能活下来", "幸存者不会被冻死"]
FIRST_PERSON_RESULTS = [r"^我(?:收了|招收了).+幸存者"]
FIRST_PERSON_SUBJECTS = ["我", "吴樾", "玩家", "樾哥"]
GENERIC_BENEFITS = ["海量福利"]
CONCRETE_BENEFITS = ["礼包码", "福利码", "输入", "兑换", "666", "888"]


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def failure(code: str, scope: str, detail: str) -> dict[str, str]:
    return {"code": code, "scope": scope, "detail": detail}


def _contains_any(text: str, terms: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(term) in normalized for term in terms if normalize_text(term))


def _matches_any(text: object, patterns: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(re.search(pattern, normalized) for pattern in patterns)


def _transition_evidence(scope: str, left: dict, right: dict, transition: dict) -> list[dict[str, str]]:
    result = []
    information_gain = normalize_text(transition.get("information_gain"))
    if GENERIC_INFORMATION_GAIN.fullmatch(str(transition.get("information_gain", "")).replace(" ", "")):
        result.append(failure("TEMPLATED_INFORMATION_GAIN", scope, str(transition.get("information_gain"))))
    evidence = transition.get("content_evidence")
    if not isinstance(evidence, dict):
        return result + [failure("CONTENT_RELATION_EVIDENCE_REQUIRED", scope, "missing content_evidence")]
    missing = [key for key in ("from_quote", "to_quote", "new_information", "entity_flow", "state_flow")
               if not normalize_text(evidence.get(key))]
    if missing:
        return result + [failure("CONTENT_RELATION_EVIDENCE_INCOMPLETE", scope, ",".join(missing))]
    if normalize_text(evidence["from_quote"]) not in normalize_text(left.get("text")):
        result.append(failure("FROM_QUOTE_NOT_IN_SPEECH", scope, str(evidence["from_quote"])))
    if normalize_text(evidence["to_quote"]) not in normalize_text(right.get("text")):
        result.append(failure("TO_QUOTE_NOT_IN_SPEECH", scope, str(evidence["to_quote"])))
    if len(normalize_text(evidence["new_information"])) < 4 or normalize_text(evidence["new_information"]) == information_gain:
        result.append(failure("NEW_INFORMATION_NOT_GROUNDED", scope, str(evidence["new_information"])))
    return result


def _result_and_benefit_rules(scope: str, segments: list[dict], transitions: list[dict]) -> list[dict[str, str]]:
    """Grounded mechanism/result, first-person subject and benefit rules."""
    result = []
    for index, segment in enumerate(segments):
        segment_scope = f"{scope}:segment:{index + 1}"
        text = str(segment.get("text") or "")
        purpose = segment.get("purpose_contract") if isinstance(segment.get("purpose_contract"), dict) else {}
        function = normalize_text(purpose.get("function"))
        transition = transitions[index - 1] if index > 0 else {}
        if _contains_any(text, PAYOFF_TAGS) and not _contains_any(text, RESULT_ANCHORS):
            result.append(failure("ISOLATED_PAYOFF_TAG", segment_scope, text))
        if _contains_any(text, RESULT_ANCHORS):
            if function not in RESULT_FUNCTIONS:
                result.append(failure("RESULT_BEAT_MISCLASSIFIED", segment_scope, str(purpose.get("function"))))
            if index > 0 and transition.get("relation") not in CAUSAL_RELATIONS:
                result.append(failure("RESULT_BEAT_CAUSAL_RELATION_REQUIRED", segment_scope, str(transition.get("relation"))))
            prior_text = " ".join(str(item.get("text") or "") for item in segments[:index])
            if _contains_any(prior_text, ["砍树"]) and _contains_any(prior_text, ["熔炉"]) and not _contains_any(prior_text, ACQUISITION_BRIDGES):
                result.append(failure("MECHANISM_TO_RESULT_BRIDGE_REQUIRED", segment_scope, prior_text))
        if _matches_any(text, FIRST_PERSON_RESULTS):
            previous_text = str(segments[index - 1].get("text") or "") if index > 0 else ""
            structured = (transition.get("relation") == "speaker_experience"
                          and normalize_text((transition.get("content_evidence") or {}).get("subject_flow")) != "")
            if not (_contains_any(previous_text, FIRST_PERSON_SUBJECTS) or structured):
                result.append(failure("FIRST_PERSON_ACCOUNT_WITHOUT_SUBJECT_BRIDGE", segment_scope, text))
        if _contains_any(text, GENERIC_BENEFITS):
            if index != len(segments) - 1:
                result.append(failure("GENERIC_BENEFIT_NOT_FINAL", segment_scope, text))
            if not _contains_any(text, CONCRETE_BENEFITS):
                result.append(failure("GENERIC_BENEFIT_WITHOUT_CONCRETE_BENEFIT", segment_scope, text))
            if index > 0:
                previous = segments[index - 1].get("purpose_contract")
                if normalize_text((previous if isinstance(previous, dict) else {}).get("function")) not in RESULT_FUNCTIONS:
                    result.append(failure("BENEFIT_CTA_BEFORE_PROOF", segment_scope, str((previous or {}).get("function"))))
    return result


def audit_semantic_sequence(plan_id: str, segments: list[dict[str, Any]],
                            transitions: list[dict[str, Any]]) -> list[dict[str, str]]:
    scope = f"plan:{plan_id}"
    if not segments:
        return [failure("EMPTY_PLAN", scope, "no complete spoken units")]
    if not isinstance(transitions, list) or len(transitions) != len(segments) - 1:
        return [failure("TRANSITION_SCOPE_MISMATCH", scope, "one transition per adjacent spoken unit")]
    if any(not isinstance(transition, dict) for transition in transitions):
        return [failure("CONTENT_RELATION_EVIDENCE_REQUIRED", scope, "transitions must be objects")]
    result: list[dict[str, str]] = []
    claims: set[str] = set()
    prior_stage: int | None = None
    prior_texts: set[str] = set()
    for index, segment in enumerate(segments):
        segment_scope = f"{scope}:segment:{index + 1}"
        text = normalize_text(segment.get("text"))
        purpose = segment.get("purpose_contract")
        if not text or text in prior_texts:
            result.append(failure("REPEATED_OR_EMPTY_SPOKEN_UNIT", segment_scope, text))
        prior_texts.add(text)
        if not isinstance(purpose, dict):
            result.append(failure("SEGMENT_PURPOSE_CONTRACT_REQUIRED", segment_scope, "missing purpose_contract"))
            continue
        new_claims = purpose.get("new_claim_ids")
        if (not normalize_text(purpose.get("function")) or purpose.get("non_redundant") is not True
                or not isinstance(new_claims, list) or not new_claims
                or any(not isinstance(claim, str) or not claim.strip() for claim in new_claims)):
            result.append(failure("SEGMENT_PURPOSE_CONTRACT_INCOMPLETE", segment_scope, "function, new claims and non_redundant required"))
        else:
            if claims.intersection(new_claims) or len(new_claims) != len(set(new_claims)):
                result.append(failure("REPEATED_CLAIM", segment_scope, str(new_claims)))
            claims.update(new_claims)
        stage = purpose.get("narrative_stage")
        if type(stage) is not int or stage < 0:
            result.append(failure("NARRATIVE_STAGE_REQUIRED", segment_scope, str(stage)))
        else:
            if prior_stage is not None and stage < prior_stage:
                bridge = transitions[0].get("content_evidence", {}) if index == 1 else {}
                quote = normalize_text(bridge.get("how_why_quote")) if isinstance(bridge, dict) else ""
                hook_return = (index == 1 and segments[0].get("purpose_contract", {}).get("function") == "hook"
                               and transitions[0].get("relation") == "hook_response"
                               and bool(quote) and quote in text
                               and any(term in quote for term in ("怎么", "如何", "为什么", "因为", "原因")))
                if not hook_return:
                    result.append(failure("NARRATIVE_STAGE_REGRESSION", segment_scope, f"{prior_stage}->{stage}"))
            prior_stage = stage
        for connector, relations in CONNECTORS.items():
            if text.startswith(connector):
                relation = transitions[index - 1].get("relation") if index else None
                if relation not in relations:
                    result.append(failure("DEPENDENT_CONNECTOR_WITHOUT_ANTECEDENT", segment_scope, connector))
                break
        if text.startswith("因为") and index == 0 and not any(term in text for term in ("所以", "因此")):
            result.append(failure("DEPENDENT_CONNECTOR_WITHOUT_ANTECEDENT", segment_scope, "因为"))
        if text.startswith("只要") and not any(term in text for term in ("就", "便", "即可", "才能")):
            if index >= len(transitions) or transitions[index].get("relation") != "condition_result":
                result.append(failure("CONDITION_WITHOUT_RESULT", segment_scope, "只要"))
        if segment.get("scenario_bound") is True and index:
            evidence = transitions[index - 1].get("content_evidence", {})
            if not isinstance(evidence, dict) or not normalize_text(evidence.get("scenario_continuity")):
                result.append(failure("SCENARIO_CONTINUITY_REQUIRED", segment_scope, "scene-dependent performance needs its context"))
    for index, transition in enumerate(transitions):
        transition_scope = f"{scope}:transition:{index + 1}"
        if transition.get("relation") not in RELATIONS or not normalize_text(transition.get("information_gain")):
            result.append(failure("NON_SPECIFIC_TRANSITION_RELATION", transition_scope, str(transition.get("relation"))))
        result.extend(_transition_evidence(transition_scope, segments[index], segments[index + 1], transition))
    last = segments[-1]
    purpose = last.get("purpose_contract") or {}
    closing = normalize_text(purpose.get("closing_quote")) if isinstance(purpose, dict) else ""
    if (not isinstance(purpose, dict) or purpose.get("function") not in CLOSING_FUNCTIONS
            or purpose.get("closing_payoff") is not True or not closing or closing not in normalize_text(last.get("text"))):
        result.append(failure("CLOSING_PAYOFF_REQUIRED", scope, "complete earned closing quote required"))
    if float(last.get("duration", 0)) < MIN_CLOSING_SECONDS:
        result.append(failure("CLOSING_SEGMENT_TOO_SHORT", scope, str(last.get("duration"))))
    if _matches_any(last.get("text"), NUMERIC_ENDINGS):
        result.append(failure("NUMERIC_ACCOUNT_NOT_A_CLOSE", scope, str(last.get("text"))))
    if _matches_any(last.get("text"), METHOD_ENDINGS):
        result.append(failure("METHOD_BEAT_NOT_A_CLOSE", scope, str(last.get("text"))))
    used = [index for index, segment in enumerate(segments) if _contains_any(str(segment.get("text", "")), REDUNDANT_PHRASES)]
    if len(used) > 1:
        result.append(failure("SEMANTIC_EQUIVALENCE_REPETITION", scope, str(used)))
    return result + _result_and_benefit_rules(scope, segments, transitions)
