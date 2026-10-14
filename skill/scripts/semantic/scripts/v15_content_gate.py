#!/usr/bin/env python3
"""Content-grounded semantic checks retained and tightened by V19.

The V9-shaped runtime remains responsible for hashes and artifact locking. This
module rejects plans whose relation labels are not supported by the actual
spoken chain, whose product identity is missing, or whose opening/pacing/short
segments violate the celebrity-montage contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


GENERIC_INFORMATION_GAIN = re.compile(r"^从?cluster:.+推进到cluster:.+并增加下一步信息$")


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_contract() -> dict[str, Any]:
    return {
        "product_terms": ["无尽冬日"],
        "product_asr_aliases": ["无尽冬日", "无尽东西", "不尽冬日", "这无尽冬日"],
        "product_mention_deadline_seconds": 8.5,
        "require_product_attestation": True,
        "require_opening_cast_attestation": True,
        "require_boundary_attestation": True,
        "celebrity_person_ids": [],
        "question_exception_max_seconds": 3.2,
        "forbidden_isolated_tokens": ["氪", "停"],
        "redundancy_groups": [["易如反掌", "so easy", "soeasy"]],
        "forbidden_transition_relations": ["round_continuation"],
        "short_plan_max_seconds": 18.0,
        "short_plan_max_single_segment_seconds": 10.0,
        "long_plan_max_single_segment_seconds": 14.0,
        "short_plan_max_segment_share": 0.62,
        "long_plan_max_segment_share": 0.68,
        "min_independent_segment_seconds": 2.0,
        "difficulty_answer_terms": ["盗版", "玩的有问题", "玩得有问题", "你玩的不对"],
        "survivor_antecedent_terms": ["幸存者", "逃难", "居民", "营地的人"],
        "mechanic_terms": ["砍树", "打猎", "挖矿", "熔炉", "升级"],
        "hard_excluded_phrases": [],
        "require_narrative_stage": False,
        "require_closing_payoff": False,
        "allowed_closing_functions": ["payoff", "benefit", "proof", "punchline", "release_close", "story_resolution", "收束", "结果", "结尾"],
        "forbidden_numeric_endings": [r"^(?:这周)?我收了(?:十|三十|[0-9]+)个幸存者$"],
        "forbidden_method_endings": [r"^把资源拿来升级熔炉$", r"^最要紧的是什么点燃熔炉$", r"^.*开局一定要先砍树.*提升温度$", r"^.*要想富先砍树.*升级熔炉$", r"^.*发展建设$"],
        "target_duration_seconds": [],
        "min_closing_segment_seconds": 2.0,
        "forbidden_opening_phrases": [],
        "forbidden_dependent_openings": ["但", "但是", "不过", "所以", "然后", "接着"],
        "require_celebrity_visible_in_every_segment": False,
        "require_visual_shot_table": False,
        "max_visual_shot_duration_spread_seconds": 3.0,
        "globally_invalidated_until_recut": [],
        "payoff_tag_terms": ["so easy", "soeasy"],
        "result_anchor_terms": ["幸存者一多", "建设末日城镇", "称霸冰原", "战力蹭蹭", "熔炉满级"],
        "result_functions": ["result", "payoff", "proof", "consequence", "结果", "收束"],
        "causal_result_relations": ["cause_effect", "condition_result", "problem_solution", "payoff"],
        "acquisition_bridge_terms": ["招收幸存者", "吸引更多的幸存者", "吸引更多幸存者", "幸存者才能活下来", "幸存者不会被冻死"],
        "first_person_player_result_patterns": [r"^我(?:收了|招收了).+幸存者"],
        "first_person_subject_terms": ["我", "吴樾", "玩家", "樾哥"],
        "generic_benefit_terms": ["海量福利"],
        "concrete_benefit_terms": ["礼包码", "福利码", "输入", "兑换", "666", "888"],
        "result_unit_completion_rules": [],
    }


def merge_contract(policy: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    value = default_contract()
    supplied = policy.get("content_grounding_contract")
    if not isinstance(supplied, dict):
        return value, False
    value.update(supplied)
    return value, True


def failure(code: str, scope: str, detail: str) -> dict[str, str]:
    return {"code": code, "scope": scope, "detail": detail}


def _audit_reusable_semantic_sequence(
    plan_id: str,
    segments: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    contract: dict[str, Any],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    scope = f"plan:{plan_id}"
    result_functions = {normalize_text(value) for value in contract.get("result_functions", [])}
    causal_relations = {str(value) for value in contract.get("causal_result_relations", [])}
    for index, segment in enumerate(segments):
        segment_scope = f"{scope}:segment:{index + 1}"
        text = str(segment.get("actual_asr_text") or segment.get("text") or "")
        normalized = normalize_text(text)
        purpose = segment.get("purpose_contract") if isinstance(segment.get("purpose_contract"), dict) else {}
        function = normalize_text(purpose.get("function"))
        transition = transitions[index - 1] if index > 0 and index - 1 < len(transitions) else {}

        if _contains_any(text, contract.get("payoff_tag_terms", [])) and not _contains_any(text, contract.get("result_anchor_terms", [])):
            result.append(failure("ISOLATED_PAYOFF_TAG", segment_scope, text))

        for rule in contract.get("result_unit_completion_rules", []):
            candidate_ids = {str(value) for value in rule.get("candidate_ids", [])}
            anchors = list(rule.get("anchor_terms", []))
            suffixes = list(rule.get("required_suffix_terms", []))
            if str(segment.get("candidate_id")) in candidate_ids and _contains_any(text, anchors) and not _contains_any(text, suffixes):
                result.append(failure("INCOMPLETE_RESULT_SOURCE_UNIT", segment_scope, text))

        if _contains_any(text, contract.get("result_anchor_terms", [])):
            if function not in result_functions:
                result.append(failure("RESULT_BEAT_MISCLASSIFIED", segment_scope, str(purpose.get("function"))))
            if index > 0 and transition.get("relation") not in causal_relations:
                result.append(failure("RESULT_BEAT_CAUSAL_RELATION_REQUIRED", segment_scope, str(transition.get("relation"))))
            prior_text = " ".join(str(item.get("actual_asr_text") or item.get("text") or "") for item in segments[:index])
            if _contains_any(prior_text, ["砍树"]) and _contains_any(prior_text, ["熔炉"]) and not _contains_any(prior_text, contract.get("acquisition_bridge_terms", [])):
                result.append(failure("MECHANISM_TO_RESULT_BRIDGE_REQUIRED", segment_scope, prior_text))

        if _matches_any(text, contract.get("first_person_player_result_patterns", [])):
            previous_text = str(segments[index - 1].get("actual_asr_text") or segments[index - 1].get("text") or "") if index > 0 else ""
            has_spoken_subject_bridge = _contains_any(previous_text, contract.get("first_person_subject_terms", []))
            has_structured_subject_bridge = transition.get("relation") == "speaker_experience" and normalize_text((transition.get("content_evidence") or {}).get("subject_flow")) != ""
            if not (has_spoken_subject_bridge or has_structured_subject_bridge):
                result.append(failure("FIRST_PERSON_ACCOUNT_WITHOUT_SUBJECT_BRIDGE", segment_scope, text))

        if _contains_any(text, contract.get("generic_benefit_terms", [])):
            if index != len(segments) - 1:
                result.append(failure("GENERIC_BENEFIT_NOT_FINAL", segment_scope, text))
            if not _contains_any(text, contract.get("concrete_benefit_terms", [])):
                result.append(failure("GENERIC_BENEFIT_WITHOUT_CONCRETE_BENEFIT", segment_scope, text))
            if index > 0:
                previous_purpose = segments[index - 1].get("purpose_contract") if isinstance(segments[index - 1].get("purpose_contract"), dict) else {}
                if normalize_text(previous_purpose.get("function")) not in result_functions:
                    result.append(failure("BENEFIT_CTA_BEFORE_PROOF", segment_scope, str(previous_purpose.get("function"))))
    return result


def _contains_any(text: str, terms: list[object]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(term) in normalized for term in terms if normalize_text(term))


def _matches_any(text: object, patterns: list[object]) -> bool:
    normalized = normalize_text(text)
    return any(re.search(str(pattern), normalized) for pattern in patterns if str(pattern))


def _elapsed_seconds(start: object, end: object) -> float:
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()
    except Exception:
        return -1.0


def _verify_product_attestation(segment: dict[str, Any], contract: dict[str, Any]) -> tuple[bool, float | None]:
    rows = segment.get("spoken_product_mentions")
    if not isinstance(rows, list):
        return False, None
    canonical_terms = {normalize_text(term) for term in contract["product_terms"]}
    aliases = [normalize_text(term) for term in contract["product_asr_aliases"]]
    text = normalize_text(segment.get("actual_asr_text") or segment.get("text"))
    for row in rows:
        if not isinstance(row, dict) or normalize_text(row.get("canonical_term")) not in canonical_terms:
            continue
        spoken = normalize_text(row.get("spoken_text"))
        if not spoken or spoken not in text or not any(alias in spoken or spoken in alias for alias in aliases):
            continue
        try:
            local_time = float(row.get("start_seconds", 0.0))
        except (TypeError, ValueError):
            continue
        path_value, hash_value = row.get("evidence_path"), str(row.get("evidence_sha256", "")).lower()
        if contract.get("require_product_attestation"):
            path = Path(str(path_value or ""))
            if not path.is_file() or _sha256(path) != hash_value:
                continue
        return True, local_time
    return False, None


def _validate_transition_evidence(
    plan_id: str,
    index: int,
    left: dict[str, Any],
    right: dict[str, Any],
    transition: dict[str, Any],
) -> list[dict[str, str]]:
    scope = f"plan:{plan_id}:transition:{index + 1}"
    result: list[dict[str, str]] = []
    information_gain = normalize_text(transition.get("information_gain"))
    if GENERIC_INFORMATION_GAIN.fullmatch(str(transition.get("information_gain", "")).replace(" ", "")):
        result.append(failure("TEMPLATED_INFORMATION_GAIN", scope, str(transition.get("information_gain"))))
    evidence = transition.get("content_evidence")
    if not isinstance(evidence, dict):
        result.append(failure("CONTENT_RELATION_EVIDENCE_REQUIRED", scope, "missing content_evidence"))
        return result
    required = ("from_quote", "to_quote", "new_information", "entity_flow", "state_flow")
    missing = [key for key in required if not normalize_text(evidence.get(key))]
    if missing:
        result.append(failure("CONTENT_RELATION_EVIDENCE_INCOMPLETE", scope, ",".join(missing)))
        return result
    left_text = normalize_text(left.get("actual_asr_text") or left.get("text"))
    right_text = normalize_text(right.get("actual_asr_text") or right.get("text"))
    if normalize_text(evidence["from_quote"]) not in left_text:
        result.append(failure("FROM_QUOTE_NOT_IN_SPEECH", scope, str(evidence["from_quote"])))
    if normalize_text(evidence["to_quote"]) not in right_text:
        result.append(failure("TO_QUOTE_NOT_IN_SPEECH", scope, str(evidence["to_quote"])))
    if len(normalize_text(evidence["new_information"])) < 4 or normalize_text(evidence["new_information"]) == information_gain:
        result.append(failure("NEW_INFORMATION_NOT_GROUNDED", scope, str(evidence["new_information"])))
    return result


def audit_semantic_sequence(
    plan_id: str,
    segments: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Shared editorial baseline, without legacy listening/cast receipt formats.

    These checks validate grounded annotations. Codex must still review the whole
    spoken edit: labels and matching keywords do not prove a coherent narrative.
    """
    scope = f"plan:{plan_id}"
    result: list[dict[str, str]] = []
    if not segments:
        return [failure("EMPTY_PLAN", scope, "no complete spoken units")]
    if not isinstance(transitions, list) or len(transitions) != len(segments) - 1:
        return [failure("TRANSITION_SCOPE_MISMATCH", scope, "one transition per adjacent spoken unit")]
    if any(not isinstance(transition, dict) for transition in transitions):
        return [failure("CONTENT_RELATION_EVIDENCE_REQUIRED", scope, "transitions must be objects")]
    allowed = {
        "hook_response", "question_answer", "answer", "explanation", "problem_solution",
        "claim_support", "cause_effect", "condition_result", "progression", "proof",
        "benefit", "payoff", "contrast", "counterpoint", "escalation", "punchline",
        "speaker_experience", "story_resolution",
    }
    connectors = {
        "但是": {"contrast", "counterpoint"}, "不过": {"contrast", "counterpoint"},
        "但": {"contrast", "counterpoint"},
        "所以": {"cause_effect", "condition_result", "problem_solution", "payoff"},
        "这下": {"cause_effect", "condition_result", "payoff"},
        "然后": {"progression", "escalation"}, "接着": {"progression", "escalation"},
        "随后": {"progression", "escalation"},
    }
    claims: set[str] = set()
    prior_stage: int | None = None
    prior_texts: set[str] = set()
    contract = default_contract()
    contract["redundancy_groups"] = [["易如反掌", "轻轻松松", "小意思", "so easy"]]
    contract["result_functions"] = [*contract["result_functions"], "hook"]
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
        for connector, relations in connectors.items():
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
        if not isinstance(transition, dict):
            result.append(failure("CONTENT_RELATION_EVIDENCE_REQUIRED", transition_scope, "transition must be an object"))
            continue
        if transition.get("relation") not in allowed or not normalize_text(transition.get("information_gain")):
            result.append(failure("NON_SPECIFIC_TRANSITION_RELATION", transition_scope, str(transition.get("relation"))))
        result.extend(_validate_transition_evidence(plan_id, index, segments[index], segments[index + 1], transition))
    last = segments[-1]
    purpose = last.get("purpose_contract") or {}
    closing = normalize_text(purpose.get("closing_quote")) if isinstance(purpose, dict) else ""
    if (not isinstance(purpose, dict) or purpose.get("function") not in set(contract["allowed_closing_functions"] + ["result", "consequence", "cta", "gift_benefit"])
            or purpose.get("closing_payoff") is not True or not closing or closing not in normalize_text(last.get("text"))):
        result.append(failure("CLOSING_PAYOFF_REQUIRED", scope, "complete earned closing quote required"))
    if float(last.get("duration", 0)) < contract["min_closing_segment_seconds"]:
        result.append(failure("CLOSING_SEGMENT_TOO_SHORT", scope, str(last.get("duration"))))
    if _matches_any(last.get("text"), contract["forbidden_numeric_endings"]):
        result.append(failure("NUMERIC_ACCOUNT_NOT_A_CLOSE", scope, str(last.get("text"))))
    if _matches_any(last.get("text"), contract["forbidden_method_endings"]):
        result.append(failure("METHOD_BEAT_NOT_A_CLOSE", scope, str(last.get("text"))))
    for group in contract["redundancy_groups"]:
        used = [index for index, segment in enumerate(segments) if _contains_any(str(segment.get("text", "")), group)]
        if len(used) > 1:
            result.append(failure("SEMANTIC_EQUIVALENCE_REPETITION", scope, str(used)))
    # Retain the original grounded mechanism/result, subject and benefit rules.
    if all(isinstance(transition, dict) for transition in transitions):
        result.extend(_audit_reusable_semantic_sequence(plan_id, segments, transitions, contract))
    return result


def audit_plan(
    plan_id: str,
    segments: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    scope = f"plan:{plan_id}"
    contract, supplied = merge_contract(policy)
    if not supplied:
        result.append(failure("V15_CONTENT_GROUNDING_CONTRACT_REQUIRED", scope, "policy.content_grounding_contract"))
    celebrity_ids = {str(value) for value in contract.get("celebrity_person_ids", [])}
    if not celebrity_ids:
        result.append(failure("CELEBRITY_PERSON_IDS_REQUIRED", scope, "content contract has no celebrity_person_ids"))
    if not segments:
        return result + [failure("EMPTY_PLAN", scope, "no segments")]

    cumulative = 0.0
    product_time: float | None = None
    narrative_stages: list[int] = []
    for index, segment in enumerate(segments):
        segment_scope = f"{scope}:segment:{index + 1}"
        text = str(segment.get("actual_asr_text") or segment.get("text") or "")
        planned_text = str(segment.get("text") or "")
        actual_text = str(segment.get("actual_asr_text") or "")
        if not actual_text or normalize_text(planned_text) != normalize_text(actual_text):
            result.append(failure("PLAN_TEXT_NOT_EXACT_SOURCE_SPEECH", segment_scope, f"plan={planned_text}|actual={actual_text}"))
        if segment.get("candidate_id") in set(contract.get("globally_invalidated_until_recut", [])):
            result.append(failure("INVALIDATED_REUSED_CANDIDATE", segment_scope, str(segment.get("candidate_id"))))
        if contract.get("require_celebrity_visible_in_every_segment"):
            visible = {str(value) for value in segment.get("visible_person_ids", [])}
            if not visible or not (visible & celebrity_ids):
                result.append(failure("SUPPORTING_CAST_WITHOUT_CELEBRITY", segment_scope, str(sorted(visible))))
        stripped = text.strip().lstrip("，。！？,.!? ")
        for connector in contract.get("forbidden_dependent_openings", []):
            if stripped.startswith(str(connector)):
                relation = transitions[index - 1].get("relation") if index > 0 and index - 1 < len(transitions) else None
                if relation not in {"contrast", "counterpoint"}:
                    result.append(failure("DEPENDENT_CONNECTOR_WITHOUT_ANTECEDENT", segment_scope, str(connector)))
                break
        attested, local_time = _verify_product_attestation(segment, contract)
        if attested and product_time is None:
            product_time = cumulative + float(local_time or 0.0)
        elif not contract.get("require_product_attestation") and _contains_any(text, contract["product_asr_aliases"]) and product_time is None:
            product_time = cumulative

        purpose = segment.get("purpose_contract")
        if not isinstance(purpose, dict):
            result.append(failure("SEGMENT_PURPOSE_CONTRACT_REQUIRED", segment_scope, str(segment.get("candidate_id"))))
        else:
            if not normalize_text(purpose.get("function")) or not isinstance(purpose.get("new_claim_ids"), list):
                result.append(failure("SEGMENT_PURPOSE_CONTRACT_INCOMPLETE", segment_scope, str(purpose)))
            if purpose.get("non_redundant") is not True:
                result.append(failure("SEGMENT_REDUNDANCY_NOT_CLEARED", segment_scope, str(purpose.get("non_redundant"))))
            if contract.get("require_narrative_stage"):
                stage = purpose.get("narrative_stage")
                if not isinstance(stage, int) or stage < 0:
                    result.append(failure("NARRATIVE_STAGE_REQUIRED", segment_scope, str(stage)))
                else:
                    narrative_stages.append(stage)

        boundary = segment.get("boundary_cleanliness")
        if not isinstance(boundary, dict) or boundary.get("decision") != "pass" or boundary.get("listened_normal_speed") is not True:
            result.append(failure("NORMAL_SPEED_BOUNDARY_REVIEW_REQUIRED", segment_scope, "boundary_cleanliness"))
        elif boundary.get("leading_extra_tokens") or boundary.get("trailing_extra_tokens"):
            result.append(failure("ISOLATED_BOUNDARY_TOKEN", segment_scope, str(boundary)))
        elif contract.get("require_boundary_attestation"):
            boundary_path = Path(str(boundary.get("evidence_path") or ""))
            boundary_hash = str(boundary.get("evidence_sha256") or "").lower()
            boundary_ok = False
            if boundary_path.is_file() and _sha256(boundary_path) == boundary_hash:
                try:
                    boundary_value = json.loads(boundary_path.read_text(encoding="utf-8"))
                except Exception:
                    boundary_value = {}
                boundary_ok = (
                    boundary_value.get("schema") == "spoken-boundary-listening-evidence/v260928"
                    and boundary_value.get("decision") == "pass"
                    and boundary_value.get("candidate_id") == segment.get("candidate_id")
                )
            if not boundary_ok:
                result.append(failure("BOUNDARY_ATTESTATION_INVALID", segment_scope, str(segment.get("candidate_id"))))

        duration = float(segment.get("duration", 0.0))
        if duration < float(contract["min_independent_segment_seconds"]):
            exception = segment.get("opening_question_exception") if index == 0 else None
            claims = purpose.get("new_claim_ids", []) if isinstance(purpose, dict) else []
            function = normalize_text(purpose.get("function")) if isinstance(purpose, dict) else ""
            if not (isinstance(exception, dict) and exception.get("allowed") is True) and (not claims or function in {"echo", "filler", "reactiononly", "同义重复", "凑数"}):
                result.append(failure("SHORT_SEGMENT_WITHOUT_UNIQUE_FUNCTION", segment_scope, f"duration={duration:.3f}"))
        cumulative += duration

    if product_time is None:
        result.append(failure("PRODUCT_MENTION_MISSING", scope, ",".join(map(str, contract["product_terms"]))))
    elif product_time > float(contract["product_mention_deadline_seconds"]):
        result.append(failure("PRODUCT_MENTION_TOO_LATE", scope, f"{product_time:.3f}s"))

    first = segments[0]
    first_text = str(first.get("actual_asr_text") or first.get("text") or "")
    for phrase in contract.get("forbidden_opening_phrases", []):
        if normalize_text(phrase) and normalize_text(first_text).startswith(normalize_text(phrase)):
            result.append(failure("FORBIDDEN_STANDALONE_OPENING", scope, str(phrase)))
    opening_ids = first.get("opening_frame_visible_person_ids")
    if contract.get("require_opening_cast_attestation"):
        cast_path = Path(str(first.get("opening_cast_evidence_path") or ""))
        cast_hash = str(first.get("opening_cast_evidence_sha256") or "").lower()
        cast_ok = False
        if cast_path.is_file() and _sha256(cast_path) == cast_hash:
            try:
                cast_value = json.loads(cast_path.read_text(encoding="utf-8"))
            except Exception:
                cast_value = {}
            cast_ok = (
                cast_value.get("schema") == "celebrity-opening-cast-evidence/v260928"
                and cast_value.get("decision") == "pass"
                and cast_value.get("candidate_id") == first.get("candidate_id")
                and cast_value.get("visible_person_ids") == opening_ids
            )
        if not cast_ok:
            result.append(failure("OPENING_CAST_ATTESTATION_INVALID", scope, str(first.get("candidate_id"))))
    celebrity_visible = isinstance(opening_ids, list) and bool(celebrity_ids & {str(value) for value in opening_ids})
    if not isinstance(opening_ids, list) or not opening_ids:
        result.append(failure("OPENING_FRAME_CAST_EVIDENCE_REQUIRED", scope, str(first.get("candidate_id"))))
    if not celebrity_visible:
        exception = first.get("opening_question_exception")
        valid_exception = isinstance(exception, dict) and exception.get("allowed") is True
        if valid_exception:
            direct_quote = normalize_text(exception.get("direct_question_quote"))
            first_text = normalize_text(first.get("actual_asr_text") or first.get("text"))
            next_id = segments[1].get("candidate_id") if len(segments) > 1 else None
            transition = transitions[0] if transitions else {}
            valid_exception = (
                float(first.get("duration", 0.0)) <= float(contract["question_exception_max_seconds"])
                and len(direct_quote) >= 6
                and direct_quote in first_text
                and normalize_text(exception.get("question_topic")) not in {"", "generic", "那你呢", "reaction"}
                and str(exception.get("target_person_id")) in celebrity_ids
                and exception.get("answer_candidate_id") == next_id
                and normalize_text(exception.get("answer_claim_id")) != ""
                and transition.get("relation") == "question_answer"
            )
        if not valid_exception:
            result.append(failure("GROUP_ONLY_CELEBRITY_OPENING", scope, str(opening_ids)))

    combined = "|".join(str(segment.get("actual_asr_text") or segment.get("text") or "") for segment in segments)
    normalized_combined = normalize_text(combined)
    for phrase in contract.get("hard_excluded_phrases", []):
        if normalize_text(phrase) and normalize_text(phrase) in normalized_combined:
            result.append(failure("HARD_EXCLUDED_PHRASE", scope, str(phrase)))
    for group in contract.get("redundancy_groups", []):
        hits = []
        seen_normalized: set[str] = set()
        for term in group:
            key = normalize_text(term)
            if key and key not in seen_normalized and key in normalized_combined:
                hits.append(str(term))
                seen_normalized.add(key)
        if len(hits) > 1:
            result.append(failure("SEMANTIC_EQUIVALENCE_REDUNDANCY", scope, " + ".join(hits)))

    result.extend(_audit_reusable_semantic_sequence(plan_id, segments, transitions, contract))

    running_text = ""
    for index, segment in enumerate(segments):
        text = str(segment.get("actual_asr_text") or segment.get("text") or "")
        normalized = normalize_text(text)
        if "安排他们" in normalized or "收留他们" in normalized:
            prior_and_prefix = normalize_text(running_text + text[: max(text.find("他们"), 0)])
            if not _contains_any(prior_and_prefix, contract["survivor_antecedent_terms"]):
                result.append(failure("UNRESOLVED_SURVIVOR_PRONOUN", f"{scope}:segment:{index + 1}", text))
        answered_reset = _contains_any(running_text, contract.get("difficulty_answer_terms", []))
        if _contains_any(running_text, ["玩了一个月", "都玩一个月"]) and _contains_any(text, ["安排他们砍树", "一上来", "开局"]) and not answered_reset:
            result.append(failure("NARRATIVE_TIME_REGRESSION", f"{scope}:segment:{index + 1}", text))
        running_text += " " + text

    if contract.get("require_narrative_stage") and len(narrative_stages) == len(segments):
        for index in range(1, len(narrative_stages)):
            if narrative_stages[index] < narrative_stages[index - 1]:
                result.append(failure("NARRATIVE_STAGE_REGRESSION", f"{scope}:segment:{index + 1}", f"{narrative_stages[index - 1]}->{narrative_stages[index]}"))

    if contract.get("require_closing_payoff"):
        last = segments[-1]
        last_text = str(last.get("actual_asr_text") or last.get("text") or "")
        last_purpose = last.get("purpose_contract") if isinstance(last.get("purpose_contract"), dict) else {}
        allowed = {normalize_text(value) for value in contract.get("allowed_closing_functions", [])}
        if normalize_text(last_purpose.get("function")) not in allowed or last_purpose.get("closing_payoff") is not True:
            result.append(failure("AD_CLOSE_FUNCTION_REQUIRED", f"{scope}:segment:{len(segments)}", str(last_purpose)))
        closing_quote = normalize_text(last_purpose.get("closing_quote"))
        if not closing_quote or closing_quote not in normalize_text(last_text):
            result.append(failure("AD_CLOSE_QUOTE_REQUIRED", f"{scope}:segment:{len(segments)}", str(last_purpose.get("closing_quote"))))
        if _matches_any(last_text, contract.get("forbidden_numeric_endings", [])):
            result.append(failure("NUMERIC_STORY_BEAT_AS_ENDING", f"{scope}:segment:{len(segments)}", last_text))
        if _matches_any(last_text, contract.get("forbidden_method_endings", [])):
            result.append(failure("METHOD_ONLY_ENDING", f"{scope}:segment:{len(segments)}", last_text))
        if float(last.get("duration", 0.0)) < float(contract.get("min_closing_segment_seconds", 0.0)):
            result.append(failure("CLOSING_SEGMENT_TOO_SHORT", f"{scope}:segment:{len(segments)}", f"duration={float(last.get('duration', 0.0)):.3f}"))

    for index, segment in enumerate(segments):
        text = str(segment.get("actual_asr_text") or segment.get("text") or "")
        if _contains_any(text, ["游戏太难", "太难了"]):
            following = " ".join(str(item.get("actual_asr_text") or item.get("text") or "") for item in segments[index + 1:index + 3])
            mechanics_before_answer = _contains_any(following, contract["mechanic_terms"]) and not _contains_any(following, contract["difficulty_answer_terms"])
            if mechanics_before_answer or not _contains_any(following, contract["difficulty_answer_terms"]):
                result.append(failure("DIFFICULTY_COMPLAINT_UNANSWERED", f"{scope}:segment:{index + 1}", following))

    total = sum(float(segment.get("duration", 0.0)) for segment in segments)
    target = contract.get("target_duration_seconds", [])
    if isinstance(target, list) and len(target) == 2 and not (float(target[0]) <= total <= float(target[1])):
        result.append(failure("TARGET_DURATION_VIOLATION", scope, f"{total:.3f} not in {target}"))
    if contract.get("require_visual_shot_table"):
        shots = policy.get("visual_shots")
        if not isinstance(shots, list) or len(shots) < 3:
            result.append(failure("VISUAL_SHOT_TABLE_REQUIRED", scope, "at least three visual shots"))
        else:
            durations = []
            for shot in shots:
                try:
                    durations.append(float(shot["duration"]))
                except (TypeError, ValueError, KeyError):
                    result.append(failure("VISUAL_SHOT_TABLE_INVALID", scope, str(shot)))
            if durations and max(durations) - min(durations) > float(contract.get("max_visual_shot_duration_spread_seconds", 3.0)) + 0.001:
                result.append(failure("VISUAL_SHOT_DURATION_IMBALANCE", scope, f"spread={max(durations) - min(durations):.3f}"))
    if total > 0 and len(segments) >= 3:
        longest = max(float(segment.get("duration", 0.0)) for segment in segments)
        short_plan = total <= float(contract["short_plan_max_seconds"])
        max_seconds = float(contract["short_plan_max_single_segment_seconds"] if short_plan else contract["long_plan_max_single_segment_seconds"])
        max_share = float(contract["short_plan_max_segment_share"] if short_plan else contract["long_plan_max_segment_share"])
        if longest > max_seconds:
            result.append(failure("DOMINANT_SEGMENT_TOO_LONG", scope, f"{longest:.3f}>{max_seconds:.3f}"))
        if longest / total > max_share:
            result.append(failure("PLAN_RHYTHM_IMBALANCE", scope, f"share={longest / total:.3f}>{max_share:.3f}"))

    forbidden_relations = set(contract.get("forbidden_transition_relations", []))
    for index, transition in enumerate(transitions):
        if transition.get("relation") in forbidden_relations:
            result.append(failure("NON_SPECIFIC_TRANSITION_RELATION", f"{scope}:transition:{index + 1}", str(transition.get("relation"))))
        if index + 1 < len(segments):
            result.extend(_validate_transition_evidence(plan_id, index, segments[index], segments[index + 1], transition))
    return result


REQUIRED_SOL_CHECKS = {
    "product_spoken_by_deadline",
    "celebrity_opening_or_substantive_question_exception",
    "no_semantic_equivalence_repetition",
    "every_segment_has_unique_function",
    "pronoun_antecedents_resolved",
    "narrative_state_progresses",
    "difficulty_response_branch_complete",
    "no_isolated_or_unrecognized_filler_tokens",
    "pacing_balance_acceptable_after_declared_speed",
    "full_normal_speed_audio_review",
    "earned_advertising_close",
}


def audit_sol_review(review: dict[str, Any], scope: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if review.get("schema") != "semantic-sol-output-review/v260928" or review.get("decision") != "pass":
        result.append(failure("V18_SOL_REVIEW_REQUIRED", scope, str(review.get("schema"))))
        return result
    provenance = review.get("model_provenance")
    if not isinstance(provenance, dict) or provenance.get("model") != "gpt-5.6-sol" or not normalize_text(provenance.get("execution_id")):
        result.append(failure("V18_SOL_MODEL_PROVENANCE_REQUIRED", scope, str(provenance)))
    audio_duration = float(review.get("audio_duration_seconds", 0.0) or 0.0)
    audio_listened = float(review.get("normal_speed_listened_seconds", 0.0) or 0.0)
    elapsed = _elapsed_seconds(review.get("review_started_at"), review.get("review_completed_at"))
    if audio_duration <= 0 or audio_listened + 0.05 < audio_duration or elapsed + 0.05 < audio_duration:
        result.append(failure("V18_NORMAL_SPEED_REVIEW_TIME_INVALID", scope, f"duration={audio_duration:.3f},listened={audio_listened:.3f},elapsed={elapsed:.3f}"))
    checks = review.get("content_checks")
    if not isinstance(checks, dict):
        return result + [failure("V18_SOL_CONTENT_CHECKS_REQUIRED", scope, "content_checks")]
    missing = sorted(REQUIRED_SOL_CHECKS - set(checks))
    false_values = sorted(key for key in REQUIRED_SOL_CHECKS if checks.get(key) is not True)
    if missing:
        result.append(failure("V18_SOL_CONTENT_CHECKS_MISSING", scope, ",".join(missing)))
    if false_values:
        result.append(failure("V18_SOL_CONTENT_CHECK_REJECTED", scope, ",".join(false_values)))
    findings = review.get("segment_findings")
    if review.get("risk_codes") or not isinstance(findings, list) or not findings:
        result.append(failure("V18_SOL_REVIEW_NOT_EVIDENCE_BACKED", scope, "risk_codes/segment_findings"))
    else:
        if any(not isinstance(item, dict) or item.get("decision") != "pass" or not normalize_text(item.get("text")) for item in findings):
            result.append(failure("V18_SOL_SEGMENT_FINDING_INVALID", scope, "every segment requires exact text and pass/reject decision"))
        last = findings[-1] if isinstance(findings[-1], dict) else {}
        if normalize_text(last.get("purpose")) not in {"payoff", "benefit", "proof", "punchline", "releaseclose", "storyresolution", "收束", "结果", "结尾"}:
            result.append(failure("V18_SOL_ENDING_NOT_REVIEWED_AS_CLOSE", scope, str(last)))
        if _matches_any(last.get("text"), default_contract()["forbidden_numeric_endings"] + default_contract()["forbidden_method_endings"]):
            result.append(failure("V18_SOL_INVALID_ENDING_TEXT", scope, str(last.get("text"))))
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="JSON with plan_id, segments, transitions and policy")
    args = parser.parse_args()
    value = json.loads(args.input.read_text(encoding="utf-8"))
    failures = audit_plan(value["plan_id"], value["segments"], value.get("transitions", []), value.get("policy", {}))
    print(json.dumps({"decision": "reject" if failures else "pass", "failures": failures}, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
