"""Whole-batch reuse limits over resolved outputs; identity is the source file plus the normalized words."""
from __future__ import annotations

from collections import defaultdict

from montage.rules import normalize

# The user has authorized source reuse for every batch, so these are the only limits; nothing is asked.
LIMITS = {"unit": 30, "text": 30, "opening": 8, "opening_family": 8, "opening_pair": 10, "closing": 16,
          "middle": 3, "overlap": .8, "similarity": .93}


def limits(decisions: dict) -> dict:
    return dict(LIMITS)


def trigram_similarity(left: str, right: str) -> float:
    def grams(value):
        return {value[i:i + 3] for i in range(max(1, len(value) - 2))}
    a, b = grams(left), grams(right)
    return len(a & b) / len(a | b) if a | b else 1.0


def audit(outputs: list[dict], decisions: dict) -> tuple[list[str], dict]:
    """Errors and usage counts. Each output has clips with `sha256`, `units` (texts) and shots with `family`."""
    cap = limits(decisions)
    errors = []
    uses = defaultdict(lambda: defaultdict(list))
    profiles = []
    for output in outputs:
        pid = output["plan_id"]
        units = [(clip["sha256"], normalize(text)) for clip in output["clips"] for text in clip["unit_texts"]]
        for unit in dict.fromkeys(units):
            uses["unit"][unit].append(pid)
            uses["text"][unit[1]].append(pid)
        clip_texts = [normalize(clip["text"]) for clip in output["clips"]]
        families = [shot["family"] for shot in output["shots"]]
        uses["opening"][normalize(output["clips"][0]["unit_texts"][0])].append(pid)
        uses["opening_family"][families[0]].append(pid)
        uses["opening_pair"][tuple(families[:2])].append(pid)
        uses["closing"][normalize(output["clips"][-1]["unit_texts"][-1])].append(pid)
        uses["route"][tuple((clip["sha256"], text) for clip, text in zip(output["clips"], clip_texts))].append(pid)
        uses["spoken"]["".join(clip_texts)].append(pid)
        if len(clip_texts) > 2:
            uses["middle"][tuple(clip_texts[1:-1])].append(pid)
        profiles.append((pid, clip_texts, "".join(clip_texts)))
    labels = {"unit": "the same unit", "text": "the same words", "opening": "the same opening words",
              "opening_family": "the same opening camera", "opening_pair": "the same first two cameras",
              "closing": "the same closing words", "route": "an identical clip sequence", "spoken": "identical speech",
              "middle": "the same middle clips (only head/tail changed)"}
    for name, rows in uses.items():
        limit = 1 if name in {"route", "spoken"} else cap[name]
        for key, plan_ids in rows.items():
            if len(plan_ids) > limit:
                sample = key[1] if name == "unit" else key
                errors.append(f"batch: {len(plan_ids)} outputs use {labels[name]} (limit {limit}): "
                              f"{','.join(plan_ids)} :: {str(sample)[:40]}")
    for i, (left_id, left_route, left_text) in enumerate(profiles):
        for right_id, right_route, right_text in profiles[i + 1:]:
            overlap = sum(a == b for a, b in zip(left_route, right_route)) / max(len(left_route), len(right_route))
            if overlap > cap["overlap"]:
                errors.append(f"batch: {left_id}/{right_id} share {overlap:.0%} of clip positions (limit {cap['overlap']:.0%})")
            similarity = trigram_similarity(left_text, right_text)
            if similarity > cap["similarity"]:
                errors.append(f"batch: {left_id}/{right_id} speech similarity {similarity:.2f} (limit {cap['similarity']})")
    summary = {"limits": cap, "openings": len(uses["opening"]), "opening_cameras": len(uses["opening_family"]),
               "closings": len(uses["closing"]),
               "most_used_unit": max((len(v) for v in uses["unit"].values()), default=0)}
    return errors, summary
