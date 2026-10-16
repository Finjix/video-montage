# Whole-batch review (`semantic-batch-review/v2`)

An acceptance gate after semantic planning, not a clip selector. First build coherent routes from the
full source analysis, then prove the batch is genuinely different. Never shuffle dependent turns to
pass. Subtitle, font, BGM, gain, ID or tiny cut changes never count as new content.

`init` binds the policy in the work order, state and source index; a full `repair` revokes the plan
and downstream approvals.

## Cast mix

There is no single/multi-celebrity ratio and no quota. Choose single- or multi-celebrity outputs, and
allocate individual celebrities, purely by what the usable material supports and by semantic
coherence; a coherent multi-celebrity edit is as acceptable as a single-celebrity one. State the actual
counts in the batch review `reason` for the record only; no deviation reason is needed. Supporting
actors do not count.

## Plan annotations

On top of [semantic planning](planning.md), a v2 plan declares `batch_review_policy` and:

- `batch_analysis.sources`: one entry per work-order source with `source_sha256`, `source_asr_sha256`
  (raw ASR file), `source_speech_sha256` (the source index `reviewed_speech.sha256`),
  `extraction_status: "exhausted"`, a concrete `reason`, `candidates` and `spoken_turns`. Never mark a
  source exhausted before analysing all of it.
- `candidates`: every complete turn found, with `source_in_frame`, `source_out_frame_exclusive`, exact
  `text`, `status` (`eligible`/`conditional`/`rejected`) and `reason`; also `speaker_id`,
  `visible_person_ids` and `primary_person_ids` matching the selected segment. Every selected segment
  must match exactly one `eligible` candidate (same range and text). Unviewed footage, isolated safe
  ranges or file counts are not candidates.
- `spoken_turns`: canonical complete turns per source, with native range, exact `text` found in the
  reviewed speech, `complete: true`, boolean `opening_eligible`, `reason`, `speaker_id`,
  and an `opening_visual_family_id` (verified from frames) when opening-eligible. Turns of one source
  do not overlap; a turn that needs earlier context is not an eligible opening. Moving cut points or
  splitting a sentence never creates another turn.
- `segments[].semantic_cluster_id`: a stable ID for the actual proposition; different wordings of the
  same proposition share it, the same normalized text never gets two clusters, and IDs are not
  numbering or file names.
- `visual_shots[].visual_family_id`: classify by real scene, camera, costume, shot size and continuous
  shot. Renaming the same or an overlapping source shot is rejected.

```json
{"source_sha256": "<sha256>", "source_asr_sha256": "<raw ASR sha256>", "source_speech_sha256": "<reviewed speech sha256>",
 "extraction_status": "exhausted", "reason": "全片分析人物、场景与前后话轮，列明可用与拒绝项",
 "candidates": [{"source_in_frame": 120, "source_out_frame_exclusive": 480, "text": "<exact speech>",
                 "status": "eligible", "reason": "完整命题，整段仅主角出镜"}],
 "spoken_turns": [{"source_in_frame": 120, "source_out_frame_exclusive": 480, "text": "<exact speech>",
                   "complete": true, "opening_eligible": true, "opening_visual_family_id": "scene-a-camera-1",
                   "reason": "完整介绍，不依赖前文，已核实片头机位"}]}
```

## Limits

Limits apply to the current request; counts from earlier jobs never carry over.

| Check | Default | With user reuse authorization |
| --- | --- | --- |
| identical complete source-content route, identical complete speech, identical decoded clean picture sequence | 1 per batch | 1 |
| same candidate, same exact text | 6 each | 16 |
| same complete opening speech, same actual opening visual family | 4 each | 4 |
| opening + second shot family pair | 2 | 5 |
| same closing text | 4 | 10 |
| same ordered semantic route | 2 | 4 |
| same-position proposition overlap between two routes | ≤ 40% | ≤ 60% |
| whole-speech trigram Jaccard similarity between two outputs | ≤ 0.88 | ≤ 0.88 |
| same middle propositions, only head/tail changed | rejected | at most 3 outputs |

Content identity binds the source SHA-256 and normalized exact text; adjacent same-source turns merge
before comparison. Splitting a turn, renaming a proposition or reusing the same words from another
file does not create new content, and passing these limits never excuses a logic error.

The opening counts from the first complete canonical turn (merged across adjacent same-source
segments; identical text in different files counts together), and its actual first shot family must
match the turn's `opening_visual_family_id`. N outputs need at least `ceil(N/4)` distinct opening turns
and opening families (10 for 40). The report's `opening_usage.speech/visual` lists each opening, count,
plan IDs, limit and overflow; `capacity` separates the inventory upper bound, planned unique outputs and
the shortfall. An inventory upper bound does not prove enough coherent routes exist. When capacity is
short, keep analysing the sources; if it is truly insufficient, report the shortfall. Never exceed four
or call a partial batch complete.

## User-authorized reasonable reuse

Reasonable reuse is a standard, permitted tool for reaching the requested count: never ask the user
whether it is allowed. When the default limits cannot reach the count (check the source inventory
first), the agent records the authorization itself in the work order, quoting the user's actual
request (the request for that many outputs is the instruction), unless the user forbade reuse. Record:

```json
"batch_reuse_authorization": {"policy": "explicit-user-reasonable-reuse/v1", "authorized_by": "user",
                              "instruction": "<the user's actual words>"}
```

`prepare` binds it into the source index and the plan must carry the same object; without it the default
limits apply. Build coherent routes first; the caps are ceilings, not targets. Never cycle a few
finished edits, change only fonts/BGM or rename IDs to fill the count. Cast, real shots, duration, cuts,
subtitles and final QC still apply.

## Extended reuse (user accepts heavier repetition)

Only when the user explicitly accepts heavier repetition because the usable material cannot reach the requested count
under the reasonable-reuse limits, record `"policy": "explicit-user-extended-reuse/v1"` (same fields, with the user's real
words as `instruction`). Limits then rise to: same candidate/exact text 30, same opening speech or visual family 8,
opening + second family pair 10, same closing text 16, same ordered semantic route 6, same-position overlap ≤ 80%,
trigram similarity ≤ 0.93. All technical, cast, shot, audio and subtitle gates are unchanged. Disclose the extended
policy and the resulting repetition in the delivery summary.

## Reviews and clean check

`plan-evidence` writes the automatic report into the plan evidence (`batch_review_report`) and into
`reports/batch_review_round_N.json` (also for rejected rounds); `approve-plan`, rendering, packaging,
final evidence and `complete` recompute or verify it. Plan and final reviews declare
`batch_review_policy` and a `batch_review` object: `report_digest` (see the
[workflow](workflow.md#plan-review-approve-plan) command), `source_inventory_pass`, `opening_turns_pass`,
`semantic_routes_pass`, `visual_families_pass`, `reuse_pass` (all `true`) and a concrete `reason`
from the real analysis. Each `outputs[]` finding adds `opening_visual_family_id`,
`second_visual_family_id`, `opening_visual_pass: true` and `opening_visual_reason`; the final review
adds `reviewed_opening_frame_count` ≥ 60 from the actual accelerated output.

`clean-qc` fully decodes every clean video (240 px, per-frame MD5) and rejects the batch if two outputs
share a picture sequence, even with different audio or file hashes. Incomplete decodes, missing
fingerprints or a changed batch scope block subtitles, packaging and delivery. Fingerprints are kept in
`manifests/batch_frames/` for reburns.

Diagnose a plan without approving anything (exit code 2 means rejected):

```text
assets/dependencies/python/python.exe scripts/video_montage.py batch-check --job-dir <job_dir> --plan <plan.json>
```
