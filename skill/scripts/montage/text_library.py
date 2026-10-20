"""Fixed spellings of game names, people and terms (assets/text-library.json)."""
from __future__ import annotations

import re

CATEGORIES = {"game", "person", "term"}


def checked(value: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError("text library needs an entries list")
    ids, texts = set(), set()
    for entry in value["entries"]:
        identity, category, text = (entry.get(key) if isinstance(entry, dict) else None for key in ("id", "category", "text"))
        if (not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9:-]*", identity) or identity in ids
                or category not in CATEGORIES or not isinstance(text, str) or not text.strip() or text in texts
                or "\n" in text):
            raise ValueError(f"text library entry needs a unique id, a category in {sorted(CATEGORIES)} and unique one-line text: {entry}")
        ids.add(identity)
        texts.add(text)
    return value
