"""Route finder over the annotated beats.

`routes` lists every clip (a run of whole beats of one source) that can be cut cleanly and every join that
occurs in at least one route passing all machine checks, so the agent judges joins instead of searching.
`routes --joins <file>` builds the requested batch from the joins the agent accepted: every route passes
`check`, and the selection stays inside the batch limits.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from montage import batch, plan as PLAN, rules
from montage.common import read, write

MAX_CLIPS = 4
SEARCH_SECONDS = 40
PLACEHOLDER = "machine candidate join, reason still to be written by the agent"
PREFERRED = ("explanation", "progression", "claim_support", "cause_effect", "payoff", "benefit", "proof",
             "speaker_experience", "condition_result", "problem_solution", "contrast", "counterpoint", "escalation")
TWO_SPEAKERS = {"speaker_experience", "contrast", "counterpoint", "explanation", "claim_support", "proof"}


# ----------------------------------------------------------------------------------- clips

def clips(catalog: dict, registered: set[str]) -> tuple[list[dict], list[str]]:
    """Every cuttable run of 1-3 consecutive whole talk beats, and why single beats cannot be cut."""
    found, unusable = [], []
    for source in catalog["sources"]:
        beats = source.get("beats") or []
        index = {unit["id"]: number for number, unit in enumerate(source["units"])}
        for first in range(len(beats)):
            for last in range(first, min(first + 3, len(beats))):
                run = beats[first:last + 1]
                if last > first and index[run[-1]["units"][0]] != index[run[-2]["units"][-1]] + 1:
                    break
                if run[-1]["kind"] != "talk":
                    break
                ranks = [rules.ROLE_RANK[beat["role"]] for beat in run]
                if ranks != sorted(ranks) or any(beat["role"] == "hook" for beat in run[1:]):
                    break
                clip, reason = cut(source, run, registered)
                if clip:
                    found.append(clip)
                elif last == first:
                    unusable.append(f"{source['id']} {run[0]['id']} {run[0]['units'][0]}-{run[0]['units'][-1]}: {reason}")
    return found, unusable


def cut(source: dict, run: list[dict], registered: set[str]) -> tuple[dict | None, str]:
    spec = run[0]["units"][0] + (f"-{run[-1]['units'][-1]}" if run[-1]["units"][-1] != run[0]["units"][0] else "")
    units = PLAN.unit_slice(source, spec)
    if any(unit["excluded"] for unit in units):
        return None, "contains an excluded unit"
    if not units[0]["in_frames"]:
        return None, f"{units[0]['id']} cannot start a clip (no clean pause or stable picture before it)"
    if not units[-1]["out_frames"]:
        return None, f"{units[-1]['id']} cannot end a clip (no clean pause or stable picture after it)"
    options = [o for o in PLAN.clip_options(source, units) if min(o["pieces"]) >= PLAN.MIN_SHOT]
    if not options:
        return None, (f"every cut leaves a shot under {PLAN.MIN_SHOT} frames (speech starts or ends at a camera cut, "
                      "or a very short shot inside); join a neighbouring beat")
    options = [o for o in options if not PLAN.CATALOG.rejected_hits(source, o["in"], o["out"])]
    if not options:
        return None, "overlaps a user-rejected interval"
    best = min(options, key=lambda o: o["cost"])
    people = [next(s for s in source["shots"] if s["start"] <= left < s["end"])["people"] for left in best["edges"][:-1]]
    if any(not set(row) & registered for row in people):
        return None, "a shot shows no registered celebrity (or people are not annotated)"
    text = "".join(unit["text"] for unit in units)
    ends = [number for number, unit in enumerate(units[:-1]) if unit["sentence_end"]]
    first = next((number for number, unit in enumerate(units) if unit["sentence_end"]), len(units) - 1)
    return {"key": f"{source['id']}:{spec}", "source": source["id"], "sha256": source["sha256"], "units": spec,
            "beats": [beat["id"] for beat in run], "roles": [beat["role"] for beat in run],
            "gists": [beat["gist"] for beat in run], "text": text,
            "first_text": "".join(unit["text"] for unit in units[:first + 1]),
            "last_text": "".join(unit["text"] for unit in units[(ends[-1] + 1 if ends else 0):]), "first_speaker": units[0].get("speaker"), "last_speaker": units[-1].get("speaker"),
            "cast": tuple(sorted({p for row in people for p in row if p in registered})),
            "min_frames": min(o["total"] for o in options), "max_frames": max(o["total"] for o in options),
            "floor": max(min(o["pieces"]) for o in options), "ceiling": min(max(o["pieces"]) for o in options),
            "pieces": (min(min(o["pieces"]) for o in options), max(max(o["pieces"]) for o in options))}, ""


def relations(left: dict, right: dict) -> list[str]:
    """Relations the machine checks allow for the join left -> right (in preference order)."""
    text = rules.normalize(right["text"])
    allowed = set(rules.RELATIONS)
    for connector, permitted in rules.CONNECTORS.items():
        if text.startswith(connector):
            allowed &= permitted
            break
    if rules.contains(right["text"], rules.RESULT_ANCHORS):
        allowed &= rules.CAUSAL
    if rules.matches(right["text"], rules.FIRST_PERSON_RESULTS) and not rules.contains(left["text"], rules.FIRST_PERSON_SUBJECTS):
        allowed &= {"speaker_experience"}
    left_text = rules.normalize(left["text"])
    if left_text.startswith("只要") and not any(term in left_text for term in ("就", "便", "即可", "才能")):
        allowed &= {"condition_result"}
    if (left["last_speaker"] and right["first_speaker"] and left["last_speaker"] != right["first_speaker"]
            and re.search(rules.PERSONAL, left["text"]) and re.search(rules.PERSONAL, right["text"])):
        allowed &= TWO_SPEAKERS
    return [name for name in PREFERRED if name in allowed] + sorted(allowed - set(PREFERRED))


# ----------------------------------------------------------------------------------- search

def search(found: list[dict], links, *, min_sources: int, mix: bool, max_clips: int, seconds: float,
           verify) -> tuple[list[dict], bool]:
    """Routes hook/body -> ... -> payoff/cta through `links(left, right)` (relation or None), verified by `verify`.

    Start clips take turns so a time limit still samples every opening.
    """
    deadline = time.monotonic() + seconds
    by_cast = defaultdict(list)
    for clip in found:
        by_cast[clip["cast"]].append(clip)

    def walk(route, relations_so_far):
        last = route[-1]
        roles = [role for clip in route for role in clip["roles"]]
        low = sum(c["min_frames"] for c in route)
        high = sum(c["max_frames"] for c in route)
        if low > PLAN.MAX_FRAMES:
            return
        if (len({c["sha256"] for c in route}) >= max(2, min_sources) and last["roles"][-1] in ("payoff", "cta") and "body" in roles
                and high >= PLAN.MIN_FRAMES and len(route) >= PLAN.MIN_SHOTS
                and max(c["ceiling"] for c in route) - PLAN.MAX_SPREAD <= min(c["floor"] for c in route)):
            yield route, relations_so_far
        if len(route) >= max_clips or last["roles"][-1] == "cta":
            return
        used = {(c["sha256"], beat) for c in route for beat in c["beats"]}
        pool = found if mix else by_cast[route[0]["cast"]]
        for clip in pool:
            if (clip["sha256"] == last["sha256"] or any((clip["sha256"], beat) in used for beat in clip["beats"])
                    or clip["roles"][0] == "hook"):
                continue
            if rules.ROLE_RANK[clip["roles"][0]] < rules.ROLE_RANK[last["roles"][-1]]:
                continue
            relation = links(last, clip)
            if relation:
                yield from walk(route + [clip], relations_so_far + [relation])

    walkers = [walk([clip], []) for clip in found if clip["roles"][0] in ("hook", "body")]
    verified, truncated = [], False
    while walkers:
        if time.monotonic() > deadline:
            truncated = True
            break
        for walker in list(walkers):
            try:
                route, chosen = next(walker)
            except StopIteration:
                walkers.remove(walker)
                continue
            result = verify(route, chosen)
            if result:
                verified.append(result)
    return verified, truncated


def verifier(sources: dict, registered: set[str], games: list[str], min_sources: int, reasons=None):
    cache = {}

    def verify(route, chosen):
        if games and not any(rules.contains(clip["text"], [name]) for clip in route for name in games):
            return None
        output = {"plan_id": "candidate", "clips": [{"source": c["source"], "units": c["units"]} for c in route],
                  "transitions": [{"relation": relation, "reason": (reasons or {}).get((a["key"], b["key"]), PLACEHOLDER)}
                                  for a, b, relation in zip(route, route[1:], chosen)]}
        resolved, errors = PLAN.resolve_output(output, sources, registered, games, min_sources, cache)
        if errors:
            return None
        return {"route": route, "relations": chosen, "output": output, "resolved": resolved}
    return verify


# ----------------------------------------------------------------------------------- batch selection

class Usage:
    """Incremental form of the batch limits (batch.audit stays the final word)."""
    def __init__(self):
        self.counts = defaultdict(Counter)
        self.profiles = []

    @staticmethod
    def keys(resolved: dict) -> dict:
        texts = [rules.normalize(clip["text"]) for clip in resolved["clips"]]
        families = [shot["family"] for shot in resolved["shots"]]
        units = list(dict.fromkeys((clip["sha256"], rules.normalize(text)) for clip in resolved["clips"]
                                   for text in clip["unit_texts"]))
        keys = {"unit": units, "text": [text for _, text in units],
                "opening": [rules.normalize(resolved["clips"][0]["unit_texts"][0])], "opening_family": [families[0]],
                "opening_pair": [tuple(families[:2])], "closing": [rules.normalize(resolved["clips"][-1]["unit_texts"][-1])],
                "route": [tuple((clip["sha256"], text) for clip, text in zip(resolved["clips"], texts))],
                "spoken": ["".join(texts)]}
        if len(texts) > 2:
            keys["middle"] = [tuple(texts[1:-1])]
        return {"keys": keys, "texts": texts, "speech": "".join(texts)}

    def fits(self, profile: dict) -> bool:
        cap = batch.LIMITS
        for name, values in profile["keys"].items():
            limit = 1 if name in ("route", "spoken") else cap[name]
            if any(self.counts[name][value] + extra > limit for value, extra in Counter(values).items()):
                return False
        for other in self.profiles:
            overlap = sum(a == b for a, b in zip(other["texts"], profile["texts"])) / max(len(other["texts"]), len(profile["texts"]))
            if overlap > cap["overlap"] or batch.trigram_similarity(other["speech"], profile["speech"]) > cap["similarity"]:
                return False
        return True

    def add(self, profile: dict) -> None:
        for name, values in profile["keys"].items():
            for value in values:
                self.counts[name][value] += 1
        self.profiles.append(profile)


def select(candidates: list[dict], count: int) -> list[dict]:
    """Greedy pick: strong joins before weak, one celebrity before mixed casts, least-used clips first."""
    usage, chosen, clip_uses, opening_uses = Usage(), [], Counter(), Counter()
    for candidate in candidates:
        candidate["profile"] = Usage.keys(candidate["resolved"])
    remaining = list(candidates)
    while len(chosen) < count and remaining:
        remaining.sort(key=lambda c: (c["weak"], len(c["resolved"]["protagonists"]) > 1,
                                      opening_uses[c["route"][0]["key"]] * 3 + sum(clip_uses[x["key"]] for x in c["route"]),
                                      abs(len(c["route"]) - 3)))
        for position, candidate in enumerate(remaining):
            if usage.fits(candidate["profile"]):
                usage.add(candidate["profile"])
                chosen.append(candidate)
                clip_uses.update(c["key"] for c in candidate["route"])
                opening_uses[candidate["route"][0]["key"]] += 1
                del remaining[position]
                break
        else:
            break
    return chosen


# ----------------------------------------------------------------------------------- command

def joins_file(path: Path, by_key: dict) -> tuple[dict, list[str]]:
    value = read(path)
    rows = value.get("joins") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError('joins file: {"joins": [{"from": "S01:U02-U05", "to": "S03:U07", "relation": ..., "reason": ...}]}')
    accepted, problems = {}, []
    for number, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or not {"from", "to", "relation", "reason"} <= set(row)
                or not all(isinstance(row[key], str) for key in ("from", "to", "relation", "reason"))):
            problems.append(f"join {number}: needs from, to, relation and reason (strings)")
            continue
        left, right = by_key.get(row["from"]), by_key.get(row["to"])
        if not left or not right:
            problems.append(f"join {number}: unknown clip {row['from'] if not left else row['to']} (use keys from routes.md)")
            continue
        if row["relation"] not in relations(left, right):
            problems.append(f"join {number} {row['from']} -> {row['to']}: relation must be one of {relations(left, right)}")
            continue
        if not rules.specific(row["reason"]):
            problems.append(f"join {number} {row['from']} -> {row['to']}: reason must name the words that link the clips")
            continue
        accepted[(row["from"], row["to"])] = {"relation": row["relation"], "reason": row["reason"].strip(),
                                              "weak": bool(row.get("weak"))}
    return accepted, problems


def run(job: Path, state: dict, *, joins: Path | None, mix: bool, max_clips: int, seconds: float) -> list[str]:
    catalog = read(job / "catalog/catalog.json")
    sources = {source["id"]: source for source in catalog["sources"]}
    registered = {row["person_id"] for row in state["celebrities"]}
    library = read(job / "config/text_library.json")
    games = [entry["text"] for entry in library["entries"] if entry["category"] == "game"]
    min_sources = state.get("decisions", {}).get("min_sources_per_output", {}).get("value", PLAN.MIN_SOURCES)
    count = state["order"]["requested_outputs"]
    found, unusable = clips(catalog, registered)
    by_key = {clip["key"]: clip for clip in found}
    if joins is None:
        return explore(job, found, unusable, sources, registered, games, min_sources, mix, max_clips, seconds, count)
    accepted, problems = joins_file(joins, by_key)
    if problems:
        raise ValueError("joins file problems:\n  " + "\n  ".join(problems))
    reasons = {key: row["reason"] for key, row in accepted.items()}
    verified, truncated = search(found, lambda a, b: (accepted.get((a["key"], b["key"])) or {}).get("relation"),
                                 min_sources=min_sources, mix=True, max_clips=max_clips, seconds=seconds,
                                 verify=verifier(sources, registered, games, min_sources, reasons))
    for row in verified:
        row["weak"] = sum(accepted[(a["key"], b["key"])]["weak"] for a, b in zip(row["route"], row["route"][1:]))
    chosen = select(verified, count)
    outputs = []
    for number, row in enumerate(chosen, 1):
        output = dict(row["output"], plan_id=f"P{number:02d}")
        if row["weak"]:
            output["note"] = "weak join (topic link only): list it in the final summary"
        outputs.append(output)
    resolved = [dict(row["resolved"], plan_id=output["plan_id"]) for row, output in zip(chosen, outputs)]
    audit, _ = batch.audit(resolved, state.get("decisions", {})) if resolved else ([], {})
    target = job / "config/plan-draft.json"
    write(target, {"outputs": outputs})
    lines = [f"routes through the accepted joins: {len(verified)}" + (" (search stopped at the time limit)" if truncated else ""),
             f"draft plan: {target} ({len(outputs)} of {count} outputs, {sum(1 for r in chosen if r['weak'])} with weak joins)"]
    lines += [f"  batch: {problem}" for problem in audit]
    if len(outputs) < count:
        openings = Counter(row["route"][0]["key"] for row in verified)
        lines += [f"short by {count - len(outputs)}: the accepted joins allow {len(openings)} openings "
                  f"(at most {batch.LIMITS['opening']} videos each) and {len(verified)} routes. Accept more joins from "
                  "routes.md (weak ones last, marked \"weak\": true), cut finer beats and re-run annotate and routes, "
                  "or add --mix for cross-celebrity hand-overs"]
    else:
        lines.append("next: check --plan the draft, read reports/scripts.md, fix any join that does not read, then render")
    return lines


def explore(job, found, unusable, sources, registered, games, min_sources, mix, max_clips, seconds, count) -> list[str]:
    links = lambda a, b: (relations(a, b) or [None])[0]
    verified, truncated = search(found, links, min_sources=min_sources, mix=mix, max_clips=max_clips, seconds=seconds,
                                 verify=verifier(sources, registered, games, min_sources))
    joins = Counter()
    openings, closings = Counter(), Counter()
    for row in verified:
        route = row["route"]
        joins.update((a["key"], b["key"]) for a, b in zip(route, route[1:]))
        openings[route[0]["key"]] += 1
        closings[route[-1]["key"]] += 1
    by_key = {clip["key"]: clip for clip in found}
    names = {pid: pid.split(":", 1)[1] for pid in registered}
    lines = ["# Routes", "",
             f"{len(found)} cuttable clips, {len(verified)} routes pass every machine check"
             + (" (search stopped at the time limit; raise --seconds for more)" if truncated else "")
             + f", {len(joins)} distinct joins. Batch: {count} videos.",
             "A route is clips in role order, neighbours from different sources (a source may return later: a b a c).",
             "Judge each join below as a viewer: does the second clip answer, continue, prove or pay off the first?",
             "Accept the good ones in a joins file and run `routes --joins <file>` to build the batch.", "", "## Clips", ""]
    for clip in sorted(found, key=lambda c: (c["cast"], c["key"])):
        lines.append(f"{clip['key']}  {'/'.join(clip['roles'])}  {clip['min_frames'] / 60:.1f}-{clip['max_frames'] / 60:.1f} s  "
                     f"shots {clip['pieces'][0]}-{clip['pieces'][1]} f  cast {','.join(names[p] for p in clip['cast'])}  "
                     f"opens {openings[clip['key']]} / closes {closings[clip['key']]} routes")
        lines.append(f"   {clip['text']}")
        lines += [f"   ({gist})" for gist in clip["gists"]]
    if unusable:
        lines += ["", "## Beats that cannot be cut alone", ""] + unusable
    lines += ["", "## Joins", "", "Each clip's last words, then every clip that can follow it (its first words; routes using the join;",
              "the relations the checks allow when they are limited).", ""]
    following = defaultdict(list)
    for (left, right), uses in joins.items():
        following[left].append((right, uses))
    for left in sorted(following):
        lines.append(f"{left}  ...{by_key[left]['last_text']}")
        for right, uses in sorted(following[left]):
            allowed = relations(by_key[left], by_key[right])
            constraint = "" if len(allowed) == len(rules.RELATIONS) else f"  [{', '.join(allowed)}]"
            lines.append(f"   -> {right}  {by_key[right]['first_text']}...  ({uses}){constraint}")
    (job / "reports/routes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write(job / "reports/routes.json", {"clips": found, "joins": [{"from": a, "to": b, "routes": n} for (a, b), n in sorted(joins.items())]})
    example = '{"joins": [{"from": "S01:U02-U05", "to": "S03:U07-U09", "relation": "explanation", "reason": "...", "weak": false}]}'
    result = [f"routes: {job / 'reports/routes.md'} ({len(found)} clips, {len(verified)} verified routes, {len(joins)} joins, "
              f"{len(openings)} openings)" + ("; time limit reached" if truncated else "")]
    if unusable:
        result.append(f"{len(unusable)} beats cannot be cut alone (reasons listed in routes.md)")
    if len(openings) * batch.LIMITS["opening"] < count:
        result.append(f"warning: {len(openings)} openings serve at most {len(openings) * batch.LIMITS['opening']} videos; "
                      "cut more hook/body beats" + ("" if mix else " or add --mix"))
    result.append(f"next: read routes.md, write the joins you accept to a file ({example}), then routes --joins <file>")
    return result
