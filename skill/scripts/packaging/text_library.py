"""Fixed spellings, never recognizer hints or evidence that a word was spoken."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "video-montage-text-library/v1"
CATEGORIES = {"game", "person", "term"}


def checked(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("entries"), list):
        raise ValueError("text library schema and entries required")
    ids, texts = set(), set()
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("text library entry must be an object")
        identity, category, text = entry.get("id"), entry.get("category"), entry.get("text")
        if (not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9:-]*", identity)
                or identity in ids or not isinstance(category, str) or category not in CATEGORIES
                or not isinstance(text, str) or not text or text != text.strip()
                or any(c in text for c in "\r\n\u2028\u2029") or text in texts
                or not re.search(r"[0-9A-Za-z\u3400-\u9fff]", text)):
            raise ValueError("text library needs unique IDs/text, a supported category and single-line canonical text")
        ids.add(identity)
        texts.add(text)
    return value


def load(reference: dict) -> dict:
    if not isinstance(reference, dict) or not reference.get("path") or not reference.get("sha256"):
        raise ValueError("text library snapshot reference required")
    path = Path(reference["path"])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]:
        raise ValueError("text library snapshot changed or missing")
    return checked(json.loads(path.read_text(encoding="utf-8-sig")))


def require_binding(reference: dict | None, *documents: dict) -> None:
    if any(document.get("text_library") != reference for document in documents):
        raise ValueError("text library snapshot binding changed or missing")
    if reference is not None:
        load(reference)


def protected_terms(reference: dict | None) -> tuple[str, ...]:
    return tuple(entry["text"] for entry in load(reference)["entries"]) if reference is not None else ()


def require_complete_terms(cues: list[dict], terms: tuple[str, ...], normalize, *, expected: str | None = None) -> None:
    """Reject a canonical word divided between sequential subtitle cues."""
    parts = [normalize(cue["text"]) for cue in cues]
    text = "".join(parts)
    edges, cursor = set(), 0
    for part in parts[:-1]:
        cursor += len(part)
        edges.add(cursor)
    for term in terms:
        canonical = normalize(term)
        if expected is not None and text.count(canonical) != normalize(expected).count(canonical):
            raise ValueError(f"subtitle text library spelling differs from reviewed speech: {term}")
        for match in re.finditer(re.escape(canonical), text):
            if any(match.start() < edge < match.end() for edge in edges):
                raise ValueError(f"text library term split between subtitle cues: {term}")
