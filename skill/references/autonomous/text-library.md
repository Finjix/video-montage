# Fixed text library

`assets/text-library.json` holds canonical spellings of game names, people and common terms (initially
无尽冬日, 吴樾, 林子聪, 许君聪). It fixes how a word is written; whether it was spoken is still decided by
the agent from the actual audio, full context and verified asset copy. Never replace by sound-alike,
similarity or expected ad copy, and never add a sentence the source does not contain.

## Format and snapshot

```json
{"schema": "video-montage-text-library/v1",
 "entries": [{"id": "person:wu-yue", "category": "person", "text": "吴樾"}]}
```

`category` is `game`, `person` or `term`. IDs (lowercase letters, digits, `:` and `-`) and texts are
unique; texts are non-empty single lines. Keep existing IDs and add new IDs for new entries; editing
the JSON is enough. Changes affect only future jobs.

`init` copies the library to `<job_dir>/config/text_library.json` and binds
`text_library: {"path", "sha256"}` in the work order and state; the source index, every source row,
speech evidence, reviewed speech and the plan root (copy the same reference) inherit it, and candidate
and clean evidence stay bound to it. Never edit or remove the snapshot; completion cleanup keeps it.

## In speech reviews

Every source, candidate and clean speech review sets `text_library_sha256` to the snapshot hash. A
correction that touches a library term cites each affected term and the actual audio range:

```json
[{"kind": "text_library", "sha256": "<snapshot sha256>", "term_id": "game:wu-jin-dong-ri", "text": "无尽冬日"},
 {"kind": "source_audio", "sha256": "<this WAV sha256>", "start_ms": 500, "end_ms": 1800}]
```

The `source_audio` range equals the correction's `start_ms`/`end_ms`. The reason explains why the
audio and context point to that game or person (cite source, reviewed speech or nameplate evidence when
useful). The library reference alone never proves speech.

## In subtitles

Subtitles come from reviewed speech and word times. Library terms are protected whole words: never
split across cues, never reverted to a misspelling (for example 吴樾, not 吴岳). Imported and reviewed
subtitles are checked for term count, spelling and word boundaries. Person names use ordinary subtitle
styling; the library assigns no flower text, identity or nameplate.
