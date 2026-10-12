---
name: video-montage
description: Produce and repair Chinese spoken-video montage batches with native-frame editing, Codex semantic and visual review, subtitle packaging, and post-encode QC. Use the autonomous workflow for new jobs and preserve the independent-review workflow for existing legacy jobs.
metadata:
  version: "v261010"
---

# Video montage

Use the bundled `assets/dependencies/python/python.exe`. The suite root is the directory
containing this file in both the source project and the installed skill. Resolve scripts and dependencies from that root.

## Choose the job workflow

- For new jobs and jobs containing `autonomous_state.json`, read
  [autonomous workflow](references/workflows/autonomous-workflow.md) and the linked component contract.
  Run `scripts/autonomous/scripts/autonomous_montage.py`. Codex reviews the
  actual ASR, native frames, asset copy, candidate and final packaged evidence;
  machine gates check ASR, PCM, subtitles and hashes. Do not claim independent
  review, human listening or forced word alignment.
  New jobs also bind `spoken-audio-cleanup/v1`; read
  [spoken audio cleanup](references/autonomous/audio-cleanup.md). Review source
  waveform, word context and VAD proposals before authorizing sample-bound mutes.
  Preserve natural expression, picture duration and subtitles; clean source audio
  before BGM and final speed-up. Never mute merely because ASR produced no text.
- For existing jobs containing `three_suite_ff_state.json` or v260928 review
  receipts, read [legacy workflow](references/workflows/legacy-workflow.md). Keep the bundled
  `scripts/executor/scripts/three_suite_ff.py` state machine, hash-bound work
  orders and independent candidate/post-encode review gates. Read its linked
  semantic, controller and executor contracts when handling those stages.

Determine the route from the existing job state before repairing or reburning.
Do not migrate legacy state or turn old receipts into autonomous approvals.
The workflow selection governs reviewer roles and completion gates; requirements
from one route do not substitute for the other route's contract.

## Shared editing and delivery rules

For new autonomous edits and full-edit repairs, read
[semantic-first planning](references/autonomous/semantic-planning.md). Restore
complete source-context analysis, grounded adjacent relations and whole-edit
narrative review. Require a 21.6–36 second natural-speed clean input, an 18–30
second final delivery, at least four real visual shots, and one declared visible
protagonist per output. No other character may appear even alongside the lead;
offscreen dialogue is allowed. Inspect every selected native frame and final
encoded frame. Do not split one continuous shot to inflate counts or shuffle
dependent speech for diversity. New plans bind `semantic-continuity/v2` and need
per-transition, per-shot and whole-edit findings; the diversity selector is removed.
Historical completions and subtitle-only reburns keep their original contract.
Every transition must cite retained prior speech in `content_evidence.premise_refs`.
When a topic change needs a bridge, cite retained adjacent speech in `bridge_ref`.
Discovery/ad-entry or audience-size claims do not directly establish gameplay
mechanics; product names, relation labels and editorial explanations cannot supply
missing spoken premises. Review relation support and missing premises individually,
then review the whole narrative. Final v2 reviews repeat all transition findings,
bound to each actual output hash and its `speech_evidence_digest`.

For new jobs and full-edit repairs also read [whole-batch review](references/autonomous/batch-review.md).
Bind `semantic-batch-review/v2`. Exhaust the source-turn inventory, prove enough
coherent unique routes for the requested count, and enforce source-content,
exact-text, opening visual family, first-two-shot, closing and narrative reuse
limits. Never cycle a few prior plans to fill a batch. Check the actual complete
decoded clean videos for duplicate picture sequences before packaging. Codex
must review the hash-bound batch report and actual encoded opening families;
single-video approval or different fonts/BGM cannot authorize duplicate content.
This is an acceptance gate after semantic planning, not a diversification selector.
When the user explicitly permits additional reuse to meet the requested count,
record their instruction in the work order as `batch_reuse_authorization` with
`policy: explicit-user-reasonable-reuse/v1`, `authorized_by: user` and `instruction`.
Use the documented reasonable-reuse caps in the batch contract. Preserve coherent
speech, real shots, protagonist restrictions and full quality review. Never repeat
whole finished edits or use packaging changes to conceal substantial duplication.
Without explicit user authorization, retain the other stricter reuse caps.
For v2, the same complete opening speech and actual opening visual family each
have a hard per-batch maximum of four, including authorized reasonable reuse.
Inventory canonical complete `spoken_turns`, their opening suitability and actual
opening families; splitting a turn, renaming IDs or moving cut points cannot
create another opening. Inspect the opening usage and capacity-shortfall report.
When coherent capacity is insufficient, report the shortfall; do not exceed four,
shuffle dependent speech or count a partial batch as complete. New init/full
repair use v2; preserve v1 limits and report formats for historical jobs/reburns.

Before delivering any finished video, apply a single whole-video 1.2x speed-up
after editing and any requested packaging, and before final evidence, review and
completion. Speed up picture and all mixed audio together, preserve audio pitch,
and keep burned subtitles and overlays synchronized with the picture. The final
duration is the pre-speed duration divided by 1.2; retain the required output FPS.
Keep unaccelerated clean inputs and their editable subtitle/overlay timing for
reburns. Rebuild from those inputs and apply 1.2x once on each new final render;
never accelerate an already accelerated delivery again. Without packaging, the
delivered clean version must also receive this final speed-up. Final ASR, audio,
visual checks, timeline evidence, manifests and hashes must describe the actual
accelerated output; old pre-speed approvals cannot authorize it.

New autonomous jobs must keep adjusting, repairing or rebuilding until every
requested finished video passes all delivery checks. There is no retry ceiling
or additional continuation approval. Preserve failed-round history while working;
never bypass quality gates or report a partial batch as complete. Pause only
when the user asks to stop or an external dependency truly prevents progress.

Every source segment must bind original-source SHA-256, integer native-frame
bounds and source FPS. Reject seconds-only cuts, rejected source ranges, stale
evidence and incomplete speech. Keep the requested batch scope; technical decode
or a generated pass alone cannot authorize completion. Render through the bundled
portable frame renderer and apply the selected workflow's quality gates.
Require decoded constant-rate timestamps starting at zero; reject variable-rate
or shifted sources before authorizing frame/FPS audio cuts. Plan IDs must also
be unique ignoring case on Windows.

Honor the current user's packaging request. Do not add music, captions, overlays
or extra audio without that request. Packaging uses a validated clean video to
create a separate output and preserves the clean video. Read the
[packaging workflow](references/packaging/workflow.md) for configuration and
reburn commands. Subtitles burn into the picture only. Each new packaging task
uses a Codex-authored design for each video: choose its main font from WenYue
W8 (新青年体) or FangTang (方糖体), keep ordinary text primarily yellow
with white support, and use complete flower text for every actual game name.
Never use an ordinary all-white cue. Yellow remains the main color; when a cue
needs selective emphasis, make the emphasized words yellow and the rest white
within the same cue. Ordinary narration can remain entirely yellow. Existing
game-name flowers keep their calibrated palette.
Only two reference-calibrated font sizes, 8 and 9, are allowed: ordinary text defaults to 8;
game names use 9 and are the only allowed mixed-size text within a caption.
Other emphasis may use 9 only as a separately displayed, uniformly sized caption;
never enlarge individual ordinary words inside an 8-size line. Ordinary yellow/white text uses
the calibrated CapCut black outline 40 (12.3px at 1440 width, text scale 164%),
independent of font and size. Flower outlines/glows retain the existing effect
without an additional ordinary stroke. Never use layout.size or shrink
long text. Subtitles must stay on one line with at most 10 displayed characters
(digits and punctuation count; whitespace does not). This is a hard limit even
when a wider layout or font would fit. Split long speech into sequential,
semantically complete reviewed cues with speech-aligned timing. Do not use line_breaks.
Subtitle drafts use `semantic-reading/v1`: preserve ASR punctuation, pauses and
word timestamps when proposing boundaries, and measure the calibrated font/flower
width rather than dividing narration into equal character counts. Review complete
sentences before individual cues. Never attach the tail of one clause to the
beginning of the next, separate a quantity from its classifier, or leave dependent
particles such as 的/了 at a cue head; actual words such as 的确/了解 remain valid.
For a flagged but legitimate complete word (for example 地形/得分/过瘾), supply
the `head_word`, a concrete `head_word_reason`, and hash-bound speech/copy evidence
quoting that exact word. Do not approve an isolated dependent particle this way.
Keep short complete speech together. Judge reading time after the final 1.2x:
aim for 1.2–2.5 seconds and revise cues below 0.8 seconds; unavoidable standalone
short phrases require a concrete `short_cue_reason` in the subtitle review.
For a new draft, use `subtitle-draft --subtitle-dir <complete batch TXT directory>`
to refresh the immutable draft snapshots after Codex resegmentation, then perform
the normal word/shot-bound review. Completed deliveries still use `reburn`.
New designs bind `reading_policy: "semantic-reading/v1"`. At most one actual
game name per video may display alone. Choose it by context, leaving meaningful
surrounding phrases; never create isolated fillers such as 玩. Require at least
0.8 seconds of final display and 0.6 seconds fully settled, use existing complete
flower text at size 9, prefer ice_drift, and default to a 200ms entrance on the
unaccelerated timeline. If those conditions fail, retain readable mixed text.
After subtitle review, read [AI packaging design](references/packaging/design.md),
inspect the effect catalog and relevant previews, then write `subtitle_design`
with `graphic_layers` omitted or empty. Select only existing installed library
resources. Codex judges suitability, emphasis and effect density; it must not
create temporary effects, animations, shaders, flower styles or decorative assets.
If none fits, use static subtitles. Only choose documented controls such as
verified text, main font, yellow/white color, size 8/9, layout and duration;
do not invent effect parameters or change calibrated implementations.
Use the existing ice1/ice2/fire1 flowers
and bounce_up/shout_wave/ice_drift entrances, or static (none); fade is not allowed; keep ordinary
narration readable and mostly static. Do not add decorative cards or the removed
reference-derived text templates. Packaging must vary effects across the batch:
use all three existing animations in batches of three or more outputs, at least
two in a two-output batch, and avoid one effect exceeding 70 percent of animated
cues. Choose by speech meaning and available space, not blind cycling. Match
ice_drift to icy/game-name atmosphere, shout_wave to emotional emphasis, and
bounce_up to short positive hooks. Check the full batch distribution before burn-in.
Do not require animation on every cue; preserve stable reading time. Review actual final motion and composition,
providing `design_pass` and a concrete `design_reason`. Old configurations without design
fields are rejected; every packaging and reburn configuration requires an explicit design.
Without packaging, use `clean-qc`, `final-evidence --clean` and `complete` with
a fresh final visual review. For packaged delivery, use the subtitle and
packaging stages before final evidence and completion.

New deliveries use `work/自动化混剪_YYYYMMDD_HHMMSS` in Beijing time. Refuse collisions rather than overwriting another job.

- `成片/`: packaged MP4s.
- `混剪（无包装）/`: clean MP4s for reburns.
- `字幕/`: editable `subtitle-xx.txt` and the extensionless instruction file.
- `临时文件/config/`: reusable packaging configuration.
- `临时文件/manifests/`: packaging manifest, clean-input authorization and bound
  plan, source ASR and asset-copy context needed for reburn.
- `临时文件/`: minimal job state and work order needed to restart repairs.

All subdirectories inside `临时文件/` use English names. Generate logs, evidence,
reviews and reports during processing and QC, then remove them after successful
completion. Preserve only files necessary for subsequent editing, reburn and repair.
The project `work/` root contains batch delivery directories only, never loose
files or temporary utility directories. This applies to Codex-created helper
scripts, request/work-order JSON, preview images, contact sheets, scans, logs and
diagnostics as well as files created by the production commands. Store disposable
helpers in `<job_dir>/scripts/`, input configuration in `<job_dir>/config/`, and
other temporary material in the appropriate English subdirectory of `<job_dir>`.
Before init returns a job directory, pass an in-memory work order through
`init --work-order -` on stdin; init saves its snapshot inside that batch.
After init, use its returned `job_dir` for every temporary output and resolve
helper paths from explicit project/job roots, not the helper's parent directory.
On completion, remove disposable helpers with the other process records. Check
that this task created no loose files or utility directories directly in `work/`.
A compact completed autonomous job regenerates evidence from sources on repair;
never reuse deleted evidence or claim old approvals remain valid. Legacy jobs retain
their existing hash-bound paths until their workflow supports compact delivery.
For subtitle-only edits on a completed autonomous job, use `reburn --plan-id <ID>
--subtitle-txt <TXT>`, then obtain new subtitle and final reviews. The command
returns the configuration for the next packaging stage.
For autonomous init, work-order `output_root` is the project `work/` directory;
use the returned `job_dir` and actual delivery path from status.

Repairs and subtitle reburns reuse the existing delivery directory. Render and
check temporary videos before replacing MP4s, invalidate stale completion
receipts, and refresh manifests, reviews and verification before delivery.
Autonomous completion publishes the whole approved batch; file replacement
errors roll back previous replacements. Packaging finishes every encoder before
publishing any delivered file.
Use the selected workflow's repair and reburn commands and stopping conditions.
Never report a partial batch as complete or invent approvals.
