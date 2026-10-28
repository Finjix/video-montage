"""Whole-batch reuse limits over resolved outputs; identity is the source file plus the normalized words."""
from __future__ import annotations

from collections import defaultdict

from montage.rules import normalize

# The user has authorized source reuse for every batch, so these are the only limits; nothing is asked.
# same_position: two videos may share material at no more than 25% of their positions (the user's control rate);
# position_share: one piece of material may sit at one position in no more than 25% of the batch.
LIMITS = {"unit": 30, "text": 30, "opening": 8, "opening_family": 8, "opening_pair": 10, "closing": 16,
          "middle": 3, "same_position": .25, "position_share": .25, "similarity": .93}


def limits(decisions: dict) -> dict:
    return dict(LIMITS)


def positions(count: int) -> list[tuple[str, int]]:
    """Clip positions as a viewer compares them: the first half counted from the opening, the rest from the ending."""
    return [("head", index) if index < (count + 1) // 2 else ("tail", count - 1 - index) for index in range(count)]


def places(clips: list[dict]) -> dict[tuple[str, int], frozenset]:
    """Position -> the material at it (source file plus normalized words of each unit)."""
    return {label: frozenset((clip["sha256"], normalize(text)) for text in clip["unit_texts"])
            for label, clip in zip(positions(len(clips)), clips)}


def position_name(label: tuple[str, int]) -> str:
    end, index = label
    return f"clip {index + 1}" if end == "head" else "the last clip" if not index else f"clip {index + 1} from the end"


def shared_positions(left: dict, right: dict) -> int:
    return sum(1 for label, material in left.items() if material & right.get(label, frozenset()))


def same_position_share(left: dict, right: dict) -> float:
    """Share of the shorter video's positions that hold the same material in both videos."""
    return shared_positions(left, right) / max(1, min(len(left), len(right)))


def position_cap(count: int) -> int:
    """Videos of the batch that may hold one piece of material at one position."""
    return max(2, int(count * LIMITS["position_share"]))


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
        where = places(output["clips"])
        for label, material in where.items():
            for unit in material:
                uses["position"][(label, unit)].append(pid)
        profiles.append((pid, where, "".join(clip_texts)))
    labels = {"unit": "the same unit", "text": "the same words", "opening": "the same opening words",
              "opening_family": "the same opening camera", "opening_pair": "the same first two cameras",
              "closing": "the same closing words", "route": "an identical clip sequence", "spoken": "identical speech",
              "middle": "the same middle clips (only head/tail changed)", "position": "the same material at one position"}
    for name, rows in uses.items():
        limit = 1 if name in {"route", "spoken"} else position_cap(len(outputs)) if name == "position" else cap[name]
        for key, plan_ids in rows.items():
            if len(plan_ids) > limit:
                sample = key[1] if name == "unit" else f"{position_name(key[0])}: {key[1][1]}" if name == "position" else key
                errors.append(f"{len(plan_ids)} outputs use {labels[name]} (limit {limit}): "
                              f"{','.join(plan_ids)} :: {str(sample)[:40]}")
    for i, (left_id, left_places, left_text) in enumerate(profiles):
        for right_id, right_places, right_text in profiles[i + 1:]:
            share = same_position_share(left_places, right_places)
            if share > cap["same_position"]:
                errors.append(f"{left_id}/{right_id} hold the same material at {share:.0%} of their positions "
                              f"(limit {cap['same_position']:.0%})")
            similarity = trigram_similarity(left_text, right_text)
            if similarity > cap["similarity"]:
                errors.append(f"{left_id}/{right_id} speech similarity {similarity:.2f} (limit {cap['similarity']})")
    summary = {"limits": cap, "openings": len(uses["opening"]), "opening_cameras": len(uses["opening_family"]),
               "closings": len(uses["closing"]),
               "most_used_unit": max((len(v) for v in uses["unit"].values()), default=0),
               "most_used_at_one_position": max((len(v) for v in uses["position"].values()), default=0),
               "most_shared_positions": max((same_position_share(a[1], b[1]) for i, a in enumerate(profiles)
                                             for b in profiles[i + 1:]), default=0.0)}
    return errors, summary
