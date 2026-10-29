"""Route finder over the annotated beats.

`routes` lists every clip (a run of whole beats of one source) that can be cut cleanly and every join that occurs
in a route passing all machine checks, grouped by the point (topic) a clip ends on and the point the next clip
opens with, so the agent judges joins instead of searching. `routes --joins <file>` builds the requested batch
from the joins the agent accepted (clip to clip, or topic to topic for beats that make the same point): every
route passes `check`, and the batch is composed video by video from the least-used material at each position,
inside the batch limits. Videos have no clip limit: the 17-30 s window and the shot rules bound them.
"""
from __future__ import annotations

import random
import re
import time
from collections import Counter, defaultdict
from itertools import count as count_from
from pathlib import Path

from montage import batch, plan as PLAN, rules, text_library
from montage.common import read, write

SEARCH_SECONDS = 40
NODE_BUDGET = 3000          # route-builder nodes per attempt from one opening clip
SATURATED = 1500            # explore stops when this many new routes add no new join
FINE_CLIPS = 6              # the batch prefers videos of at least this many clips (finer pieces, more sources)
PLACEHOLDER = "machine candidate join, reason still to be written by the agent"
PREFERRED = ("explanation", "progression", "claim_support", "cause_effect", "payoff", "benefit", "proof",
             "speaker_experience", "condition_result", "problem_solution", "contrast", "counterpoint", "escalation")
TWO_SPEAKERS = {"speaker_experience", "contrast", "counterpoint", "explanation", "claim_support", "proof"}
TOPIC = "topic:"


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
    topics = [beat.get("topic") for beat in run]
    said = sorted({point for beat in run for point in rules.points(beat)})
    return {"key": f"{source['id']}:{spec}", "source": source["id"], "sha256": source["sha256"], "units": spec,
            "beats": [beat["id"] for beat in run], "roles": [beat["role"] for beat in run],
            "gists": [beat["gist"] for beat in run], "topics": topics, "topic_in": topics[0], "topic_out": topics[-1],
            "points": said, "opens": run[0].get("opening", True), "closes": run[-1].get("closing", True),
            "text": text, "clauses": [rules.normalize(unit["text"]) for unit in units], "marks": rules.marks(text),
            "first_text": "".join(unit["text"] for unit in units[:first + 1]),
            "last_text": "".join(unit["text"] for unit in units[(ends[-1] + 1 if ends else 0):]),
            "first_speaker": units[0].get("speaker"), "last_speaker": units[-1].get("speaker"),
            "cast": tuple(sorted({p for row in people for p in row if p in registered})),
            "min_frames": min(o["total"] for o in options), "max_frames": max(o["total"] for o in options),
            "floor": max(min(o["pieces"]) for o in options), "ceiling": min(max(o["pieces"]) for o in options),
            "bands": PLAN.bands(options),
            "pieces": (min(min(o["pieces"]) for o in options), max(max(o["pieces"]) for o in options))}, ""


def relation_limits(left: dict, right: dict) -> tuple[list[str], list[str]]:
    """Relations the machine checks allow for the join left -> right (in preference order), and what limits them."""
    text = rules.normalize(right["text"])
    allowed, why = set(rules.RELATIONS), []
    for connector, permitted in rules.CONNECTORS.items():
        if text.startswith(connector):
            allowed &= permitted
            why.append(f"the second clip opens with {connector}")
            break
    if rules.contains(right["text"], rules.RESULT_ANCHORS):
        allowed &= rules.CAUSAL
        why.append("the second clip states a result")
    if rules.matches(right["text"], rules.FIRST_PERSON_RESULTS) and not rules.contains(left["text"], rules.FIRST_PERSON_SUBJECTS):
        allowed &= {"speaker_experience"}
        why.append("a first-person result follows a clip without a spoken subject")
    left_text = rules.normalize(left["text"])
    if left_text.startswith("只要") and not any(term in left_text for term in ("就", "便", "即可", "才能")):
        allowed &= {"condition_result"}
        why.append("the first clip opens with 只要 and no result")
    if (left["last_speaker"] and right["first_speaker"] and left["last_speaker"] != right["first_speaker"]
            and re.search(rules.PERSONAL, left["text"]) and re.search(rules.PERSONAL, right["text"])):
        allowed &= TWO_SPEAKERS
        why.append("two speakers' first-person accounts")
    return [name for name in PREFERRED if name in allowed] + sorted(allowed - set(PREFERRED)), why


def relations(left: dict, right: dict) -> list[str]:
    return relation_limits(left, right)[0]


def quotes(reason: str, text: str) -> bool:
    """The reason quotes at least one word (two or more Chinese characters) exactly as the clip says it."""
    spoken = rules.normalize(text)
    return any(run in spoken for run in re.findall(r"[\u3400-\u9fff]{2,}", reason))


def boundary(left: dict, right: dict) -> tuple:
    return left["source"], left["units"].split("-")[-1], right["source"], right["units"].split("-")[0]


def ends_on(clip: dict) -> str:
    """The group a join out of this clip belongs to: the topic it ends on, else the clip itself."""
    return TOPIC + clip["topic_out"] if clip["topic_out"] else clip["key"]


def opens_with(clip: dict) -> str:
    return TOPIC + clip["topic_in"] if clip["topic_in"] else clip["key"]


# ----------------------------------------------------------------------------------- route building

def can_open(clip: dict) -> bool:
    """Any clip that stands alone may open a video: not a cta, not marked opening: false, no leaning first word."""
    return ("cta" not in clip["roles"] and clip["opens"]
            and not rules.opening_problem(rules.normalize(clip["text"])))


def follow_problem(left: dict, right: dict, opening: bool = False) -> str | None:
    """Why the machine checks never let `right` follow `left` in a video, or None. With `opening`, `left` is the
    clip a video opens on, so the role order starts again at `right`."""
    if right["sha256"] == left["sha256"]:
        return "both clips come from one source file"
    if right["roles"][0] == "hook":
        return "a hook only opens a video"
    if not opening and rules.ROLE_RANK[right["roles"][0]] < rules.ROLE_RANK[left["roles"][-1]]:
        return f"a {right['roles'][0]} beat cannot follow a {left['roles'][-1]} beat (hook, body, payoff, cta)"
    if left["roles"].count("cta") + right["roles"].count("cta") > rules.MAX_CTA:
        return f"more than {rules.MAX_CTA} cta beats"
    if rules.discovery_jump(left["text"], right["text"]):
        return "a discovery hook cannot jump to a mechanic without a spoken gameplay bridge (怎么玩, 开局, 首先...)"
    if rules.restated(left["clauses"][-1], right["clauses"][0]):
        return "the second clip opens by restating the end of the first"
    return None


class Budget(Exception):
    pass


class Step:
    """What a partial route has used so far (each beat and point once; duration, shot band, cast)."""
    __slots__ = ("beats", "points", "cta", "low", "high", "bands", "marks", "shas", "cast", "body")

    def __init__(self, clip: dict, bands=None, previous: "Step | None" = None):
        roles, beats = clip["roles"], {(clip["sha256"], beat) for beat in clip["beats"]}
        points = set(clip["points"])
        if previous is None:
            self.beats, self.points, self.cta, self.low, self.high = frozenset(beats), frozenset(points), 0, 0, 0
            self.bands, self.marks, self.shas, self.cast, self.body = clip["bands"], frozenset(), frozenset(), frozenset(), False
        else:
            self.beats, self.points = previous.beats | beats, previous.points | points
            self.cta, self.low, self.high, self.bands = previous.cta, previous.low, previous.high, bands
            self.marks, self.shas, self.cast, self.body = previous.marks, previous.shas, previous.cast, previous.body
        self.cta += roles.count("cta")
        self.low += clip["min_frames"]
        self.high += clip["max_frames"]
        self.marks = self.marks.union(clip["marks"])
        self.shas |= {clip["sha256"]}
        self.cast |= set(clip["cast"])
        self.body = self.body or "body" in roles


class Walker:
    """Depth-first route builder over `links(left, right)` (a relation or None): clips in role order, neighbours from
    different sources, each beat, point (topic) and clause once, inside the duration and shot-length window.

    Without `mix` a route keeps one cast, except that clips of a `stranded` cast (one whose own clips finish no route)
    may hand over to or from it. `min_cast` > 1 (the user's decision) keeps only routes featuring that many celebrities.
    """

    def __init__(self, found: list[dict], links, *, min_sources: int, mix: bool, max_clips: int | None = None,
                 min_cast: int = 1, stranded: frozenset = frozenset()):
        self.min_sources, self.mix, self.max_clips, self.min_cast, self.stranded = min_sources, mix, max_clips, min_cast, stranded
        self.relation, self.next, self.after_opening, self.clash = {}, defaultdict(list), defaultdict(list), {}
        self.starts = [clip for clip in found if can_open(clip)]
        openers = {clip["key"] for clip in self.starts}
        for left in found:
            for right in found:
                middle = self.may_follow(left, right)
                first = left["key"] in openers and follow_problem(left, right, opening=True) is None
                if (middle or first) and (relation := links(left, right)):
                    self.relation[(left["key"], right["key"])] = relation
                    if middle:
                        self.next[left["key"]].append(right)
                    if first:
                        self.after_opening[left["key"]].append(right)

    @staticmethod
    def may_follow(left: dict, right: dict) -> bool:
        return follow_problem(left, right) is None

    def clashes(self, left: dict, right: dict) -> bool:
        """The two clips say one clause twice (word for word, or nearly)."""
        key = (left["key"], right["key"])
        if key not in self.clash:
            self.clash[key] = any((a == b and len(a) >= 4) or rules.same_clause(a, b)
                                  for a in left["clauses"] for b in right["clauses"])
        return self.clash[key]

    def allowed(self, route: list[dict], clip: dict) -> bool:
        if self.min_cast > 1 or self.mix or clip["cast"] in self.stranded:
            return True
        casts = {c["cast"] for c in route if c["cast"] not in self.stranded}
        return not casts or clip["cast"] in casts

    def children(self, route: list[dict], step: Step) -> dict[str, tuple[dict, list]]:
        """Clip key -> (clip, the shot band left) for every clip that may extend the route."""
        if self.max_clips and len(route) >= self.max_clips:
            return {}
        result = {}
        for clip in (self.after_opening if len(route) == 1 else self.next)[route[-1]["key"]]:
            if (step.low + clip["min_frames"] > PLAN.MAX_FRAMES or step.cta + clip["roles"].count("cta") > rules.MAX_CTA
                    or any((clip["sha256"], beat) in step.beats for beat in clip["beats"])
                    or any(point in step.points for point in clip["points"])
                    or rules.blocked(step.marks, clip["marks"]) or not self.allowed(route, clip)
                    or any(self.clashes(earlier, clip) for earlier in route)):
                continue
            if bands := PLAN.intersect(step.bands, clip["bands"]):
                result[clip["key"]] = (clip, bands)
        return result

    def complete(self, route: list[dict], step: Step) -> bool:
        return (route[-1]["roles"][-1] in ("payoff", "cta") and route[-1]["closes"] and step.body and len(route) >= PLAN.MIN_SHOTS
                and route[-1]["min_frames"] >= rules.MIN_CLOSING_SECONDS * 60 and step.high >= PLAN.MIN_FRAMES
                and len(step.shas) >= max(2, self.min_sources) and len(step.cast) >= self.min_cast)

    def first(self, start: dict, order, accept, budget: int = NODE_BUDGET):
        """The first route from `start` that `accept(route, relations)` takes (its result), trying children in
        `order(route, clips)`; None when none is found within `budget` nodes."""
        nodes = 0

        def walk(route, chosen, step):
            nonlocal nodes
            nodes += 1
            if nodes > budget:
                raise Budget
            if self.complete(route, step) and (result := accept(route, chosen)):
                return result
            options = self.children(route, step)
            for clip in order(route, [clip for clip, _ in options.values()]):
                result = walk(route + [clip], chosen + [self.relation[(route[-1]["key"], clip["key"])]],
                              Step(clip, options[clip["key"]][1], step))
                if result:
                    return result
            return None
        try:
            return walk([start], [], Step(start))
        except Budget:
            return None


def search(found: list[dict], links, *, min_sources: int, mix: bool, max_clips: int | None = None, seconds: float,
           verify, stranded: frozenset = frozenset(), limit: int | None = None, min_cast: int = 1,
           seed: int = 0) -> tuple[list[dict], bool]:
    """Verified routes sampled hook/body -> ... -> payoff/cta: every opening takes a turn per round, and each turn
    walks the joins in a fresh random order to the next route not seen before. Stops at the time limit (truncated),
    after `limit` routes, or when rounds stop finding new routes or new joins."""
    walker = Walker(found, links, min_sources=min_sources, mix=mix, max_clips=max_clips, min_cast=min_cast,
                    stranded=stranded)
    rng = random.Random(seed)
    deadline = time.monotonic() + seconds
    verified, seen, joins = [], set(), set()
    since_new_join = 0

    def order(route, children):
        rng.shuffle(children)
        return children

    def accept(route, chosen):
        signature = tuple(clip["key"] for clip in route)
        if signature in seen:
            return None
        seen.add(signature)
        return verify(route, chosen)

    starts, idle = list(walker.starts), 0
    while starts and idle < 2:
        rng.shuffle(starts)
        progress = False
        for start in starts:
            if time.monotonic() > deadline:
                return verified, True
            result = walker.first(start, order, accept)
            if not result:
                continue
            verified.append(result)
            progress = True
            pairs = {(a["key"], b["key"]) for a, b in zip(result["route"], result["route"][1:])}
            since_new_join = 0 if pairs - joins else since_new_join + 1
            joins |= pairs
            if (limit and len(verified) >= limit) or since_new_join >= SATURATED:
                return verified, False
        idle = 0 if progress else idle + 1
    return verified, False


def stranded_casts(found: list[dict], links, *, min_sources: int, max_clips: int | None, verify) -> frozenset:
    """Casts whose own clips finish no route: editing lets such a celebrity hand over to another one."""
    by_cast = defaultdict(list)
    for clip in found:
        by_cast[clip["cast"]].append(clip)
    return frozenset(cast for cast, own in by_cast.items()
                     if not search(own, links, min_sources=min_sources, mix=False, max_clips=max_clips, seconds=2.0,
                                   verify=verify, limit=1)[0])


def verifier(sources: dict, registered: set[str], games: list[str], min_sources: int, reasons=None, min_cast: int = 1):
    cache = {}

    def verify(route, chosen):
        if games and not any(rules.contains(clip["text"], [name]) for clip in route for name in games):
            return None
        output = {"plan_id": "candidate", "clips": [{"source": c["source"], "units": c["units"]} for c in route],
                  "transitions": [{"relation": relation, "reason": (reasons or {}).get((a["key"], b["key"]), PLACEHOLDER)}
                                  for a, b, relation in zip(route, route[1:], chosen)]}
        resolved, errors = PLAN.resolve_output(output, sources, registered, games, min_sources, cache, min_cast)
        if errors:
            return None
        return {"route": route, "relations": chosen, "output": output, "resolved": resolved}
    return verify


# ----------------------------------------------------------------------------------- batch composition

class Usage:
    """Incremental form of the batch limits (batch.audit stays the final word); `count` is the batch size."""
    def __init__(self, count: int | None = None):
        self.counts = defaultdict(Counter)
        self.profiles = []
        self.position_cap = batch.position_cap(count) if count else None

    @staticmethod
    def keys(resolved: dict) -> dict:
        texts = [rules.normalize(clip["text"]) for clip in resolved["clips"]]
        families = [shot["family"] for shot in resolved["shots"]]
        units = list(dict.fromkeys((clip["sha256"], rules.normalize(text)) for clip in resolved["clips"]
                                   for text in clip["unit_texts"]))
        places = batch.places(resolved["clips"])
        keys = {"unit": units, "text": [text for _, text in units],
                "opening": [rules.normalize(resolved["clips"][0]["unit_texts"][0])], "opening_family": [families[0]],
                "opening_pair": [tuple(families[:2])], "closing": [rules.normalize(resolved["clips"][-1]["unit_texts"][-1])],
                "route": [tuple((clip["sha256"], text) for clip, text in zip(resolved["clips"], texts))],
                "spoken": ["".join(texts)],
                "position": [(label, unit) for label, material in places.items() for unit in material]}
        if len(texts) > 2:
            keys["middle"] = [tuple(texts[1:-1])]
        return {"keys": keys, "texts": texts, "speech": "".join(texts), "places": places}

    def limit(self, name: str) -> int | None:
        return 1 if name in ("route", "spoken") else self.position_cap if name == "position" else batch.LIMITS[name]

    def fits(self, profile: dict) -> bool:
        for name, values in profile["keys"].items():
            limit = self.limit(name)
            if limit is not None and any(self.counts[name][value] + extra > limit for value, extra in Counter(values).items()):
                return False
        for other in self.profiles:
            if (batch.same_position_share(other["places"], profile["places"]) > batch.LIMITS["same_position"]
                    or batch.trigram_similarity(other["speech"], profile["speech"]) > batch.LIMITS["similarity"]):
                return False
        return True

    def add(self, profile: dict) -> None:
        for name, values in profile["keys"].items():
            for value in values:
                self.counts[name][value] += 1
        self.profiles.append(profile)


def compose(walker: Walker, verify, count: int, accepted: dict, *, keep: list[list[dict]] = (), seconds: float,
            seed: int = 0) -> list[dict]:
    """The batch, video by video: videos of the current plan that still pass and fit first, then new routes, strong
    joins before weak ones and videos of at least FINE_CLIPS clips before shorter ones. Each new video opens, for a
    celebrity still under its fair share, on the point and source file that have opened fewest videos (any clip that
    stands alone, not only hooks) and continues, position by position, with the least-used material a join allows, preferring (when the user asked for several celebrities) a celebrity not yet in the video,
    a new source file and single beats over runs, so videos are many short pieces from many sources."""
    rng = random.Random(seed)
    deadline = time.monotonic() + seconds
    usage, chosen = Usage(count), []
    at_position, opening_uses, featured, leads = Counter(), Counter(), Counter(), Counter()
    opening_points, opening_sources = Counter(), Counter()
    point = lambda clip: clip["topic_in"] or clip["key"]
    people = {p for clip in walker.starts for p in clip["cast"]}
    share = count // max(1, len(people))
    material = lambda clip: frozenset((clip["sha256"], clause) for clause in clip["clauses"])
    used = lambda clip: max(usage.counts["unit"][unit] for unit in material(clip))   # videos holding its most-used unit

    def fitting(row):
        if row is None:
            return None
        row["profile"] = Usage.keys(row["resolved"])
        return row if usage.fits(row["profile"]) else None

    def add(row):
        usage.add(row["profile"])
        chosen.append(row)
        route = row["route"]
        at_position.update((label, c["key"]) for label, c in zip(batch.positions(len(route)), route))
        opening_uses[route[0]["key"]] += 1
        opening_points[point(route[0])] += 1
        opening_sources[route[0]["sha256"]] += 1
        featured.update(row["resolved"]["protagonists"])
        leads.update(route[0]["cast"])

    def crowded(index: int, clip: dict, route: list[dict], target: int) -> bool:
        """The clip at `index` (a position counted from the opening in every video of >= target clips) would hold
        material too many videos hold there, or give the route too many positions in common with a chosen video."""
        if index >= (target + 1) // 2:
            return False
        stuff = material(clip)
        if any(usage.counts["position"][(("head", index), unit)] >= usage.position_cap for unit in stuff):
            return True
        if index == 0 and (usage.counts["opening"][clip["clauses"][0]] >= batch.LIMITS["opening"]):
            return True
        prefix = [material(c) for c in route] + [stuff]
        for other in usage.profiles:
            places = other["places"]
            shared = sum(1 for i, held in enumerate(prefix) if held & places.get(("head", i), frozenset()))
            if shared > batch.LIMITS["same_position"] * len(places):
                return True
        return False

    for route in keep:
        if (len(chosen) < count and all((a["key"], b["key"]) in walker.relation for a, b in zip(route, route[1:]))
                and (row := fitting(verify(route, [walker.relation[(a["key"], b["key"])] for a, b in zip(route, route[1:])])))):
            row["kept"] = True
            add(row)

    for allow_weak, target in ((False, FINE_CLIPS), (False, PLAN.MIN_SHOTS), (True, PLAN.MIN_SHOTS)):
        def order(route, children):
            index, sources = len(route), {c["sha256"] for c in route}
            cast = {p for c in route for p in c["cast"]}
            lead = route[0]["cast"]
            rows = []
            for clip in children:
                weak = accepted[(route[-1]["key"], clip["key"])]["weak"]
                if (weak and not allow_weak) or crowded(index, clip, route, target):
                    continue
                if walker.min_cast > 1:
                    blend = 0 if len(cast) >= walker.min_cast or set(clip["cast"]) - cast else 1
                else:
                    blend = 0 if clip["cast"] == lead else 1
                rows.append(((weak, blend, at_position[(("head", index), clip["key"])], used(clip),
                              clip["sha256"] in sources, len(clip["beats"]), rng.random()), clip))
            return [clip for _, clip in sorted(rows, key=lambda row: row[0])]

        def accept(route, chosen_relations):
            return fitting(verify(route, chosen_relations)) if len(route) >= target else None

        failed = set()
        while len(chosen) < count and time.monotonic() < deadline:
            starts = sorted((c for c in walker.starts if c["key"] not in failed and not crowded(0, c, [], target)),
                            key=lambda c: (all(featured[p] >= share for p in c["cast"]),
                                           min((leads[p] for p in c["cast"]), default=0),
                                           opening_points[point(c)] + opening_sources[c["sha256"]],
                                           usage.counts["opening"][c["clauses"][0]],
                                           used(c), len(c["beats"]), rng.random()))
            row = None
            for start in starts:
                if time.monotonic() > deadline:
                    break
                if row := walker.first(start, order, accept):
                    break
                failed.add(start["key"])
            if row is None:
                break
            row["weak"] = sum(accepted[(a["key"], b["key"])]["weak"] for a, b in zip(row["route"], row["route"][1:]))
            add(row)
    for row in chosen:
        row.setdefault("weak", sum(accepted[(a["key"], b["key"])]["weak"] for a, b in zip(row["route"], row["route"][1:])))
    return chosen


def signature(output: dict) -> tuple:
    return tuple((clip.get("source"), clip.get("units")) for clip in output.get("clips") or [] if isinstance(clip, dict))


def previous_ids(job: Path) -> dict[tuple, str]:
    """Clip sequence -> plan_id in the current plan (else the last draft): a re-run keeps those videos and their IDs,
    so render and package reuse their work."""
    for name in ("config/plan.json", "config/plan-draft.json"):
        try:
            rows = read(job / name).get("outputs") or []
        except (OSError, ValueError, AttributeError):
            continue
        return {signature(row): row["plan_id"] for row in rows if isinstance(row, dict) and isinstance(row.get("plan_id"), str)}
    return {}


# ----------------------------------------------------------------------------------- joins file

def joins_file(path: Path, by_key: dict) -> tuple[dict, list[str], list[str]]:
    """Accepted clip pairs -> {relation, reason, weak}, problems, and notes on topic joins that skip some pairs.

    `from`/`to` name a clip key or `topic:<slug>` (every clip ending on / opening with that topic). A clip-to-clip
    join overrides a topic join for its pair; `"reject": true` removes the pairs it names from every accepted join."""
    value = read(path)
    rows = value.get("joins") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError('joins file: {"joins": [{"from": "S01:U02-U05", "to": "S03:U07", "relation": ..., "reason": ...}]}')
    ending, opening = defaultdict(list), defaultdict(list)
    for clip in by_key.values():
        if clip["topic_out"]:
            ending[clip["topic_out"]].append(clip)
        if clip["topic_in"]:
            opening[clip["topic_in"]].append(clip)

    def side(name, topics):
        if name.startswith(TOPIC):
            return topics.get(name[len(TOPIC):], [])
        return [by_key[name]] if name in by_key else []

    accepted, rank, rejected, problems, notes, reused = {}, {}, set(), [], [], {}
    for number, row in enumerate(rows, 1):
        reject = isinstance(row, dict) and row.get("reject") is True
        needed = ("from", "to", "reason") if reject else ("from", "to", "relation", "reason")
        if not isinstance(row, dict) or not set(needed) <= set(row) or not all(isinstance(row[key], str) for key in needed):
            problems.append(f"join {number}: needs from, to, relation and reason (strings); a rejection needs from, to, "
                            "reason and \"reject\": true")
            continue
        left, right = side(row["from"], ending), side(row["to"], opening)
        if not left or not right:
            missing = row["from"] if not left else row["to"]
            problems.append(f"join {number}: unknown clip or topic {missing} (use clip keys or topics from routes.md; a "
                            "topic on the from side is the one a clip ends on, on the to side the one it opens with)")
            continue
        where = f"join {number} {row['from']} -> {row['to']}"
        if reject:
            rejected.update((a["key"], b["key"]) for a in left for b in right)
            continue
        pairs = [(a, b) for a in left for b in right
                 if Walker.may_follow(a, b) or (can_open(a) and follow_problem(a, b, opening=True) is None)]
        if not pairs:
            notes.append(f"{where} is never used: " + (follow_problem(left[0], right[0]) if len(left) == len(right) == 1 else
                                                         "no clip of the second side may follow one of the first"))
            continue
        fitting = [(a, b) for a, b in pairs if row["relation"] in relation_limits(a, b)[0]]
        if not fitting:
            if len(pairs) == 1:
                allowed, why = relation_limits(*pairs[0])
                problems.append(f"{where}: relation must be one of {allowed}" + (f" ({'; '.join(why)})" if why else ""))
            else:
                problems.append(f"{where}: no clip pair of these sides takes {row['relation']} (see the bracketed relations "
                                "in routes.md)")
            continue
        if not rules.specific(row["reason"]):
            problems.append(f"{where}: reason must name the words that link the clips")
            continue
        missing = [name for name, members in (("first", left), ("second", right))
                   if not quotes(row["reason"], "".join(clip["text"] for clip in members))]
        if missing:
            problems.append(f"{where}: the reason must quote the linking words of both clips as spoken; nothing quoted from "
                            f"the {' and '.join(missing)} clip")
            continue
        if len(fitting) < len(pairs):
            notes.append(f"{where}: {len(pairs) - len(fitting)} of {len(pairs)} clip pairs left out "
                         f"({row['relation']} does not fit their words)")
        level = (not row["from"].startswith(TOPIC)) + (not row["to"].startswith(TOPIC))
        for a, b in fitting:
            pair = (a["key"], b["key"])
            if rank.get(pair, -1) <= level:
                accepted[pair] = {"relation": row["relation"], "reason": row["reason"].strip(), "weak": bool(row.get("weak"))}
                rank[pair] = level
        ends = (row["from"] if row["from"].startswith(TOPIC) else boundary(left[0], right[0])[:2],
                row["to"] if row["to"].startswith(TOPIC) else boundary(left[0], right[0])[2:])
        reused.setdefault(rules.normalize(row["reason"]), {}).setdefault(ends, number)
    for joins in reused.values():
        if len(joins) > 1:
            problems.append(f"joins {', '.join(map(str, sorted(joins.values())))} share one reason across different clip "
                            "boundaries; judge each join on its own words and write its own reason")
    for pair in rejected:
        accepted.pop(pair, None)
    return accepted, problems, notes


# ----------------------------------------------------------------------------------- command

def run(job: Path, state: dict, *, joins: Path | None, mix: bool, max_clips: int | None, seconds: float,
        fresh: bool = False) -> list[str]:
    if max_clips is not None and max_clips < PLAN.MIN_SHOTS:
        raise ValueError(f"--max-clips {max_clips} finds no route: a video needs >= {PLAN.MIN_SHOTS} clips")
    min_cast = state.get("decisions", {}).get("min_celebrities_per_output", {}).get("value", 1)
    if max_clips is not None and min_cast > max_clips:
        raise ValueError(f"--max-clips {max_clips} cannot feature {min_cast} celebrities per video; raise --max-clips")
    catalog = read(job / "catalog/catalog.json")
    sources = {source["id"]: source for source in catalog["sources"]}
    registered = {row["person_id"] for row in state["celebrities"]}
    library = text_library.load(job)
    games = [entry["text"] for entry in library["entries"] if entry["category"] == "game"]
    min_sources = state.get("decisions", {}).get("min_sources_per_output", {}).get("value", PLAN.MIN_SOURCES)
    count = state["order"]["requested_outputs"]
    found, unusable = clips(catalog, registered)
    by_key = {clip["key"]: clip for clip in found}
    if joins is None:
        return explore(job, found, unusable, sources, registered, games, min_sources, mix, max_clips, seconds, count,
                       min_cast)
    accepted, problems, notes = joins_file(joins, by_key)
    if problems:
        raise ValueError("joins file problems:\n  " + "\n  ".join(problems))
    reasons = {key: row["reason"] for key, row in accepted.items()}
    walker = Walker(found, lambda a, b: (accepted.get((a["key"], b["key"])) or {}).get("relation"),
                    min_sources=min_sources, mix=True, max_clips=max_clips, min_cast=min_cast)
    verify = verifier(sources, registered, games, min_sources, reasons, min_cast)
    previous = {} if fresh else previous_ids(job)
    keep = []
    for sig, _ in sorted(previous.items(), key=lambda row: (len(row[1]), row[1])):
        route = [by_key.get(f"{source}:{units}") for source, units in sig]
        if route and all(route):
            keep.append(route)
    started = time.monotonic()
    chosen = compose(walker, verify, count, accepted, keep=keep, seconds=max(seconds, 60.0))
    kept = {signature(row["output"]): previous[signature(row["output"])] for row in chosen if row.get("kept")}
    free = (f"P{number:02d}" for number in count_from(1) if f"P{number:02d}" not in kept.values())
    outputs = []
    for row in chosen:
        output = dict(row["output"], plan_id=kept.get(signature(row["output"])) or next(free))
        if row["weak"]:
            output["note"] = "weak join (topic link only): list it in the final summary"
        outputs.append(output)
    order = sorted(range(len(outputs)), key=lambda i: (len(outputs[i]["plan_id"]), outputs[i]["plan_id"]))
    chosen, outputs = [chosen[i] for i in order], [outputs[i] for i in order]
    resolved = [dict(row["resolved"], plan_id=output["plan_id"]) for row, output in zip(chosen, outputs)]
    audit, summary = batch.audit(resolved, state.get("decisions", {})) if resolved else ([], {})
    target = job / "config/plan-draft.json"
    write(target, {"outputs": outputs})
    openings = sum(1 for clip in walker.starts if walker.after_opening[clip["key"]])
    endings = len({right for _, right in walker.relation if by_key[right]["roles"][-1] in ("payoff", "cta")})
    lines = [f"accepted joins: {len(accepted)} clip pairs ({openings} openings, {endings} endings)"]
    lines += [f"  note: {note}" for note in notes]
    lines.append(f"draft plan: {target} ({len(outputs)} of {count} outputs, {sum(1 for r in chosen if r['weak'])} with weak "
                 f"joins; composed in {time.monotonic() - started:.0f} s)")
    if resolved:
        clip_counts = Counter(len(row["route"]) for row in chosen)
        source_counts = [len({clip["sha256"] for clip in row["route"]}) for row in chosen]
        lines.append(f"shape: clips per video {', '.join(f'{n} x{clip_counts[n]}' for n in sorted(clip_counts))}; source files "
                     f"per video {min(source_counts)}-{max(source_counts)} (mean {sum(source_counts) / len(source_counts):.1f})")
        lines.append(f"repetition: at most {summary['most_shared_positions']:.0%} of positions shared by two videos (limit "
                     f"{batch.LIMITS['same_position']:.0%}), one piece of material at one position in at most "
                     f"{summary['most_used_at_one_position']} videos (limit {batch.position_cap(count)}), most-used unit "
                     f"x{summary['most_used_unit']}")
    lines += [f"  batch: {problem}" for problem in audit]
    if kept:
        lines.append(f"kept {len(kept)} videos of the previous plan under their plan IDs (render and package reuse them)")
    names = {row["person_id"]: row.get("name") or row["person_id"].split(":", 1)[1] for row in state["celebrities"]}
    cast = Counter(p for row in chosen for p in row["resolved"]["protagonists"])
    lines.append("cast: " + ", ".join(f"{names[p]} {cast[p]}" for p in names))
    if absent := [names[p] for p in names if not cast[p]]:
        lines.append(f"no video features {', '.join(absent)}: accept joins into or out of their clips in routes.md (a "
                     "celebrity whose own clips finish no video hands over to another), or cut finer beats; if nothing "
                     "works, say why in the final summary")
    if len(outputs) < count:
        lines.append(f"short by {count - len(outputs)}: no further route through the accepted joins fits the batch limits "
                     f"(two videos share material at no more than {batch.LIMITS['same_position']:.0%} of positions; one "
                     f"opening serves at most {batch.LIMITS['opening']} videos). Accept more joins from routes.md (a topic "
                     "join covers every clip of both topics), give interchangeable beats a shared topic, cut finer beats "
                     "and re-run annotate and routes; weak joins last, marked \"weak\": true")
    else:
        lines.append("next: check --plan the draft, read reports/scripts.md, fix any join that does not read, then render")
    return lines


def explore(job, found, unusable, sources, registered, games, min_sources, mix, max_clips, seconds, count,
            min_cast=1) -> list[str]:
    links = lambda a, b: (relations(a, b) or [None])[0]
    verify = verifier(sources, registered, games, min_sources, min_cast=min_cast)
    stranded = frozenset() if mix or min_cast > 1 else stranded_casts(found, links, min_sources=min_sources,
                                                                       max_clips=max_clips, verify=verify)
    verified, truncated = search(found, links, min_sources=min_sources, mix=mix, max_clips=max_clips, seconds=seconds,
                                 verify=verify, stranded=stranded, min_cast=min_cast)
    joins = Counter()
    openings, closings = Counter(), Counter()
    for row in verified:
        route = row["route"]
        joins.update((a["key"], b["key"]) for a, b in zip(route, route[1:]))
        openings[route[0]["key"]] += 1
        closings[route[-1]["key"]] += 1
    by_key = {clip["key"]: clip for clip in found}
    names = {pid: pid.split(":", 1)[1] for pid in registered}
    lengths = Counter(len(row["route"]) for row in verified)
    lines = ["# Routes", "",
             f"{len(found)} cuttable clips, {len(verified)} sampled routes pass every machine check"
             + (" (sampling stopped at the time limit; raise --seconds for more)" if truncated else "")
             + f", {len(joins)} distinct joins. Batch: {count} videos.",
             f"Clips per sampled route: {', '.join(f'{n} x{lengths[n]}' for n in sorted(lengths)) or 'none'}; there is no "
             "clip limit, only the 17-30 s window.",
             "A route is clips in role order, neighbours from different sources (a source may return later: a b a c)."
             + (f" Every route features {min_cast} celebrities (the user's decision)." if min_cast > 1 else ""),
             "Judge each join below as a viewer: does the second clip answer, continue, prove or pay off the first?",
             "Accept the good ones in a joins file, each with a reason quoting the linking words of both sides: clip to",
             "clip, or topic to topic when every clip of both topics reads (`topic:<slug>`; reject a pair that does not).",
             "Then run `routes --joins <file>` to build the batch. This file is long: read every part of it."]
    if stranded:
        lines.append(f"Own clips finish no video for {', '.join(names[p] for cast in sorted(stranded) for p in cast)}: "
                     "their clips hand over to or from another celebrity.")
    lines += ["", "## Clips", ""]
    for clip in sorted(found, key=lambda c: (c["cast"], c["key"])):
        topics = ("/".join(topic or "-" for topic in clip["topics"]) + "".join(f" +{p}" for p in clip["points"] if p not in clip["topics"])
                  + ("" if clip["opens"] else " (never first)") + ("" if clip["closes"] else " (never last)"))
        lines.append(f"{clip['key']}  {'/'.join(clip['roles'])}  #{topics}  {clip['min_frames'] / 60:.1f}-"
                     f"{clip['max_frames'] / 60:.1f} s  shots {clip['pieces'][0]}-{clip['pieces'][1]} f  cast "
                     f"{','.join(names[p] for p in clip['cast'])}  opens {openings[clip['key']]} / closes "
                     f"{closings[clip['key']]} routes")
        lines.append(f"   {clip['text']}")
        lines += [f"   ({gist})" for gist in clip["gists"]]
    if unusable:
        lines += ["", "## Beats that cannot be cut alone", ""] + unusable
    members, opening, ending = defaultdict(set), defaultdict(list), defaultdict(list)
    for clip in found:
        for topic in clip["topics"]:
            if topic:
                members[topic].add(clip["key"])
        if clip["topic_in"]:
            opening[clip["topic_in"]].append(clip["key"])
        if clip["topic_out"]:
            ending[clip["topic_out"]].append(clip["key"])
    if members:
        lines += ["", "## Topics", "", "Beats that make the same point: each topic is one slot any of its clips can fill (more",
                  "speakers per topic, more variety at each position). A topic join reads `from` as the clips ending on it",
                  "and `to` as the clips opening with it.", ""]
        for topic in sorted(members):
            casts = Counter(names[p] for key in members[topic] for p in by_key[key]["cast"])
            lines.append(f"topic:{topic}  ({', '.join(f'{n} {casts[n]}' for n in sorted(casts))})  opens: "
                         f"{', '.join(sorted(opening[topic])) or '-'}  ends: {', '.join(sorted(ending[topic])) or '-'}")
    lines += ["", "## Joins", "", "Grouped by what a clip ends on (its topic, else the clip), then what can follow it (a topic",
              "with the clips that open with it, or a clip with its first words); pairs and routes using them; in brackets",
              "the only relations the checks allow when they are limited.", ""]
    grouped = defaultdict(lambda: defaultdict(Counter))
    for (left, right), uses in joins.items():
        grouped[ends_on(by_key[left])][opens_with(by_key[right])][(left, right)] = uses
    for group in sorted(grouped):
        ends = sorted({left for followers in grouped[group].values() for left, _ in followers})
        if group.startswith(TOPIC):
            lines.append(f"{group}  " + " · ".join(f"{key} ...{by_key[key]['last_text']}" for key in ends))
        else:
            lines.append(f"{group}  ...{by_key[group]['last_text']}")
        for target in sorted(grouped[group]):
            pairs = grouped[group][target]
            common = set.intersection(*(set(relations(by_key[a], by_key[b])) for a, b in pairs))
            allowed = [name for name in PREFERRED if name in common] + sorted(common - set(PREFERRED))
            constraint = ("" if len(common) == len(rules.RELATIONS) else f"  [{', '.join(allowed)}]" if allowed
                          else "  [varies by pair]")
            uses = f"{sum(pairs.values())} routes" + (f", {len(pairs)} pairs" if len(pairs) > 1 else "")
            if target.startswith(TOPIC):
                words = " · ".join(f"{key} {by_key[key]['first_text']}..." for key in sorted({right for _, right in pairs}))
                lines.append(f"   -> {target}  {words}  ({uses}){constraint}")
            else:
                lines.append(f"   -> {target}  {by_key[target]['first_text']}...  ({uses}){constraint}")
    (job / "reports").mkdir(parents=True, exist_ok=True)
    (job / "reports/routes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write(job / "reports/routes.json", {"clips": found, "joins": [{"from": a, "to": b, "routes": n} for (a, b), n in sorted(joins.items())]})
    example = ('{"joins": [{"from": "topic:chop-trees-first", "to": "topic:furnace-heat", "relation": "progression", '
               '"reason": "砍树 first, then the wood feeds the 熔炉"}, {"from": "S01:U02-U05", "to": "S03:U07-U09", '
               '"relation": "explanation", "reason": "S01 asks 最要紧的是什么; S03 answers 提升温度"}]}')
    result = [f"routes: {job / 'reports/routes.md'} ({len(lines)} lines: {len(found)} clips, {len(verified)} sampled "
              f"routes, {len(joins)} joins, {len(openings)} openings, {len(members)} topics)"
              + ("; time limit reached" if truncated else "")]
    if unusable:
        result.append(f"{len(unusable)} beats cannot be cut alone (reasons listed in routes.md)")
    if len(openings) * batch.LIMITS["opening"] < count:
        result.append(f"warning: {len(openings)} openings serve at most {len(openings) * batch.LIMITS['opening']} videos; "
                      "cut more hook/body beats" + ("" if mix else " or add --mix"))
    result.append(f"next: read routes.md, write the joins you accept to a file ({example}), then routes --joins <file>")
    return result
