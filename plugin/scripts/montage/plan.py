"""Resolve the agent's plan against the catalog: exact cut frames, shots, cast, nameplates and every rule.

A plan names clips as unit ranges; the solver chooses the in/out frames inside each unit's clean cut
window so that the edit-shape rules hold, preferring tight natural pauses.
"""
from __future__ import annotations

import math

from montage import batch, timeline as CATALOG, rules
from montage.common import read

MIN_FRAMES, MAX_FRAMES = 1020, 1800      # 17-30 s at 60 fps, clean and final (no speed change)
MIN_SHOTS, MIN_SHOT, MAX_SPREAD = 4, 60, 300   # shots >= 1 s, longest minus shortest <= 300 frames (5 s)
NAMEPLATE_FRAMES, MIN_NAMEPLATE, NAMEPLATE_GAP = 192, 48, 18
IN_PAD, OUT_PAD = 0.10, 0.12             # preferred silence beyond the muted edge
SAMPLES_PER_EDGE = 14
MIN_SOURCES = 3


class PlanError(ValueError):
    pass


def output_frames(frames: int, source: dict) -> int:
    """Native frames -> 60 fps output frames (same rounding as the renderer)."""
    return round(frames * source["fps_den"] * 60 / source["fps_num"])


def unit_slice(source: dict, spec: str) -> list[dict]:
    units = source["units"]
    ids = [unit["id"] for unit in units]
    first, _, last = str(spec).partition("-")
    last = last or first
    if first not in ids or last not in ids:
        missing = [value for value in (first, last) if value not in ids]
        raise PlanError(f"{source['id']} has no unit {', '.join(missing)}")
    a, b = ids.index(first), ids.index(last)
    if b < a:
        raise PlanError(f"{source['id']} {spec}: units out of order")
    return units[a:b + 1]


def sample(frames: list[int], preferred: int, limit: int = SAMPLES_PER_EDGE) -> list[int]:
    if len(frames) <= limit:
        return frames
    picks = {min(frames, key=lambda f: abs(f - preferred))}
    step = (len(frames) - 1) / (limit - 1)
    picks.update(frames[round(i * step)] for i in range(limit))
    return sorted(picks)


def clip_options(source: dict, units: list[dict]) -> list[dict]:
    fps = source["fps_num"] / source["fps_den"]
    head, tail = units[0]["protect"][0], units[-1]["protect"][1]
    ins, outs = CATALOG.expand(units[0]["in_frames"]), CATALOG.expand(units[-1]["out_frames"])
    prefer_in = math.floor((head - CATALOG.SILENT_EDGE - IN_PAD) * fps)
    prefer_out = math.ceil((tail + CATALOG.SILENT_EDGE + OUT_PAD) * fps)
    options = []
    for first in sample(ins, prefer_in):
        for last in sample(outs, prefer_out):
            if last <= first:
                continue
            inner = [cut for cut in source["cuts"] if first < cut < last]
            edges = [first, *inner, last]
            total = output_frames(last - first, source)
            marks = [round((edge - first) * source["fps_den"] * 60 / source["fps_num"]) for edge in edges[:-1]] + [total]
            pieces = [b - a for a, b in zip(marks, marks[1:])]
            cost = abs(first - prefer_in) / fps + abs(last - prefer_out) / fps
            options.append({"in": first, "out": last, "total": total, "pieces": pieces, "edges": edges, "cost": cost})
    return options


def violation(total: int, shots: int) -> int:
    return max(0, MIN_FRAMES - total) + max(0, total - MAX_FRAMES) + 1000 * max(0, MIN_SHOTS - shots)


def repair(feasible: list[list[dict]]) -> list[dict] | None:
    """Cheapest option per clip, then single-clip swaps until duration and shot count hold."""
    choice = [min(row, key=lambda o: o["cost"]) for row in feasible]
    total = sum(o["total"] for o in choice)
    shots = sum(len(o["pieces"]) for o in choice)
    for _ in range(60):
        current = violation(total, shots)
        if not current:
            return choice
        best = None
        for i, row in enumerate(feasible):
            for o in row:
                score = violation(total - choice[i]["total"] + o["total"], shots - len(choice[i]["pieces"]) + len(o["pieces"]))
                key = (score, o["cost"] - choice[i]["cost"])
                if score < current and (best is None or key < best[0]):
                    best = (key, i, o)
        if best is None:
            return None
        _, i, o = best
        total += o["total"] - choice[i]["total"]
        shots += len(o["pieces"]) - len(choice[i]["pieces"])
        choice[i] = o
    return None


def solve(options: list[list[dict]]) -> list[dict] | None:
    """One option per clip: every shot in [L, L+MAX_SPREAD] with L >= MIN_SHOT, 17-30 s, >= 4 shots, least padding."""
    usable = [[o for o in row if min(o["pieces"]) >= MIN_SHOT] for row in options]
    if any(not row for row in usable):
        return None
    cheapest = repair(usable)
    if cheapest and max(max(o["pieces"]) for o in cheapest) - min(min(o["pieces"]) for o in cheapest) <= MAX_SPREAD:
        return cheapest
    lows = sorted({min(o["pieces"]) // 6 * 6 for row in usable for o in row} | {MIN_SHOT})
    best = None
    for low in lows:
        low = max(low, MIN_SHOT)
        feasible = [[o for o in row if min(o["pieces"]) >= low and max(o["pieces"]) <= low + MAX_SPREAD] for row in usable]
        if any(not row for row in feasible):
            continue
        choice = repair(feasible)
        if choice is not None:
            cost = sum(o["cost"] for o in choice)
            if best is None or cost < best[0]:
                best = (cost, choice)
    return best[1] if best else None


def explain(source_rows: list[tuple[dict, list[dict]]], options: list[list[dict]]) -> list[str]:
    """Why no cut choice satisfies the edit shape."""
    reasons = []
    for index, ((source, units), row) in enumerate(zip(source_rows, options), 1):
        short_inner = sorted({p for o in row for p in o["pieces"][1:-1] if p < MIN_SHOT})
        if short_inner:
            reasons.append(f"clip {index} ({source['id']} {units[0]['id']}-{units[-1]['id']}) contains a camera shot of "
                           f"{short_inner[0]} frames (< {MIN_SHOT}); start/end the clip on another unit or pick other footage")
    for index, ((source, units), row) in enumerate(zip(source_rows, options), 1):
        edge = max(min(o["pieces"][0], o["pieces"][-1]) for o in row)
        if edge < MIN_SHOT:
            reasons.append(f"clip {index} ({source['id']} {units[0]['id']}-{units[-1]['id']}) always starts or ends within {edge} "
                           f"frames of a camera cut (< {MIN_SHOT}); cut the beat at another unit or pick other footage")
    totals = [(min(o["total"] for o in row), max(o["total"] for o in row)) for row in options]
    low, high = sum(t[0] for t in totals), sum(t[1] for t in totals)
    if high < MIN_FRAMES:
        reasons.append(f"at most {high} frames ({high / 60:.1f} s); needs >= {MIN_FRAMES} (17 s): add complete speech")
    if low > MAX_FRAMES:
        reasons.append(f"at least {low} frames ({low / 60:.1f} s); needs <= {MAX_FRAMES} (30 s): drop a clip")
    shots = sum(max(len(o["pieces"]) for o in row) for row in options)
    if shots < MIN_SHOTS:
        reasons.append(f"only {shots} camera shots; needs >= {MIN_SHOTS}: add clips from other cameras/sources")
    if not reasons:
        spans = [f"clip {i}: {min(min(o['pieces']) for o in row)}-{max(max(o['pieces']) for o in row)}"
                 for i, row in enumerate(options, 1)]
        reasons.append(f"shot lengths cannot all fit within one {MIN_SHOT}..(+{MAX_SPREAD}) frame band ({MAX_SPREAD // 60} s spread); "
                       f"possible shot lengths per clip: {'; '.join(spans)}. Split long shots with more clips or shorten them")
    return reasons


def nameplates(shots: list[dict], registered: set[str]) -> tuple[list[dict], list[str]]:
    """First appearance of each celebrity: a solo shot, 192 frames, >= 18 frames apart, >= 48 frames shown."""
    windows, errors, seen = [], [], set()
    for index, shot in enumerate(shots):
        present = [p for p in shot["people"] if p in registered]
        new = [p for p in present if p not in seen]
        if len(new) > 1:
            errors.append(f"shot {index + 1}: {', '.join(new)} first appear together; introduce each in a solo shot")
        elif new:
            person = new[0]
            if present != [person]:
                errors.append(f"shot {index + 1}: {person} first appears beside another celebrity; needs a solo first shot")
            limit = shot["out_out"]
            for following in shots[index + 1:]:
                if [p for p in following["people"] if p in registered] != [person] or following["out_in"] != limit:
                    break
                limit = following["out_out"]
            windows.append({"person_id": person, "start_frame": shot["out_in"],
                            "end_frame_exclusive": min(shot["out_in"] + NAMEPLATE_FRAMES, limit)})
        seen.update(present)
    for current, following in zip(windows, windows[1:]):
        current["end_frame_exclusive"] = min(current["end_frame_exclusive"], following["start_frame"] - NAMEPLATE_GAP)
    for window in windows:
        if window["end_frame_exclusive"] - window["start_frame"] < MIN_NAMEPLATE:
            errors.append(f"{window['person_id']} nameplate would show {window['end_frame_exclusive'] - window['start_frame']} "
                          f"frames (< {MIN_NAMEPLATE}); give the first appearance a longer solo shot")
    return windows, errors


def clip_beats(source: dict, units: list[dict]) -> tuple[list[dict], list[str]]:
    """The beats a clip covers; a clip is whole beats of one source, nothing outside a beat and no half beat."""
    beats = source.get("beats") or []
    if not beats:
        return [], [f"{source['id']} has no beats; annotate them first (beats are the only material a plan may use)"]
    ids = {unit["id"] for unit in units}
    covered, problems = [], []
    for beat in beats:
        members = [unit_id for unit_id in beat["units"] if unit_id in ids]
        if not members:
            continue
        if len(members) != len(beat["units"]):
            problems.append(f"{source['id']} cuts through beat {beat['id']} ({beat['units'][0]}-{beat['units'][-1]}); take the "
                            "whole beat, or split the beat in the annotations")
        covered.append(beat)
    loose = [unit["id"] for unit in units if not any(unit["id"] in beat["units"] for beat in covered)]
    if loose:
        problems.append(f"{source['id']} {', '.join(loose)} belong to no beat (cannot be planned)")
    return [{key: beat[key] for key in ("id", "kind", "role", "gist")} for beat in covered], problems


def resolve_output(output: dict, sources: dict, registered: set[str], games: list[str], min_sources: int,
                   cache: dict | None = None) -> tuple[dict, list[str]]:
    """`cache` (optional) keeps the in/out options of each clip across many calls on one catalog."""
    if not isinstance(output, dict):
        raise PlanError("each output is an object with plan_id, clips and transitions")
    pid = output.get("plan_id")
    clips_spec = output.get("clips")
    if not isinstance(clips_spec, list) or not clips_spec:
        raise PlanError("clips must be a non-empty list")
    rows, errors, beat_rows = [], [], []
    for number, clip in enumerate(clips_spec, 1):
        if not isinstance(clip, dict) or clip.get("source") not in sources:
            raise PlanError(f"clip {number}: unknown source {clip.get('source') if isinstance(clip, dict) else clip!r}")
        source = sources[clip["source"]]
        units = unit_slice(source, clip.get("units", ""))
        covered, problems = clip_beats(source, units)
        errors += [f"clip {number}: {problem}" for problem in problems]
        beat_rows.append(covered)
        for unit in units:
            if unit["excluded"]:
                errors.append(f"clip {number}: {source['id']} {unit['id']} is excluded in the annotations")
        if not units[0]["in_frames"]:
            errors.append(f"clip {number}: {source['id']} {units[0]['id']} cannot start a clip (no clean silence or stable picture before it)")
        if not units[-1]["out_frames"]:
            errors.append(f"clip {number}: {source['id']} {units[-1]['id']} cannot end a clip (no clean silence or stable picture after it)")
        rows.append((source, units))
    if not errors:
        errors += rules.structure_errors([{"beats": covered, "kind": "scene" if any(b["kind"] == "scene" for b in covered) else "talk"}
                                          for covered in beat_rows])
    if errors:
        return {}, errors
    options = []
    for source, units in rows:
        key = (source["id"], units[0]["id"], units[-1]["id"])
        if cache is None or key not in cache:
            row = clip_options(source, units)
            if cache is None:
                options.append(row)
                continue
            cache[key] = row
        options.append(cache[key])
    empty = [f"clip {number}: {source['id']} {units[0]['id']}-{units[-1]['id']} has no valid in/out frame pair"
             for number, ((source, units), row) in enumerate(zip(rows, options), 1) if not row]
    if empty:
        return {}, empty
    choice = solve(options)
    if choice is None:
        return {}, explain(rows, options)
    clips, shots, cursor = [], [], 0
    for number, ((source, units), option, covered) in enumerate(zip(rows, choice, beat_rows), 1):
        start, end = option["in"], option["out"]
        hits = CATALOG.rejected_hits(source, start, end)
        if hits:
            errors.append(f"clip {number}: overlaps user-rejected interval {', '.join(hits)}")
        clip = {"source": source["id"], "path": source["path"], "sha256": source["sha256"],
                "fps_num": source["fps_num"], "fps_den": source["fps_den"],
                "beats": covered, "kind": "scene" if any(beat["kind"] == "scene" for beat in covered) else "talk",
                "units": [unit["id"] for unit in units], "unit_texts": [unit["text"] for unit in units],
                "text": "".join(unit["text"] for unit in units), "in": start, "out": end,
                "protect": [units[0]["protect"][0], units[-1]["protect"][1]],
                "words": [word for unit in units for word in unit["words"]],
                "first_speaker": units[0].get("speaker"), "last_speaker": units[-1].get("speaker"),
                "out_in": cursor, "out_out": cursor + option["total"], "frames": option["total"],
                "duration": option["total"] / 60}
        clips.append(clip)
        offset = cursor
        for (left, right), length in zip(zip(option["edges"], option["edges"][1:]), option["pieces"]):
            shot = next(s for s in source["shots"] if s["start"] <= left < s["end"])
            shots.append({"clip": number, "source": source["id"], "start": left, "end": right,
                          "out_in": offset, "out_out": offset + length, "frames": length,
                          "people": shot["people"], "family": shot["family"], "catalog_shot": shot["number"]})
            offset += length
        cursor += option["total"]
    for left, right in zip(shots, shots[1:]):
        if (left["source"], left["catalog_shot"]) == (right["source"], right["catalog_shot"]):
            errors.append(f"clips {left['clip']} and {right['clip']} cut inside one camera shot ({left['source']} shot "
                          f"{left['catalog_shot']}): a jump cut is not a new shot; put another camera between them or join them")
    for index, shot in enumerate(shots, 1):
        if not shot["people"]:
            errors.append(f"shot {index} ({shot['source']} catalog shot {shot['catalog_shot']}): people not annotated")
        elif not set(shot["people"]) & registered:
            errors.append(f"shot {index} ({shot['source']} catalog shot {shot['catalog_shot']}): no registered celebrity visible")
    windows, cast_errors = nameplates(shots, registered)
    errors += cast_errors
    used = len({clip["sha256"] for clip in clips})
    if used < min_sources:
        errors.append(f"uses {used} source files; needs >= {min_sources}: take a clip from another source")
    transitions = output.get("transitions") or []
    if not isinstance(transitions, list) or not all(isinstance(row, dict) for row in transitions):
        errors.append("transitions is a list of {relation, reason} objects")
        transitions = []
    errors += rules.structure_errors(clips)
    errors += rules.output_errors(clips, transitions, games)
    resolved = {"plan_id": pid, "clips": clips, "shots": shots, "frames": cursor, "nameplates": windows,
                "protagonists": sorted({p for shot in shots for p in shot["people"] if p in registered}),
                "transitions": transitions, "note": output.get("note", "")}
    return resolved, errors


def resolve(plan: dict, job, state: dict) -> tuple[list[dict], dict[str, list[str]], dict]:
    """Every output resolved, errors keyed by plan ID (plus "batch"), and the batch usage summary."""
    catalog = read(job / "catalog/catalog.json")
    sources = {source["id"]: source for source in catalog["sources"]}
    registered = {row["person_id"] for row in state["celebrities"]}
    library = read(job / "config/text_library.json")
    games = [entry["text"] for entry in library["entries"] if entry["category"] == "game"]
    decisions = state.get("decisions", {})
    min_sources = decisions.get("min_sources_per_output", {}).get("value", MIN_SOURCES)
    outputs, errors = [], {}
    rows = plan.get("outputs") if isinstance(plan, dict) else None
    if not isinstance(rows, list) or not rows:
        return [], {"plan": ["plan needs {\"outputs\": [...]}"]}, {}
    ids = [row.get("plan_id") if isinstance(row, dict) else None for row in rows]
    for pid in ids:
        if not isinstance(pid, str) or not pid or not pid.replace("_", "").replace("-", "").isalnum() or len(pid) > 32:
            errors.setdefault("plan", []).append(f"invalid plan_id {pid!r} (letters, digits, - and _)")
    if len({str(pid).casefold() for pid in ids}) != len(ids):
        errors.setdefault("plan", []).append("plan_id values must be unique")
    if len(rows) != state["order"]["requested_outputs"]:
        errors.setdefault("plan", []).append(f"plan has {len(rows)} outputs; the request is {state['order']['requested_outputs']} "
                                                  "(the count is the user's: plan every requested video, never edit requested_outputs)")
    for row in rows:
        pid = row.get("plan_id") if isinstance(row, dict) else None
        try:
            resolved, problems = resolve_output(row, sources, registered, games, min_sources)
        except PlanError as error:
            resolved, problems = {}, [str(error)]
        if problems:
            errors[str(pid)] = problems
        if resolved:
            outputs.append(resolved)
    summary = {}
    if len(outputs) == len(rows):
        batch_errors, summary = batch.audit(outputs, decisions)
        if batch_errors:
            errors["batch"] = batch_errors
    return outputs, errors, summary


def describe(output: dict) -> str:
    sources = len({clip["sha256"] for clip in output["clips"]})
    lengths = ",".join(str(shot["frames"]) for shot in output["shots"])
    clips = " + ".join(f"{clip['source']}:{clip['units'][0]}" + (f"-{clip['units'][-1]}" if len(clip["units"]) > 1 else "")
                       for clip in output["clips"])
    talk = sum(clip["frames"] for clip in output["clips"] if clip["kind"] == "talk") / output["frames"]
    return (f"{output['plan_id']}: {output['frames']} frames ({output['frames'] / 60:.1f} s), {len(output['shots'])} shots "
            f"[{lengths}], {sources} sources, talk {talk:.0%}, cast {','.join(p.split(':', 1)[1] for p in output['protagonists'])} :: {clips}")


def script(output: dict) -> list[str]:
    """The video as the viewer hears it: each clip's words with its beats, and what the next clip claims to follow from."""
    lines = [f"## {output['plan_id']} ({output['frames'] / 60:.1f} s)" + (f" - {output['note']}" if output.get("note") else "")]
    for number, clip in enumerate(output["clips"], 1):
        if number > 1:
            links = output["transitions"]
            link = links[number - 2] if number - 2 < len(links) else {}
            lines.append(f"   -> {link.get('relation', '(missing)')}: {link.get('reason', '(missing)')}")
        beats = ", ".join(f"{beat['id']} {beat['kind']}/{beat['role']}" for beat in clip["beats"])
        lines.append(f"{number}. {clip['source']} {clip['units'][0]}-{clip['units'][-1]} [{beats}] {clip['duration']:.1f} s")
        lines.append(f"   {clip['text']}")
        lines += [f"   ({beat['gist']})" for beat in clip["beats"]]
    return lines
