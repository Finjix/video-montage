# Workflow and file formats

All commands: `assets/dependencies/python/python.exe scripts/video_montage.py <command> --job-dir <job_dir>`.
Results print on stdout; progress lines on stderr. Exit code 2 means the input broke a rule: read the listed
problems, fix the input and run the same command again. Every command can be re-run; nothing needs a repair step.

| Command | Does | Typical time |
| --- | --- | --- |
| `init --work-order -` | validates the order (JSON on stdin or a file), creates the delivery folder and job dir | seconds |
| `prepare` | frame clock, camera cuts, speech recognition, units with clean cut points, sheets (a re-run keeps the speech and unit IDs; `--fresh` recognizes again) | about 1 min per 10 sources |
| `annotate --file <json>` | applies people, speakers and transcript fixes; rewrites the catalog | seconds (+10 s when text changed) |
| `check --plan <json>` | resolves exact cut frames and checks every rule; writes `reports/check.json` | < 1 s |
| `render --plan <json>` | clean videos, QC, subtitle drafts, cut sheets (changed outputs only) | ~15 s per 3 videos |
| `package [--config <json>]` | designs, encodes, checks and publishes (changed outputs only) | ~15-40 s per video, 4 in parallel |
| `package --clean` | publishes the clean videos unpackaged (explicit user request only) | seconds |
| `status` | what exists so far | - |
| `decide --key K --value V --instruction "<user's words>"` | records a user decision | - |
| `bgm-assets`, `fetch-bgm ...` | BGM inventory and licensed downloads ([packaging](packaging.md#bgm)) | - |

## Work order (`init`)

```json
{"requested_outputs": 40,
 "sources": ["D:/footage/wu/a.mp4", "D:/footage/lin/b.mp4"],
 "asset_root": "D:/footage/packaging",
 "celebrities": [{"person_id": "person:wu-yue", "name": "吴樾", "nameplate": "D:/footage/packaging/明星铭牌/吴樾.png"}],
 "request": "<the user's request, verbatim>",
 "output_root": "<optional folder; default: the user's Desktop>"}
```

`sources` are .mp4/.mov/.mkv files with a constant frame rate from timestamp 0 (otherwise `prepare` says so: make
a constant-frame-rate copy with FFmpeg and use that). `celebrities` lists every selected celebrity with a
transparent 9:16 nameplate PNG; person IDs look like `person:wu-yue`.

User decisions (only after the user answered; `decide` adds them later):

| Key | Value | Effect |
| --- | --- | --- |
| `min_sources_per_output` | `1` or `2` | fewer than 3 source files per video |
| `reuse` | `"reasonable"` or `"extended"` | higher batch reuse limits ([editing](editing.md#batch-limits)) |
| `bgm_downloads` | `true` | download extra BGM beside the pack's own audio |
| `bgm_repetition` | `true` | one BGM track may serve more than 67% of the videos |

In the work order: `"decisions": {"reuse": {"value": "reasonable", "instruction": "<the user's words>"}}`.

## Catalog (`prepare`)

`temp/catalog/catalog.md` lists each source (S01...) with its camera shots and its speech units (U01...).
A unit is a clause; `|` marks a sentence end. `in` means a clip may start at this unit, `out` that it may end
after it (both need a clean pause and a stable picture). `x` excluded, `R` overlaps a user-rejected interval,
`*` corrected text. `S01.jpg` shows the middle frame of every shot (`S01 #3 f574-681` = shot 3, native frames);
`assets.jpg` shows every overlay PNG of the asset pack.

## Annotations (`annotate`)

One file for all sources; re-run `annotate` with the complete file whenever you change it.

```json
{"sources": {
  "S01": {"people": ["person:wu-yue"],
          "shots": {"3": {"people": ["person:wu-yue", "person:crew-1"]}, "7": {"merge": true}},
          "cameras": [[1, 3, 5], [2, 4]],
          "speaker": "person:wu-yue", "speakers": {"U07": "person:crew-1"},
          "text": {"U12": "樾哥，", "U20": "", "U31": ["第一句，", "第二句。"]},
          "exclude": ["U09"],
          "notes": "U12 is 樾哥 (context: he is addressed by name)"}}}
```

- `people`: who is visible in every shot of the source (registered celebrities and any other `person:` IDs for
  supporting actors); `shots` overrides a shot by number. Tag what the frames show, never what a folder name says.
  Every shot used in a video must show at least one registered celebrity.
- `merge: true`: the detected cut that starts this shot is not a real camera change (flash, fast motion).
- `cameras`: optional groups of shots filmed by the same camera setup (they count as one opening camera).
- `speaker` / `speakers`: who talks (default: the single registered celebrity in `people`).
- `text`: the corrected text of a unit; `""` deletes it (noise, hallucination); a list splits it into
  `U31.1`, `U31.2`... The aligner re-times corrected text, so keep the words in spoken order.
- `exclude`: units that must not be used (off-topic, broken audio, wrong person speaking...).

## Plan (`check`, `render`)

```json
{"outputs": [
  {"plan_id": "P01",
   "clips": [{"source": "S08", "units": "U06"}, {"source": "S08", "units": "U13"}, {"source": "S04", "units": "U07-U09"}],
   "transitions": [{"relation": "question_answer", "reason": "clip 2 answers what matters most: temperature"},
                   {"relation": "explanation", "reason": "explains how to get temperature: chopping lights the furnace"}],
   "note": "optional one-line narrative"}]}
```

- `plan_id`: letters, digits, `-` and `_`; one output per requested video.
- A clip is a contiguous unit range of one source (`"U07-U09"`). The program chooses the exact in/out frames
  inside the clean pauses so that duration, shot lengths and silent cuts hold, and maps the shots, cast and
  nameplates itself.
- `transitions`: one per adjacent clip pair, with a relation from [editing](editing.md#transitions) and a concrete
  reason (what links the two clips).
- `check` prints one line per output (frames, shot lengths, sources, cast) and every problem with its numbers.
  `render` runs the same check first and renders nothing while problems remain.

## After `render`

- `temp/sheets/<plan_id>-cuts.jpg`: the first and last frame of every shot in order. Look across each cut: a jump in
  subject position or scale, a wrong or missing person, two shot changes in quick succession or a transition
  effect is a plan problem.
- `temp/subtitles/subtitle-<plan_id>.txt`: SRT cues drafted from the corrected words. You may edit text and times
  (one line per cue, at most 10 characters, inside the video, no overlaps, never across a cut).
- Render QC problems (frame mismatch, clipping, a cut that is not silent) are listed per output; they mean a plan or
  source problem, not a reason to retry blindly.

## After `package`

Videos land in the delivery folder as `<plan_id>.mp4`. `temp/sheets/<plan_id>-final.jpg` shows each cue settled in
the encoded video. `temp/delivery.json` records what was published. Re-running `package` re-encodes only outputs
whose inputs changed.

## Revisions and reburns

- Edit, swap or replace outputs: change the plan, `render`, then `package`. Removed plan IDs are deleted from the
  delivery folder.
- Subtitle reburn (the user edited `temp/subtitles/subtitle-<plan_id>.txt`, or asks for a wording change): run
  `package`. Edited text that differs from the transcript is kept and noted.
- Sources must stay where they were; if one moved, `init` a new job.
