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
randomly selects one of WenYue W8, Smiley Sans (得意黑) or FangTang (方糖体)
from `assets/packaging/fonts`, using it throughout the batch and preserving the
selection for reburns. All three fonts support the dynamic flower styles;
ordinary subtitles remain yellow with a black outline.
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
