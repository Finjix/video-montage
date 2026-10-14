# Packaging workflow

Only when the user requests packaging. Input is a validated clean video (1440×2560, 60 fps, with
audio); the packaged copy goes to `成片/` and the clean video is kept. Every package, render and reburn
configuration needs a per-output Codex [design](design.md); there is no fixed-yellow, random-font or
random-flower fallback and `subtitle_srt` is not read.

Rendering composes subtitles, flowers, overlays and audio on the unaccelerated timeline, then applies the
single 1.2x (picture and mix together, pitch kept, 60 fps) and encodes. All outputs of the batch are
encoded (in parallel) before anything is published; a replacement error rolls back. Encoding: H.264
`yuv420p` CRF 18 by default; when `fire1` flower text is actually used, RGB composition and `yuv444p`
CRF 8 keep its thin red edge (larger files; some hardware players lack 4:4:4). The manifest records
`video_encoding`. Temporary ASS files and font copies are deleted; the MP4 has exactly one video and
one audio stream and no subtitle track.

## Subtitles (autonomous)

1. `subtitle-draft` writes `字幕/subtitle-<plan_id>.txt` from the Codex-reviewed shot words and keeps an
   immutable snapshot. The TXT uses SRT blocks (`index`, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, text) on the
   unaccelerated clean timeline.
2. To resegment, write the complete batch of `subtitle-<plan_id>.txt` files into a directory and run
   `subtitle-draft --subtitle-dir <dir>`. The cue count may change but the concatenated text must equal
   the verified speech. Never edit a bound snapshot. Take cue times from the reviewed word times on the
   clean timeline (`results[].timing_asr` in the reviewed clean QC report).
3. Submit the subtitle review and run `subtitle-review --review <json>`:

```json
{"schema": "video-montage-codex-subtitle-review/v260929",
 "draft_sha256": "<sha256 of reports/subtitle_draft.json>", "asset_copy_sha256": "<asset copy sha256>",
 "results": [{"plan_id": "P1", "draft_subtitle_sha256": "<draft snapshot TXT sha256>",
   "cues": [{"index": 1, "draft_start_ms": 0, "draft_end_ms": 1500, "before": "来玩无尽冬日",
             "start_ms": 0, "end_ms": 1500, "after": "来玩无尽冬日"}]}]}
```

- One cue entry per draft cue, in order, binding the draft times and text. Resegment through
  `--subtitle-dir` so that `after == before`; a changed `after` needs
  `evidence: [{"kind": "asset_copy|source_asr|source_speech|plan_speech", "sha256": "...", "text": "<quote>"}]`.
- The cues' words cover all rendered speech in order; times increase without overlap, stay inside the
  rendered shot that contains the words, and start/end within ±250 ms of the first/last word (clamped
  to the shot).
- Library terms stay whole ([text library](../autonomous/text-library.md)).

Reading rules (`semantic-reading/v1`):

- One line, at most **10 displayed characters** (digits and punctuation count, whitespace does not),
  regardless of font or layout. Split long speech into sequential, semantically complete cues; never
  shrink text or use `line_breaks`.
- Read the whole sentence first and use ASR punctuation, pauses and word times; measure real font and
  flower width instead of equal character counts. Never attach the tail of one clause to the start of
  the next, separate a number from its classifier, split a word or game name, or start a cue with a
  dependent particle (的, 了…; real words like 的确, 了解 are fine). Keep short complete speech together.
- Aim for 1.2–2.5 s per cue after the 1.2x; revise cues under 0.8 s. A flagged short cue that cannot be
  merged needs a concrete `short_cue_reason`; a flagged legitimate head word (地形, 得分, 过瘾…) needs
  `head_word`, a concrete `head_word_reason` and evidence quoting that word. Isolated particles never
  pass this way.

## Packaging config

Write the config into `<job_dir>/config/`; relative paths resolve from the config's directory.

```json
{
  "schema": "video-montage-packaging/v1",
  "text_pin_policy": "required-full-coverage/v1",
  "outputs": [{
    "plan_id": "P1",
    "input_path": "<clean MP4 path from the clean delivery manifest>",
    "subtitle_txt": "<reviewed subtitle path from reports/subtitle_review.json>",
    "subtitle_sha256": "<reviewed subtitle sha256>",
    "subtitle_design": {"subtitle_sha256": "<same sha256>", "reading_policy": "semantic-reading/v1", "font": "w8",
                        "game_names": ["无尽冬日"], "game_flower": "ice2", "reason": "...", "cues": []},
    "nameplates": [{"person_id": "person:wu-yue", "path": "<registry path>", "sha256": "<sha256>",
                    "start_frame": 0, "end_frame_exclusive": 192}],
    "text_pins": [{"path": "<asset path>", "sha256": "<sha256>", "start_frame": 0, "end_frame_exclusive": 1800}],
    "disclaimer": {"path": "<asset path>"},
    "bgm": {"path": "<asset path>", "gain_db": -18}
  }]
}
```

- `outputs` covers every plan ID. `subtitle_txt`/`subtitle_sha256` must be the reviewed subtitle;
  `subtitle_design` follows [packaging design](design.md).
- **Text pins** (required on every fully packaged video): content-appropriate, verified pin images from
  the asset pack, separate from subtitles, nameplates and disclaimers. Intervals use unaccelerated
  60 fps frames, run from frame 0 to the last frame without gaps or overlaps, and each shows for at
  least 48 final frames (0.8 s). Choose suitable existing assets; never omit pins because choosing takes
  work. Check overlap with people, subtitles and nameplates; the final `text_pin_reason` records the
  pin assets, their copy and the coverage seen in the encoded frames.
- **Nameplates**: copy the windows from the plan evidence ([selected celebrities](../autonomous/selected-celebrities.md)).
- **Disclaimer**: covers the whole video. Overlays are 9:16 PNGs with transparency.
- **BGM** loops to the end, gain -60..0 dB (default -18); speech is not ducked, and final evidence
  requires the voice to stay at least 6 dB above the music.
- `graphic_layers` must be absent or empty: decorative cards are not supported.

`package --config <json>` checks the design sources (game names must appear in the speech, asset copy
or packaging request), nameplates and pins, then renders. When the config is supplied from elsewhere,
the stored copy in `临时文件/config/` points to absolute paths and the `字幕/` copies, so its hash may
differ; the manifest binds both. Then run `final-evidence` and `complete` ([workflow](../autonomous/workflow.md)).

## Standalone packager (testing only)

`scripts/packaging/package_video.py` can package an existing clean video outside a job. Its manifest is
marked `standalone_test` and never passes the delivery gates. `--output-dir` must be
`work/自动化混剪_YYYYMMDD_HHMMSS` and `--manifest` must be in its `临时文件/manifests/`.

```text
python.exe scripts/packaging/package_video.py draft --input <clean.mp4> --plan-id demo-01 --output-dir <work/自动化混剪_...>
python.exe scripts/packaging/package_video.py render --config <config.json> --output-dir <dir> --manifest <dir>/临时文件/manifests/packaging_manifest.json
python.exe scripts/packaging/package_video.py validate --manifest <manifest> --report <dir>/临时文件/reports/packaging_validation.json
```

`validate` alone returns `technical_pass_pending_review`. Multi-line text inside one SRT block displays
line by line, splitting the time by width (each line at least 20 ms).
