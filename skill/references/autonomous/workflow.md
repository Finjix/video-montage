# Autonomous workflow

Entry point (from the suite root):

```text
assets/dependencies/python/python.exe scripts/autonomous/autonomous_montage.py <command> --job-dir <job_dir> [options]
```

`job_dir` is `work/自动化混剪_YYYYMMDD_HHMMSS/临时文件`, returned by `init`. `status --job-dir` prints
the phase and `last_failures`. Read evidence files directly from the paths the commands write or print.

## Stages

| # | Command | Agent input | Phase afterwards |
| --- | --- | --- | --- |
| 1 | `init --work-order -` (JSON on stdin, or a file path) | work order | `initialized`; prints `job_dir`, `delivery_directory` |
| 2 | `prepare` | – | `prepared` (raw source ASR, video specs, asset list) |
| 3 | `asset-copy --copy-text <json>` | asset copy | `copy_indexed` |
| 4 | `source-speech-evidence` | – | `source_speech_evidenced`; prints the evidence path |
| 5 | `source-speech-review --review <json>` | source speech review | `copy_indexed` |
| 6 | `plan-evidence --plan <json>` | plan | `plan_evidenced` |
| 7 | `approve-plan --review <json>` | plan review | `plan_approved` |
| 8 | `render-clean` | – | `clean_rendered` |
| 9 | `clean-qc` | – | `clean_speech_pending`; prints the evidence path |
| 10 | `clean-speech-review --review <json>` | clean speech review | `clean_validated` |

Then, **without packaging**: `final-evidence --clean` → `complete --review <json>`.
**With packaging** ([packaging workflow](../packaging/workflow.md)): `subtitle-draft [--subtitle-dir <dir>]`
→ `subtitle-review --review <json>` → `package --config <json>` → `final-evidence` → `complete --review <json>`.

Contracts per stage: speech reviews in [speech review](speech-review.md) and
[text library](text-library.md); the plan in [semantic planning](semantic-planning.md),
[batch review](batch-review.md), [selected celebrities](selected-celebrities.md) and
[audio cleanup](audio-cleanup.md); rendering loudness in [audio balance](audio-balance.md).

## Work order

```json
{
  "schema": "video-montage-autonomous-work-order/v260929",
  "requested_outputs": 40,
  "sources": [{"path": "D:/footage/source-01.mp4", "sha256": "<optional check>"}],
  "asset_root": "D:/footage/packaging-assets",
  "output_root": "D:/project/video-montage/work",
  "packaging_request": "<the user's packaging request, if any>",
  "cast_policy": "selected-celebrities/v3",
  "celebrities": [{"person_id": "person:wu-yue", "name": "吴樾",
                   "nameplate": {"path": "D:/footage/packaging-assets/明星铭牌/吴樾.png", "sha256": "<sha256>"}}]
}
```

- `sources` are distinct `.mp4/.mov/.mkv` files; `output_root` (optional) must be the project `work/`.
- `celebrities` is required: every selected celebrity with a hash-bound absolute nameplate PNG
  ([selected celebrities](selected-celebrities.md)); `cast_policy` is filled in by `init`.
- `batch_reuse_authorization` only when the user explicitly allows extra reuse ([batch review](batch-review.md)).
- `init` binds `semantic-continuity/v2`, `semantic-batch-review/v2`, `selected-celebrities/v3`,
  `spoken-audio-cleanup/v1`, `agent-speech-review/v1`, `required-full-coverage/v1` text pins,
  `semantic-reading/v1` subtitles and a snapshot of the [text library](text-library.md).

## Asset copy

`prepare` lists every `.md/.txt/.png/.jpg/.jpeg` under `asset_root` in `evidence/asset_copy_sources.json`.
Look at every image and submit:

```json
{"schema": "video-montage-agent-asset-copy/v260929", "reviewer_role": "agent",
 "sources_sha256": "<sha256 of asset_copy_sources.json>",
 "assets": [{"sha256": "<asset sha256>", "text": "<visible text, verbatim>"}]}
```

Cover every asset exactly once. `.md/.txt` text must be copied byte-for-byte; use `""` for an image
whose text is unreadable. Never guess.

## Plan evidence: machine checks

`plan-evidence` validates the plan, then for every segment extracts candidate PCM, one neutral ASR
pass, VAD/waveform cleanup evidence, boundary frames (±0.5 s) and every selected native frame at 960 px
(`continuous_visual`; 16-frame contact sheets are navigation only). It rejects a candidate when:

- the planned text differs from the reviewed source words inside its frame range, or a cut truncates a word;
- the first/last 13 frames or the nearby 30 frames show a hidden transition (adjacent mean difference
  > 0.07 or endpoint distance > 0.12).

Identical segments reuse evidence within a round, and full-interval frames are cached by source range
across rounds, so repairing one output only re-extracts changed ranges. The whole-batch report is
also written to `reports/batch_review_round_N.json`.

## Plan review (`approve-plan`)

```json
{
  "schema": "video-montage-agent-review/v260929", "stage": "plan", "reviewer_role": "agent",
  "evidence_sha256": "<plan evidence file sha256>", "plan_sha256": "<plan sha256>",
  "planning_policy": "semantic-continuity/v2", "batch_review_policy": "semantic-batch-review/v2",
  "speech_review_policy": "agent-speech-review/v1", "audio_cleanup_policy": "spoken-audio-cleanup/v1",
  "text_library_sha256": "<job text library snapshot sha256>",
  "cast_policy": "selected-celebrities/v3", "celebrities": ["<exact registry from the work order>"],
  "batch_review": {"report_digest": "...", "source_inventory_pass": true, "opening_turns_pass": true,
                   "semantic_routes_pass": true, "visual_families_pass": true, "reuse_pass": true, "reason": "..."},
  "segments": [], "transitions": [], "visual_shots": [], "outputs": []
}
```

Every list covers its full scope exactly once.

- `segments[]` (one per plan segment): `plan_id`, `segment_index` (1-based), `semantic_pass`,
  `visual_pass`, `entry_visual_pass`, `exit_visual_pass` (all `true`), `reason`, `boundary_reason`,
  `continuous_visual_sha256` (from the evidence row), `speech_review` ([speech review](speech-review.md)),
  `audio_cleanup` ([audio cleanup](audio-cleanup.md); the evidence row lists head/tail energy, clipping
  and transients for that review) and the cast fields of
  [selected celebrities](selected-celebrities.md) (`cast_reason` etc.).
- `transitions[]` (one per adjacent pair): `plan_id`, `transition_index` (1-based), `continuity_pass: true`,
  `relation_supported: true`, `missing_premise: false`, specific `reason`; plus `speaker_change_pass`
  and `speaker_change_reason` when the speaker changes.
- `visual_shots[]`: `plan_id`, `shot_index`, `source_visual_shot_id`, `shot_pass: true`, `reason`,
  `boundary_reason`, plus cast fields.
- `outputs[]`: `plan_id`, `semantic_pass`, `visual_shots_pass`, `narrative_pass` (all `true`), integer
  `visual_shot_count`, `semantic_reason`, `shot_reason`, `narrative_reason`, cast fields, and the
  batch fields `opening_visual_family_id`, `second_visual_family_id`, `opening_visual_pass: true`,
  `opening_visual_reason`.

Compute `report_digest` from the evidence file:

```text
assets/dependencies/python/python.exe -c "import json,hashlib,sys;r=json.load(open(sys.argv[1],encoding='utf-8'))['batch_review_report'];print(hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())" <evidence.json>
```

Inspect, for every candidate, the first and last 30 native frames in order and any source transition
near them; report entry and exit separately. Compare the outgoing segment's last frames with the
incoming segment's first frames for subject position, scale and shot size; choose a compatible cut
when they jump. Move a boundary past a residual transition only when a complete phrase remains.

## Clean render and QC

`render-clean` renders every output in parallel at 1440×2560, 60 fps CFR, H.264/AAC from integer
native frame ranges, applying approved audio cleanup and [audio balance](audio-balance.md). `clean-qc`
checks frame counts and timeline lineage, clipping, the 40 ms on both sides of every cut (each
≤ -30 dBFS with no transients, so cut in silence), audio-cleanup residuals, and decodes every clean
video to reject duplicate picture sequences across the batch. It recognizes each rendered shot separately (shot-local word times feed
the clean speech review and then the subtitles). Then submit the clean speech review. Inspect the rendered
video frame by frame around every splice: two shot changes in quick succession are a defect even if
each candidate edge looked stable.

## Final evidence and review (`complete`)

`final-evidence` (packaged) or `final-evidence --clean` runs ASR on the accelerated output, checks
clipping, cut PCM, speech/BGM masking (voice at least 6 dB over music), subtitle timing inside shots,
text pin coverage, nameplates, audio cleanup in the mix, and a sample-level mix proof (correlation
≥ 0.999, residual ≤ -45 dBFS, every 250 ms ≤ -38 dBFS). It extracts every final frame (cached by output
hash), ±72 frames around every cut, subtitle and overlay start/end frames and animation frames.

Final review header: `schema`, `"stage": "final"`, `reviewer_role`, `evidence_sha256` of
`reports/final_evidence.json`, `planning_policy`, `batch_review_policy`, `cast_policy`, `celebrities`,
and `text_pin_policy` when packaged. Then:

- `outputs[]`: `plan_id`, `output_sha256`, `visual_pass`, `reason`, plus when packaged `subtitle_pass`,
  `overlay_pass`, `design_pass`, `design_reason`, `text_pins_pass`, `text_pin_reason`,
  `nameplates_pass`, `nameplate_reason`. Repeat the plan-review output fields (semantic, narrative,
  shot count, cast) and `continuous_visual_sha256` of the final evidence row. Batch fields as in the
  plan review plus `reviewed_opening_frame_count` ≥ 60 (inspect at least the first 60 encoded frames).
- `transitions[]`: every transition again, judged on the actual output, with the plan-review fields
  plus `output_sha256` and the `speech_evidence_digest` from `results[].transitions[]`.
- `batch_review`: recomputed `report_digest` from the final evidence file.

`complete` re-runs the technical validator, publishes the batch and compacts the job directory. An
unpackaged delivery puts the accelerated videos in `混剪（无包装）/` and keeps the unaccelerated inputs in
`临时文件/manifests/clean_inputs/`.

## Recovering from a rejection

A failed command records `reports/repair_round_N.json`, sets phase `repair_required` and keeps all
earlier evidence. Fix the cause and resume at the earliest affected stage; do not restart the job.

| Rejected | Resume with |
| --- | --- |
| asset copy, source speech review, plan review, clean speech review, final review | correct the JSON and rerun the same command (evidence still matches) |
| plan content or candidate evidence | revise the plan → `plan-evidence` → `approve-plan` |
| `render-clean` | `approve-plan` (same review) → `render-clean` |
| `clean-qc` | repair the plan → `plan-evidence`; or rerun `clean-qc` after a transient error |
| `subtitle-review` | `subtitle-draft` (or `reburn` on a reburn job) → `subtitle-review` |
| `package`, `final-evidence` | fix the config → `package` → `final-evidence` (or resume at `subtitle-draft` for subtitle defects) |

Use `repair --reason <defect>` only to discard the plan and rebuild the edit (it revokes all
downstream approvals and keeps the failure history). Source speech cannot be re-reviewed after a plan
exists without `repair`.

## Reburn a completed delivery

Completed packaged jobs keep the clean inputs, clean QC, plan, source speech, asset copy and config.
The sources used by the batch and all assets must still exist unchanged.

- One video: `reburn --plan-id <ID> --subtitle-txt <TXT>`; whole batch: `reburn --subtitle-dir <dir>`
  (contains every `subtitle-<plan_id>.txt`; never combine with `--plan-id`).
- `reburn` prints the draft and config paths. Continue with `subtitle-review` → `package --config
  <returned config>` → `final-evidence` → `complete`. Changed cue text marks the design
  `needs_design_review`: update `subtitle_design.cues` and remove the flag.
- `reburn` can be rerun while the reburn is unfinished. Use `repair` for any change to the edit itself.
