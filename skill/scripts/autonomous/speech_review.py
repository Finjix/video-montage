"""Hash-bound agent speech judgments, separate from immutable recognizer output.

These checks validate the scope and provenance of a judgment, not whether a
model or a person heard the words. ASR remains an observation, never an approval.
"""
from __future__ import annotations

import difflib
import math
import re

POLICY = "agent-speech-review/v1"
SCHEMA = "video-montage-agent-speech-review/v1"


def changes(before, after, normalize):
    left, right = normalize(before), normalize(after)
    return [{"before_start": i, "before_end": j, "after_start": k, "after_end": l,
             "before": left[i:j], "after": right[k:l]}
            for op, i, j, k, l in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
            if op != "equal"]


def checked_asr(a, value, duration, *, allow_empty=False):
    """Require explicit, ordered word times for every reviewed character."""
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        raise ValueError("reviewed speech text required")
    segments = value.get("segments")
    if not isinstance(segments, list) or (not a.normalized(value["text"]) and not allow_empty):
        raise ValueError("complete reviewed speech segments required")
    cursor = 0.0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise ValueError("reviewed speech segment invalid")
        words = segment.get("words")
        if not isinstance(words, list) or not words:
            raise ValueError("reviewed word times required; recheck missing ASR words")
        start, end = segment.get("start"), segment.get("end")
        if not valid_time(start, end, duration) or start < cursor:
            raise ValueError("reviewed segment time outside audio or out of order")
        word_cursor = start
        for word in words:
            if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                    or not a.normalized(word["word"])
                    or not valid_time(word.get("start"), word.get("end"), duration)
                    or word["start"] < word_cursor or word["start"] < start or word["end"] > end):
                raise ValueError("reviewed word time outside its segment or out of order")
            word_cursor = word["end"]
        if a.normalized("".join(w["word"] for w in words)) != a.normalized(segment["text"]):
            raise ValueError("reviewed words do not cover segment text")
        cursor = end
    if a.normalized("".join(s["text"] for s in segments)) != a.normalized(value["text"]):
        raise ValueError("reviewed segments do not cover complete speech")
    return value


def valid_time(start, end, duration):
    return (type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + .001)


def context(a, index, asset_ref, library_ref=None):
    """Actual source observations/reviews and copy, plus optional fixed spellings."""
    texts = {}
    for row in index["sources"]:
        raw = a.read(a.require_ref(row["asr"], "speech context ASR"))
        texts[("source_asr", row["asr"]["sha256"])] = a.normalized(raw.get("asr", raw)["text"])
        if row.get("reviewed_speech"):
            reviewed = source_asr(a, row)
            texts[("source_speech", row["reviewed_speech"]["sha256"])] = a.normalized(reviewed["text"])
    copy = a.read(a.require_ref(asset_ref, "speech context asset copy"))
    for row in copy.get("assets", []):
        a.require_ref(row, "speech context copy asset")
        texts[("asset_copy", row["sha256"])] = a.normalized(row.get("text", ""))
    if library_ref is not None:
        library = a.text_library.load(library_ref)
        texts[("text_library", library_ref["sha256"])] = {row["id"]: row["text"] for row in library["entries"]}
    return texts


def validate(a, finding, observation, audio_ref, duration, texts, *, expected=None, allow_empty=False):
    if (not isinstance(finding, dict) or finding.get("speech_pass") is not True
            or finding.get("audio_sha256") != audio_ref["sha256"]
            or finding.get("observed_text") != observation.get("text")
            or not isinstance(finding.get("reason"), str) or not finding["reason"].strip()
            or not isinstance(finding.get("timing_reason"), str) or not finding["timing_reason"].strip()):
        raise ValueError("explicit audio-bound speech and timing review required")
    a.require_ref(audio_ref, "reviewed speech audio")
    reviewed = checked_asr(a, finding.get("reviewed_asr"), duration, allow_empty=allow_empty)
    if expected is not None and a.normalized(reviewed["text"]) != a.normalized(expected):
        raise ValueError("reviewed speech differs from source-bound plan; repair the plan")
    required = changes(observation["text"], reviewed["text"], a.normalized)
    libraries = {digest: entries for (kind, digest), entries in texts.items() if kind == "text_library"}
    supplied = finding.get("corrections", [])
    if not isinstance(supplied, list) or len(supplied) != len(required):
        raise ValueError("every ASR change needs an individual correction record")
    times = a.character_times(reviewed)
    for change, actual in zip(required, supplied):
        if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in change.items()):
            raise ValueError("ASR correction span or before/after text changed")
        start, end = actual.get("start_ms"), actual.get("end_ms")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= round(duration * 1000)
                or not isinstance(actual.get("reason"), str) or not actual["reason"].strip()):
            raise ValueError("ASR correction needs audio interval and concrete reason")
        if change["after"]:
            first, last = times[change["after_start"]], times[change["after_end"] - 1]
            if start > first[0] + 250 or end < last[1] - 250:
                raise ValueError("ASR correction interval does not cover its reviewed words")
        evidence = actual.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("ASR correction needs hash-bound source/context/copy evidence")
        affected, cited = set(), set()
        reviewed_text = a.normalized(reviewed["text"])
        for digest, entries in libraries.items():
            for identity, canonical in entries.items():
                for match in re.finditer(re.escape(a.normalized(canonical)), reviewed_text):
                    left, right = change["after_start"], change["after_end"]
                    if (left < match.end() and right > match.start()) or (left == right and match.start() < left < match.end()):
                        affected.add((digest, identity))
        for item in evidence:
            if isinstance(item, dict) and item.get("kind") == "text_library":
                digest, identity = item.get("sha256"), item.get("term_id")
                if (not isinstance(digest, str) or not isinstance(identity, str)
                        or (digest, identity) not in affected
                        or item.get("text") != libraries.get(digest, {}).get(identity)):
                    raise ValueError("text library correction term/hash/text or reviewed span invalid")
                cited.add((digest, identity))
                continue
            if isinstance(item, dict) and item.get("kind") == "source_audio":
                if (item.get("sha256") != audio_ref["sha256"] or item.get("start_ms") != start or item.get("end_ms") != end):
                    raise ValueError("correction source-audio hash/range changed")
                continue  # A bound agent judgment, not a machine-verified quotation.
            quote = a.normalized(item.get("text", "")) if isinstance(item, dict) else ""
            if not quote or quote not in texts.get((item.get("kind"), item.get("sha256")), ""):
                raise ValueError("ASR correction evidence changed, missing or invented")
        if affected - cited:
            raise ValueError("fixed spelling correction needs its text library term reference")
        if cited and not any(isinstance(item, dict) and item.get("kind") == "source_audio" for item in evidence):
            raise ValueError("text library spelling alone cannot prove speech; bound source-audio evidence required")
    return reviewed


def source_asr(a, row):
    raw = a.read(a.require_ref(row["asr"], "original source ASR"))
    if not row.get("reviewed_speech"):
        return raw.get("asr", raw)
    artifact = a.read(a.require_ref(row["reviewed_speech"], "reviewed source speech"))
    evidence = a.read(a.require_ref(artifact["evidence"], "source speech evidence"))
    review = a.read(a.require_ref(artifact["review"], "source speech review"))
    if (artifact.get("policy") != POLICY or artifact.get("source") != row["source"]
            or artifact.get("raw_asr") != row["asr"] or artifact.get("video") != row["video"]):
        raise ValueError("reviewed source speech provenance changed")
    library_ref = artifact.get("text_library")
    a.text_library.require_binding(library_ref, artifact, evidence, row)
    header(review, "source", artifact["evidence"], artifact["asset_copy"], library_ref)
    if evidence.get("policy") != POLICY or evidence.get("asset_copy") != artifact["asset_copy"]:
        raise ValueError("source speech evidence policy/copy changed")
    observations = {r["source"]["sha256"]: r for r in evidence["sources"]}
    findings = {r["source_sha256"]: r for r in review["results"]}
    if (len(observations) != len(evidence["sources"]) or len(findings) != len(review["results"])
            or set(observations) != set(findings)):
        raise ValueError("source speech review full scope changed")
    obs = observations[row["source"]["sha256"]]
    if obs["source"] != row["source"] or obs["asr"] != row["asr"] or obs["video"] != row["video"]:
        raise ValueError("source speech evidence describes another source/range")
    # Do not recurse through source_speech evidence when auditing the source review itself.
    index = {"sources": [{"source": r["source"], "asr": r["asr"]} for r in evidence["sources"]]}
    result = validate(a, findings[row["source"]["sha256"]], raw.get("asr", raw), obs["audio"],
                      row["video"]["frames"] * row["video"]["fps_den"] / row["video"]["fps_num"],
                      context(a, index, artifact["asset_copy"], library_ref), allow_empty=True)
    if result != artifact.get("asr"):
        raise ValueError("compiled source speech differs from its reviewed text/times")
    return result


def header(review, stage, evidence_ref, asset_ref, library_ref=None):
    if (review.get("schema") != SCHEMA or review.get("reviewer_role") != "agent"
            or review.get("stage") != stage or review.get("evidence_sha256") != evidence_ref["sha256"]
            or review.get("asset_copy_sha256") != asset_ref["sha256"]):
        raise ValueError("speech review stage/evidence/copy binding invalid")
    library_header(review, library_ref)


def library_header(review, library_ref):
    if review.get("text_library_sha256") != (library_ref["sha256"] if library_ref is not None else None):
        raise ValueError("speech review text library hash binding invalid")


def range_text(a, asr, segment):
    start = segment["source_in_frame"] * segment["source_fps_den"] / segment["source_fps_num"]
    end = segment["source_out_frame_exclusive"] * segment["source_fps_den"] / segment["source_fps_num"]
    words = [w for s in asr["segments"] for w in s["words"] if w["end"] > start and w["start"] < end]
    if any(w["start"] < start - .001 or w["end"] > end + .001 for w in words):
        raise ValueError("source cut truncates reviewed word; adjust native-frame bounds")
    return a.normalized("".join(w["word"] for w in words))


def retain_refs(a, value, root, keep, seen=None):
    """Retain the speech dependency graph for compact-delivery reburn audits."""
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        if value.get("path") and value.get("sha256"):
            path = a.require_ref(value, "retained speech dependency").resolve()
            if path.is_relative_to(root) and path not in seen:
                seen.add(path)
                keep.add(path)
                if path.suffix.lower() == ".json":
                    retain_refs(a, a.read(path), root, keep, seen)
        for item in value.values():
            retain_refs(a, item, root, keep, seen)
    elif isinstance(value, list):
        for item in value:
            retain_refs(a, item, root, keep, seen)
