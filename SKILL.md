---
name: video-montage
description: Produce and repair Chinese spoken-video montage batches with native-frame editing, Codex semantic and visual review, subtitle packaging, and post-encode QC. Use the autonomous workflow for new jobs and preserve the independent-review workflow for existing legacy jobs.
metadata:
  version: "v261008"
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
dependent speech for diversity. New plans bind `semantic-continuity/v1` and need
per-transition, per-shot and whole-edit findings; the diversity selector is removed.
Historical completions and subtitle-only reburns keep their original contract.

For new jobs and full-edit repairs also read [whole-batch review](references/autonomous/batch-review.md).
Bind `semantic-batch-review/v1`. Exhaust the source-turn inventory, prove enough
coherent unique routes for the requested count, and enforce source-content,
exact-text, opening visual family, first-two-shot, closing and narrative reuse
limits. Never cycle a few prior plans to fill a batch. Check the actual complete
decoded clean videos for duplicate picture sequences before packaging. Codex
must review the hash-bound batch report and actual encoded opening families;
single-video approval or different fonts/BGM cannot authorize duplicate content.
This is an acceptance gate after semantic planning, not a diversification selector.

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
W8, Smiley Sans (得意黑) or FangTang (方糖体), keep ordinary text primarily yellow
with white support, and use complete flower text for every actual game name.
Only two reference-calibrated font sizes, 8 and 9, are allowed: ordinary text defaults to 8;
game names use 9 and are the only allowed mixed-size text within a caption.
Other emphasis may use 9 only as a separately displayed, uniformly sized caption;
never enlarge individual ordinary words inside an 8-size line. Ordinary yellow/white text uses
the calibrated CapCut black outline 40 (12.3px at 1440 width, text scale 164%),
independent of font and size. Flower outlines/glows retain the existing effect
without an additional ordinary stroke. Never use layout.size or shrink
long text. Subtitles must stay on one line; split long speech into sequential,
semantically complete reviewed cues with speech-aligned timing. Do not use line_breaks.
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
reference-derived text templates. Review actual final motion and composition,
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
