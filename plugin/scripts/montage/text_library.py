"""Fixed spellings of game names, people and terms (assets/text-library.json)."""
from __future__ import annotations

import re

from montage.common import LIBRARY, read

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


DIGITS = {"0": "0零〇", "1": "1一幺", "2": "2二两", "3": "3三", "4": "4四", "5": "5五", "6": "6六", "7": "7七", "8": "8八",
          "9": "9九"}


def load(job) -> dict:
    """The job's copy plus every entry added to the plugin library since the job was created."""
    own = read(job / "config/text_library.json")
    ids, texts = {e["id"] for e in own["entries"]}, {e["text"] for e in own["entries"]}
    extra = [e for e in read(LIBRARY)["entries"] if e["id"] not in ids and e["text"] not in texts]
    return {**own, "entries": own["entries"] + extra}


def spellings(library: dict) -> list[tuple[re.Pattern, str]]:
    """Entries with digits, matched however the recognizer wrote the digits (金子六六六 -> 金子666)."""
    found = []
    for entry in library["entries"]:
        text = entry["text"]
        if any(ch in DIGITS for ch in text):
            found.append((re.compile("".join(f"[{DIGITS[ch]}]" if ch in DIGITS else re.escape(ch) for ch in text)), text))
    return found


def respell(words: list[dict], patterns: list[tuple[re.Pattern, str]]) -> list[dict]:
    """Library spelling for the matched words, merged into one word (first start, last end, outer characters kept)."""
    for pattern, text in patterns:
        joined = "".join(word["word"] for word in words)
        owner = [index for index, word in enumerate(words) for _ in word["word"]]
        for match in reversed(list(pattern.finditer(joined))):
            first, last = owner[match.start()], owner[match.end() - 1]
            head = owner.index(first)
            tail = len(owner) - owner[::-1].index(last)
            merged = joined[head:match.start()] + text + joined[match.end():tail]
            words = words[:first] + [{**words[first], "word": merged, "end": words[last]["end"]}] + words[last + 1:]
    return words
