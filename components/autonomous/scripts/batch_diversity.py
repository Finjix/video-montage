"""Best-effort batch selection of coherent montage alternatives; no quality approval."""
from __future__ import annotations

import copy
import re
from collections import Counter
from functools import lru_cache

REPORT_SCHEMA = "video-montage-batch-diversity/v1"


def normalized(text: str) -> str:
    return "".join(re.findall(r"[\w]", text.lower()))


def interval(segment: dict, head: bool = False) -> tuple[str, float, float]:
    scale = segment["source_fps_den"] / segment["source_fps_num"]
    start = segment["source_in_frame"] * scale
    end = segment["source_out_frame_exclusive"] * scale
    return segment["source_sha256"], start, min(end, start + 3) if head else end


def overlap(left: tuple, right: tuple) -> float:
    if left[0] != right[0]:
        return 0.0
    return max(0.0, min(left[2], right[2]) - max(left[1], right[1])) / max(
        1e-9, min(left[2] - left[1], right[2] - right[1]))


def same_segment(left: dict, right: dict, head: bool = False) -> bool:
    a, b = normalized(left["text"]), normalized(right["text"])
    # Extended takes retain the same hook. IDs, filenames and small cut shifts
    # never create a new opening. Cross-file repeated speech is counted too.
    speech_same = a == b or (head and len(a) >= 12 and len(b) >= 12 and a[:12] == b[:12])
    return speech_same or overlap(interval(left, head), interval(right, head)) >= .8


def merged_ranges(output: dict) -> dict[str, list[tuple[float, float]]]:
    sources = {}
    for segment in output["segments"]:
        source, start, end = interval(segment)
        sources.setdefault(source, []).append((start, end))
    for source, ranges in sources.items():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        sources[source] = merged
    return sources


def features(output: dict) -> dict:
    ranges = merged_ranges(output)
    return {"output": output, "ranges": ranges,
            "duration": sum(end - start for rows in ranges.values() for start, end in rows),
            "speech": normalized("".join(s["text"] for s in output["segments"]))}


def compare(left: dict, right: dict) -> dict:
    a, b = left["output"]["segments"], right["output"]["segments"]
    shared = sum(max(0, min(end_a, end_b) - max(start_a, start_b))
                 for source, rows in left["ranges"].items()
                 for start_a, end_a in rows for start_b, end_b in right["ranges"].get(source, []))
    coverage = shared / max(1e-9, min(left["duration"], right["duration"]))
    # A segment split/merge or an ID change does not disguise the same full edit.
    duplicate = left["speech"] == right["speech"] or (len(a) == len(b) and all(
        overlap(interval(x), interval(y)) >= .9
        and min(interval(x)[2] - interval(x)[1], interval(y)[2] - interval(y)[1])
        / max(interval(x)[2] - interval(x)[1], interval(y)[2] - interval(y)[1]) >= .9
        for x, y in zip(a, b)))
    pairs_a, pairs_b = list(zip(a, a[1:])), list(zip(b, b[1:]))
    pair_reuse = sum(any(same_segment(x, u) and same_segment(y, v) for u, v in pairs_b)
                     for x, y in pairs_a) / max(1, len(pairs_a), len(pairs_b))
    family_a = left["output"].get("opening_visual_family")
    family_b = right["output"].get("opening_visual_family")
    # Family labels come from Codex inspecting frames, never from a filename.
    family_same = bool(family_a and family_b and family_a == family_b)
    return {"same_opening": same_segment(a[0], b[0], head=True),
            "duplicate_sequence": duplicate, "source_overlap_ratio": round(coverage, 6),
            "shared_source_seconds": round(shared, 6), "adjacent_pair_reuse": round(pair_reuse, 6),
            "same_closing": same_segment(a[-1], b[-1]), "same_opening_visual_family": family_same}


def cost(pair: dict) -> tuple[int, int, int]:
    # Lexicographic: spread hooks first, then avoid whole-edit reuse, then reduce
    # body/order/closing/visual reuse. No hard quotas or diversity pass threshold.
    secondary = (4 * pair["source_overlap_ratio"] + 2 * pair["adjacent_pair_reuse"]
                 + pair["same_closing"] + 2 * pair["same_opening_visual_family"])
    return int(pair["same_opening"]), int(pair["duplicate_sequence"]), round(secondary * 1000000)


def add_cost(left: tuple, right: tuple) -> tuple:
    return tuple(a + b for a, b in zip(left, right))


def evaluate(outputs: list[dict]) -> dict:
    values = [features(output) for output in outputs]
    pairs = []
    total = (0, 0, 0)
    for i, left in enumerate(values):
        for j in range(i + 1, len(values)):
            finding = compare(left, values[j])
            total = add_cost(total, cost(finding))
            pairs.append({"left": outputs[i]["plan_id"], "right": outputs[j]["plan_id"], **finding})
    opening_pairs = [p for p in pairs if p["same_opening"]]
    warnings = []
    if opening_pairs:
        warnings.append(f"{len(opening_pairs)} pairs reuse an opening; check usable alternative hooks")
    if total[1]:
        warnings.append(f"{total[1]} pairs repeat a full sequence; prefer another coherent combination when available")
    return {"schema": REPORT_SCHEMA, "policy": "best_effort", "quality_approval": False,
            "output_count": len(outputs), "opening_repeat_pairs": total[0],
            "duplicate_sequence_pairs": total[1], "objective": list(total),
            "opening_visual_family_unlabeled": [o["plan_id"] for o in outputs if not o.get("opening_visual_family")],
            "warnings": warnings, "pairs": pairs}


def optimize(options: list[dict], count: int) -> tuple[list[dict], dict]:
    """Greedy selection plus bounded coordinate improvement, permitting reuse.

    Options must be coherent whole edits supplied by Codex; this never shuffles
    speech fragments. All selected edits still need normal media/semantic QC.
    """
    if not options or not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("nonempty coherent options and a positive output count required")
    values = [features(option) for option in options]

    @lru_cache(maxsize=None)
    def pair_cost(i: int, j: int) -> tuple:
        return cost(compare(values[min(i, j)], values[max(i, j)]))

    def incremental(option: int, selected: list[int]) -> tuple:
        score = (0, 0, 0)
        for other in selected:
            score = add_cost(score, pair_cost(min(option, other), max(option, other)))
        return score

    chosen = []
    for _ in range(count):
        chosen.append(min(range(len(options)), key=lambda i: (incremental(i, chosen), i)))
    # Polynomial, bounded search suitable for batches of 40/50; deterministic.
    passes = 0
    for _ in range(3):
        changed = False
        passes += 1
        for slot, old in enumerate(chosen):
            others = chosen[:slot] + chosen[slot + 1:]
            best = min(range(len(options)), key=lambda i: (incremental(i, others), i))
            if incremental(best, others) < incremental(old, others):
                chosen[slot] = best
                changed = True
        if not changed:
            break
    selected = []
    width = max(2, len(str(count)))
    for index, option in enumerate(chosen, 1):
        output = copy.deepcopy(options[option])
        output["plan_id"] = f"montage-{index:0{width}d}"
        selected.append(output)
    report = evaluate(selected)
    usage = Counter(chosen)
    report.update({"selection_method": "greedy_then_coordinate_improvement", "global_optimum_claimed": False,
                   "candidate_option_count": len(options), "improvement_passes": passes,
                   "selected_options": [{"plan_id": output["plan_id"], "option_plan_id": options[option]["plan_id"]}
                                        for output, option in zip(selected, chosen)],
                   "option_usage": [{"option_plan_id": options[i]["plan_id"], "count": usage[i]}
                                    for i in range(len(options))],
                   "capacity_note": "Repeat counts describe this supplied pool only, not all possible source edits. "
                                    "Expand usable coherent alternatives before calling repetition unavoidable."})
    return selected, report
