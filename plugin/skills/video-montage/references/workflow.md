# Commands and file formats

All commands: `& "<plugin_root>/scripts/video_montage.cmd" <command> --job-dir <job_dir>`. Times assume a warm
runtime cache; the first use of a feature adds its download (`runtime --feature base|media|packaging|asr [--install]`
inspects or prefetches it; it needs no job). Long commands print progress lines on stderr: wait for them.

| Command | Does | Typical time |
| --- | --- | --- |
| `init --work-order -` | validates the order (JSON on stdin or a file), creates the delivery folder and job dir, reports the asset pack | seconds |
| `prepare` | frame clock, camera cuts, speech recognition, units with clean cut points, sheets (`--fresh` recognizes again) | ~1 min per 10 sources (first use: +30-60 min of downloads) |
| `annotate --file <json>` | applies people, speakers, transcript fixes and beats; rewrites the catalog | seconds (+ aligner load when text changed) |
| `routes [--mix]` | cuttable clips, unusable beats with reasons, topics, joins of sampled verified routes → `reports/routes.md` | at most `--seconds` (40) |
| `routes --joins <json> [--fresh]` | composes `config/plan-draft.json` for the whole batch from the accepted joins | 10-60 s |
| `check --plan <json>` | exact cut frames and every rule → `reports/check.json`, `reports/scripts.md` | < 1 s |
| `render --plan <json>` | clean videos, QC, subtitle drafts, cut sheets (changed outputs only) | ~15 s per 3 videos |
| `package [--config <json>]` | designs, encodes, checks and publishes (changed outputs only) | ~25-40 s per video, 4 in parallel |
| `package --clean` | publishes the clean videos unpackaged (explicit user request only) | seconds |
| `status` | what exists so far and the next step | - |
| `decide --key K --value V --instruction "<text>"` | records a decision (the user's words, or `fallback: ...`) | - |
| `bgm-assets`, `fetch-bgm ...` | BGM inventory and licensed downloads ([packaging](packaging.md#bgm)) | - |

## Work order (`init`)

```json
{"requested_outputs": 40,
 "sources": ["D:/footage/wu/a.mp4", "D:/footage/lin/b.mp4"],
 "asset_root": "D:/footage/packaging",
 "celebrities": [{"person_id": "person:wu-yue", "name": "吴樾", "nameplate": "D:/footage/packaging/明星铭牌/吴樾.png"}],
 "request": "<the user's request, verbatim>",
 "output_root": "<optional folder; default: the user's Desktop>",
 "decisions": {"min_sources_per_output": {"value": 2, "instruction": "<the user's words>"}}}
```

- `sources`: .mp4/.mov/.mkv with a constant frame rate from timestamp 0 (otherwise `prepare` says so: make a
  constant-frame-rate copy with FFmpeg and use that).
- `celebrities`: every selected celebrity with a transparent 9:16 nameplate PNG; IDs look like `person:wu-yue`.
- `decisions` (optional): `min_sources_per_output` 1 or 2 (default 3 different source files per video);
  `bgm_repetition` `true` (one track may serve more than eight videos); `min_celebrities_per_output` 2-8 when the
  user asks for several celebrities in every video (`check` enforces it, `routes` mixes casts and lists only routes
  featuring that many; raise `--max-clips` above it for routes where a source returns).

## Catalog (`prepare`)

`temp/catalog/catalog.md` lists each source (S01...) with its camera shots and speech units (U01...). A unit is a
clause; `|` marks a sentence end. `in`: a clip may start at this unit; `out`: it may end after it (clean pause and
stable picture). `x` excluded, `R` overlaps a user-rejected interval, `*` corrected text. `>> Bnn` opens a beat;
"cannot start/end a clip" means its first/last unit has no clean cut. `S01.jpg` shows the middle frame of every shot
(`S01 #3 f574-681` = shot 3, native frames); `assets.jpg` shows every overlay PNG of the asset pack.

## Annotations (`annotate`)

One file for all sources; re-run `annotate` with the complete file after every change.

```json
{"sources": {
  "S01": {"people": ["person:wu-yue"],
          "shots": {"3": {"people": ["person:wu-yue", "person:crew-1"]}, "7": {"merge": true}},
          "cameras": [[1, 3, 5], [2, 4]],
          "speaker": "person:wu-yue", "speakers": {"U07": "person:crew-1"},
          "text": {"U12": "樾哥，", "U20": "", "U31": ["第一句，", "第二句。"]},
          "exclude": ["U09"],
          "beats": [{"units": "U02-U05", "kind": "talk", "role": "hook", "gist": "asks who still fails at the cold start and promises the fix"},
                    {"units": "U06", "kind": "talk", "role": "body", "topic": "chop-trees-first", "gist": "chop trees first at the start"},
                    {"units": "U07-U08", "kind": "talk", "role": "body", "topic": "furnace-heat", "also": ["furnace"],
                     "opening": false, "gist": "the wood feeds the furnace and the temperature rises"},
                    {"units": "U09", "kind": "talk", "role": "payoff", "topic": "easy-hooked", "closing": false,
                     "gist": "one minute to learn, three to get hooked, a tally that leads into the cta"},
                    {"units": "U11-U12", "kind": "scene", "role": "body", "gist": "friend asks why the furnace never lights"},
                    {"units": "U30-U31.2", "kind": "talk", "role": "cta", "topic": "click-to-play", "gist": "no download needed, click to play"}],
          "notes": "U12 is 樾哥 (he is addressed by name)"}}}
```

- `people`: who is visible in every shot of the source (registered celebrities, plus any other `person:` ID for
  supporting actors); `shots` overrides one shot by number. Tag what the frames show.
- `merge: true`: the detected cut starting this shot is no camera change (a flash, blur or fast motion inside one
  shot). The two shots become one; clips still cannot start or end within 0.5 s of the disturbance.
- `cameras`: optional groups of shots from one camera setup (they count as one opening camera).
- `speaker` / `speakers`: who talks (default: the single registered celebrity in `people`).
- `text`: corrected unit text; `""` deletes the unit (noise, hallucination); a list splits it into `U31.1`,
  `U31.2`... Keep the words in spoken order: the aligner re-times them.
- `exclude`: units that must never be used, e.g. beside a transition (zoom blur, flash) a sheet shows but the
  catalog missed. `exclude` and `speakers` name units by their current IDs (a split unit is `U31.1`...).
- `beats`: the only material a plan may use: runs of whole units with `kind`, `role` and an English `gist` of at
  least 12 letters, never overlapping; optional `topic` (slug: the point it makes, shared by stand-ins from other
  sources), `also` (further points), `opening: false`, `closing: false`. How to cut them: [editing](editing.md#beats).

## Routes (`routes`)

`reports/routes.md` has four parts:
- **Clips**: every run of one to three consecutive talk beats of one source that can be cut cleanly (key
  `S01:U02-U05`), with roles, topics (`+` marks `also` points, `never first`/`never last` the flags), length range,
  shot-length range, cast, how many sampled routes it opens or closes, its words and gists.
- **Beats that cannot be cut alone**, each with the reason (no clean pause, edge shot under 60 frames, rejected
  interval, a shot without a registered celebrity). Join such a beat to a neighbour or re-cut it.
- **Topics**: each topic with its speakers, the clips opening with it and the clips ending on it.
- **Joins**: grouped by what a clip ends on (its topic, else the clip and its last sentence), then what can follow
  it in a route that passes every machine check (a topic with the first sentence of each clip opening with it, or a
  clip), the routes and pairs using it, and in brackets the only relations allowed when the checks limit them.

Videos have no clip limit (`--max-clips` is an optional cap); the sampler shows routes of every length. Read the
file in parts until you have seen every join you judge. Routes keep one celebrity per video; `--mix` adds
cross-celebrity routes. A celebrity whose own clips finish no video (`routes.md` names them) hands over to or from
another celebrity without `--mix`. Neighbouring clips come from different source files (a source may return later:
a b a c). Write the joins you accept:

```json
{"joins": [{"from": "topic:chop-trees-first", "to": "topic:furnace-heat", "relation": "progression", "reason": "先砍树, then the wood 生熔炉"},
           {"from": "S08:U06-U09", "to": "S04:U07-U09", "relation": "explanation", "reason": "S08 asks 最要紧的是什么; S04 answers 提升温度"},
           {"from": "S30:U04-U05", "to": "topic:chop-trees-first", "reject": true, "reason": "大聪手把手教你 must go on in 大聪's own voice"},
           {"from": "S04:U07-U09", "to": "S20:U35-U36", "relation": "benefit", "reason": "after 升级熔炉 the 礼包码 gives the resources", "weak": true}]}
```

- `from` / `to`: a clip key, or `topic:<slug>`: on the from side every clip ending on that topic, on the to side
  every clip opening with it. A clip-to-clip join overrides a topic join for its pair; `"reject": true` (with a
  reason) removes the pairs it names from every accepted join.
- `relation`: one of the bracketed relations when routes.md lists them, otherwise any from
  [editing](editing.md#transitions). Pairs of a topic join whose words do not take the relation are left out (the
  run says how many).
- `reason`: one per join, quoting the linking words of both sides exactly as spoken (two or more Chinese
  characters from each; for a topic, from one of its clips). A reason without both quotes, or one reason reused for
  joins at different boundaries, is refused: every join is judged on its own words, never as a cross product of
  openings and endings. A join the checks can never use is reported as a note, not an error.

`routes --joins <file>` composes the batch video by video through those joins, verifying every route with the full
`check`: videos of the current plan that still pass and fit first (`--fresh` ignores them), then strong joins before
`weak` ones and videos of six or more clips before shorter ones; each video opens, for a celebrity under their fair
share, on the point and source file that have opened fewest videos (any clip that can open, not only hooks) and
continues with the least-used material at each position, preferring a source
file it has not used yet, all inside the batch limits (two videos share at most 25% of their positions, one piece of
material sits at one position in at most 25% of the batch). It writes `config/plan-draft.json` (outputs with a weak
join carry a note) and prints the clips and sources per video, the repetition figures, the videos per celebrity and,
when short, what limited it. A video already in `config/plan.json` keeps its plan ID, so render and package reuse its
work.

## Plan (`check`, `render`)

```json
{"outputs": [
  {"plan_id": "P01",
   "clips": [{"source": "S08", "units": "U06-U09"}, {"source": "S04", "units": "U07-U09"}, {"source": "S20", "units": "U35-U36"}],
   "transitions": [{"relation": "explanation", "reason": "S08 asks 最要紧的是什么; S04 answers 提升温度"},
                   {"relation": "benefit", "reason": "after 升级熔炉 the 礼包码 gives the resources"}],
   "note": "optional one-line narrative"}]}
```

- `plan_id`: letters, digits, `-` and `_`; one output per requested video.
- A clip is a contiguous unit range of one source made of whole beats. The program picks the exact in/out frames
  inside the clean pauses and maps shots, cast and nameplates itself.
- `transitions`: one per adjacent clip pair: a relation from [editing](editing.md#transitions) and a reason quoting
  the words that link the two clips.
- Edit the draft by hand only for a small fix (one clip, one relation) and `check` again; to replace videos, change
  the joins or beats and re-run `routes --joins`.
- `check` prints one line per output (frames, shot lengths, sources, cast) and every problem with its numbers, and
  writes `reports/scripts.md`: each video's clips, beats, words and gists with the relation and reason of every
  join. `render` runs the same check first and renders nothing while problems remain.

## After `render`

- `temp/sheets/<plan_id>-cuts.jpg`: first and last frame of every shot. A jump in subject position or scale, a wrong
  or missing person, two shot changes in quick succession or a transition effect is a plan problem. A transition
  the catalog missed (zoom blur, flash) belongs in the annotations (`exclude` the unit at that edge, or `merge` a
  false cut), then `annotate` and `routes --joins` again, so no other video uses that edge either.
- `temp/subtitles/subtitle-<plan_id>.txt`: SRT cues drafted from the corrected words. Edit text and times freely
  (one line per cue, at most 10 characters, inside the video, no overlaps, never across a cut). Edits survive
  re-renders while that video's clips stay the same; when its clips change, `render` writes a new draft and says so.
- Render QC problems (frame mismatch, clipping, a cut that is not silent) point to the plan or the source.

## After `package`

Videos land in the delivery folder as `<plan_id>.mp4`; `temp/sheets/<plan_id>-final.jpg` shows each settled cue.
Outputs without problems are published even while others still have problems; re-running redoes only what changed.

## Revisions and reburns

- Edit, swap or replace outputs: change the joins (`routes --joins`) or the plan, `render`, `package`. Removed plan
  IDs leave the delivery folder.
- Subtitle reburn (the user edited `temp/subtitles/subtitle-<plan_id>.txt` or asks for a wording change): `package`.
  Edited text that differs from the transcript is kept and noted.
- Sources must stay where they were; if one moved, `init` a new job.
