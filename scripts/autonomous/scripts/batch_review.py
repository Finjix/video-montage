"""Batch acceptance gates after semantic planning; never a clip selector."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import unicodedata

POLICY = "semantic-batch-review/v1"
LIMITS = {"candidate_uses": 6, "exact_text_uses": 6, "opening_uses": 3,
          "opening_visual_family_uses": 3, "opening_second_pair_uses": 2,
          "closing_uses": 4, "semantic_route_uses": 2,
          "same_position_overlap": .4, "trigram_similarity": .88}
REASONABLE_REUSE_POLICY = "explicit-user-reasonable-reuse/v1"
REASONABLE_LIMITS = {**LIMITS, "candidate_uses": 16, "exact_text_uses": 16,
    "opening_uses": 7, "opening_visual_family_uses": 7, "opening_second_pair_uses": 5,
    "closing_uses": 10, "semantic_route_uses": 4, "same_position_overlap": .6,
    "middle_route_uses": 3}


def reuse_limits(source_index: dict) -> dict:
    authorization = source_index.get("batch_reuse_authorization")
    if authorization is None:
        return LIMITS.copy()
    if (not isinstance(authorization, dict) or authorization.get("policy") != REASONABLE_REUSE_POLICY
            or authorization.get("authorized_by") != "user" or not isinstance(authorization.get("instruction"), str)
            or not authorization["instruction"].strip()):
        raise ValueError("explicit user instruction required for reasonable batch reuse")
    return REASONABLE_LIMITS.copy()


def text(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def candidate(segment: dict) -> tuple[str, str]:
    # Filenames, IDs, gain and tiny cut-boundary changes cannot create content.
    return segment["source_sha256"], text(segment["text"])


def source_route(segments: list[dict]) -> list[list[str]]:
    result = []
    for segment in segments:
        source, quote = candidate(segment)
        if result and result[-1][0] == source:
            result[-1][1] += quote
        else:
            result.append([source, quote])
    return result


def semantic_route(segments: list[dict]) -> list[tuple[str, str]]:
    result = []
    for segment in segments:
        item = (segment.get("semantic_cluster_id", text(segment["text"])),
                segment.get("purpose_contract", {}).get("function", ""))
        if not result or result[-1] != item:
            result.append(item)
    return result


def similarity(left: str, right: str) -> float:
    def grams(value):
        return {value[i:i + 3] for i in range(max(1, len(value) - 2))}
    a, b = grams(left), grams(right)
    return len(a & b) / len(a | b) if a | b else 1.0


def audit(plan: dict, source_index: dict) -> dict:
    limits_used = reuse_limits(source_index)
    failures = []
    def fail(code, scope, detail):
        failures.append({"code": code, "scope": scope, "detail": detail})
    if plan.get("batch_review_policy") != POLICY:
        fail("BATCH_REVIEW_POLICY_REQUIRED", "plan", POLICY)
    authorization = source_index.get("batch_reuse_authorization")
    if plan.get("batch_reuse_authorization") != authorization:
        fail("BATCH_REUSE_AUTHORIZATION_MISMATCH", "plan", "bind the source-index user authorization")
    rows = plan.get("outputs", [])
    sources = {row["source"]["sha256"]: row for row in source_index.get("sources", [])}
    inventory = plan.get("batch_analysis", {}).get("sources", [])
    inventory_ids = [row.get("source_sha256") for row in inventory]
    if len(inventory_ids) != len(sources) or set(inventory_ids) != set(sources):
        fail("CANDIDATE_INVENTORY_INCOMPLETE", "sources", "cover every work-order source")
    eligible = set()
    for entry in inventory:
        source = sources.get(entry.get("source_sha256"))
        if (source is None or entry.get("source_asr_sha256") != source.get("asr", {}).get("sha256")
                or entry.get("extraction_status") != "exhausted" or not entry.get("reason")
                or not isinstance(entry.get("candidates"), list)):
            fail("CANDIDATE_INVENTORY_INCOMPLETE", str(entry.get("source_sha256")),
                 "full source ASR binding, exhausted extraction and reasons required")
            continue
        for unit in entry["candidates"]:
            start, end = unit.get("source_in_frame"), unit.get("source_out_frame_exclusive")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= source["video"]["frames"]
                    or not isinstance(unit.get("text"), str) or not text(unit["text"])
                    or unit.get("status") not in {"eligible", "conditional", "rejected"} or not unit.get("reason")):
                fail("CANDIDATE_INVENTORY_ENTRY_INVALID", entry["source_sha256"], "native complete-turn evidence required")
                continue
            if unit["status"] == "eligible":
                eligible.add((entry["source_sha256"], start, end, text(unit["text"])))
    uses = {name: Counter() for name in ("candidate", "exact_text", "opening", "family", "pair", "closing", "route", "ordered", "spoken")}
    plans = []
    cluster_by_text = {}
    family_by_shot = {}
    native_shots = defaultdict(list)
    for row in rows:
        pid = row["plan_id"]
        segments = row["segments"]
        for segment in segments:
            key = candidate(segment)
            uses["candidate"][digest(key)] += 1
            uses["exact_text"][digest(key[1])] += 1
            bound = (key[0], segment["source_in_frame"], segment["source_out_frame_exclusive"], key[1])
            if bound not in eligible:
                fail("SELECTED_CANDIDATE_NOT_IN_EXHAUSTED_INVENTORY", pid, digest(bound))
            cluster = segment.get("semantic_cluster_id")
            if not isinstance(cluster, str) or not cluster.strip():
                fail("SEMANTIC_CLUSTER_REQUIRED", pid, key[1])
            elif key[1] in cluster_by_text and cluster_by_text[key[1]] != cluster:
                fail("RENAMED_SEMANTIC_CLUSTER", pid, key[1])
            else:
                cluster_by_text[key[1]] = cluster
        shots = row.get("visual_shots", [])
        for shot in shots:
            family = shot.get("visual_family_id")
            identity = (shot["source_sha256"], shot["source_visual_shot_id"])
            if not isinstance(family, str) or not family.strip():
                fail("ACTUAL_VISUAL_FAMILY_REQUIRED", pid, str(identity))
                continue
            if identity in family_by_shot and family_by_shot[identity] != family:
                fail("RENAMED_VISUAL_FAMILY", pid, str(identity))
            family_by_shot[identity] = family
            native_shots[shot["source_sha256"]].append((shot["source_in_frame"], shot["source_out_frame_exclusive"], family, pid))
        families = [shot.get("visual_family_id", "unreviewed") for shot in shots[:2]]
        opening = families[0] if families else "unreviewed"
        second = families[1] if len(families) > 1 else "unreviewed"
        uses["opening"][digest(text(segments[0]["text"]))] += 1
        uses["closing"][digest(text(segments[-1]["text"]))] += 1
        uses["family"][opening] += 1
        uses["pair"][digest([opening, second])] += 1
        route = semantic_route(segments)
        ordered = digest(source_route(segments))
        spoken = "".join(text(s["text"]) for s in segments)
        uses["ordered"][ordered] += 1
        uses["spoken"][digest(spoken)] += 1
        uses["route"][digest(route)] += 1
        plans.append({"plan_id": pid, "ordered_signature": ordered, "spoken_signature": digest(spoken),
                      "semantic_route_signature": digest(route), "route": route, "spoken": spoken,
                      "opening_visual_family_id": opening, "second_visual_family_id": second,
                      "middle_signature": digest([item[0] for item in semantic_route(segments[1:-1])]) if len(segments) > 2 else None,
                      "middle_spoken_signature": digest("".join(text(s["text"]) for s in segments[1:-1])) if len(segments) > 2 else None})
    # Even a renamed source-shot ID cannot conceal overlapping original frames.
    for source, spans in native_shots.items():
        for i, (start, end, family, pid) in enumerate(spans):
            for left, right, other, other_pid in spans[i + 1:]:
                if family != other and max(start, left) < min(end, right):
                    fail("RENAMED_OVERLAPPING_VISUAL_FAMILY", f"{pid}|{other_pid}", source)
    limits = {"candidate": (limits_used["candidate_uses"], "CANDIDATE_REUSE_EXCEEDED"), "exact_text": (limits_used["exact_text_uses"], "EXACT_TEXT_REUSE_EXCEEDED"),
              "opening": (limits_used["opening_uses"], "OPENING_REUSE_EXCEEDED"), "family": (limits_used["opening_visual_family_uses"], "OPENING_VISUAL_FAMILY_REUSE_EXCEEDED"),
              "pair": (limits_used["opening_second_pair_uses"], "OPENING_SECOND_VISUAL_PAIR_REUSE_EXCEEDED"), "closing": (limits_used["closing_uses"], "CLOSING_REUSE_EXCEEDED"),
              "route": (limits_used["semantic_route_uses"], "DUPLICATE_SEMANTIC_ROUTE"), "ordered": (1, "DUPLICATE_ORDERED_PLAN_SEQUENCE"),
              "spoken": (1, "DUPLICATE_COMPLETE_SPOKEN_CONTENT")}
    for name, (limit, code) in limits.items():
        for identity, count in uses[name].items():
            if count > limit:
                fail(code, identity, f"{count}>{limit}")
    comparisons = []
    middle_counts = Counter(row["middle_signature"] for row in plans if row["middle_signature"])
    middle_spoken_counts = Counter(row["middle_spoken_signature"] for row in plans if row["middle_spoken_signature"])
    for i, left in enumerate(plans):
        for right in plans[i + 1:]:
            pair = f"{left['plan_id']}|{right['plan_id']}"
            size = max(len(left["route"]), len(right["route"]))
            overlap = sum(a[0] == b[0] for a, b in zip(left["route"], right["route"])) / size
            trigram = similarity(left["spoken"], right["spoken"])
            same_middle = bool(left["middle_signature"] and (
                left["middle_signature"] == right["middle_signature"]
                or left["middle_spoken_signature"] == right["middle_spoken_signature"]))
            comparisons.append({"plans": pair, "same_position_overlap": round(overlap, 6),
                                "trigram_similarity": round(trigram, 6), "same_middle_route": same_middle})
            if overlap > limits_used["same_position_overlap"]:
                fail("SAME_POSITION_ROUTE_OVERLAP_EXCEEDED", pair, f"{overlap:.6f}>{limits_used['same_position_overlap']}")
            if trigram > limits_used["trigram_similarity"]:
                fail("PAIRWISE_TEXT_SIMILARITY_EXCEEDED", pair, f"{trigram:.6f}>{limits_used['trigram_similarity']}")
            if same_middle and max(middle_counts[left["middle_signature"]], middle_spoken_counts[left["middle_spoken_signature"]]) > limits_used.get("middle_route_uses", 1):
                fail("HEAD_OR_TAIL_ONLY_VARIANT", pair, "identical middle propositions")
    required_families = math.ceil(len(rows) / limits_used["opening_visual_family_uses"])
    if len(uses["family"]) < required_families or "unreviewed" in uses["family"]:
        fail("OPENING_VISUAL_CAPACITY_SHORTAGE", "batch", f"need {required_families} reviewed families")
    if len(uses["ordered"]) != len(rows) or len(uses["spoken"]) != len(rows):
        fail("UNIQUE_PLAN_CAPACITY_SHORTAGE", "batch", "duplicate plans cannot satisfy requested count")
    return {"schema": POLICY, "limits": limits_used, "batch_reuse_authorization": authorization, "decision": "reject" if failures else "pass",
            "failures": failures, "metrics": {"output_count": len(rows), "unique_ordered_routes": len(uses["ordered"]),
                "unique_spoken_routes": len(uses["spoken"]), "required_opening_families": required_families,
                "usage": {name: dict(counts) for name, counts in uses.items()}},
            "plans": [{k: v for k, v in row.items() if k not in {"route", "spoken", "middle_signature", "middle_spoken_signature"}} for row in plans],
            "comparisons": comparisons}


def review_errors(report: dict, review: dict) -> list[str]:
    finding = review.get("batch_review", {})
    if (review.get("batch_review_policy") != POLICY or finding.get("report_digest") != digest(report)
            or any(finding.get(key) is not True for key in ("source_inventory_pass", "semantic_routes_pass", "visual_families_pass", "reuse_pass"))
            or not isinstance(finding.get("reason"), str) or not finding["reason"].strip()):
        return ["HASH_BOUND_WHOLE_BATCH_REVIEW_REQUIRED"]
    by_id = {row.get("plan_id"): row for row in review.get("outputs", [])}
    errors = []
    for row in report["plans"]:
        finding = by_id.get(row["plan_id"], {})
        if (finding.get("opening_visual_family_id") != row["opening_visual_family_id"]
                or finding.get("second_visual_family_id") != row["second_visual_family_id"]
                or finding.get("opening_visual_pass") is not True or not finding.get("opening_visual_reason")
                or (review.get("stage") == "final" and (
                    type(finding.get("reviewed_opening_frame_count")) is not int or finding["reviewed_opening_frame_count"] < 60))):
            errors.append(f"{row['plan_id']}:ACTUAL_OPENING_AND_SECOND_SHOT_REVIEW_REQUIRED")
    return errors


def frame_digest(payload: str, expected_frames: int) -> str:
    rows = [line.split(",") for line in payload.splitlines() if line.strip() and not line.startswith("#")]
    if len(rows) != expected_frames or any(len(row) != 6 or not re.fullmatch(r"[0-9a-f]{32}", row[-1].strip()) for row in rows):
        raise ValueError("complete decoded clean-frame fingerprint required")
    return digest([row[-1].strip() for row in rows])


def clean_errors(rows: list[dict]) -> list[str]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["frames"], row["decoded_visual_digest"])].append(row["plan_id"])
    return ["DUPLICATE_RENDERED_CLEAN_VISUAL:" + "|".join(ids) for ids in groups.values() if len(ids) > 1]


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit(json.loads(args.plan.read_text(encoding="utf-8-sig")),
                   json.loads(args.source_index.read_text(encoding="utf-8-sig")))
    report.update(plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(),
                  source_index_sha256=hashlib.sha256(args.source_index.read_bytes()).hexdigest())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": len(report["failures"]),
                      "unique_routes": report["metrics"]["unique_ordered_routes"]}))
    raise SystemExit(0 if report["decision"] == "pass" else 2)
