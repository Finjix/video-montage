---
name: video-montage
description: Produce and repair Chinese spoken-video montage batches with native-frame editing, Codex semantic and visual review, subtitle packaging, and post-encode QC. Use the autonomous workflow for new jobs and preserve the independent-review workflow for existing legacy jobs.
metadata:
  version: "v260928"
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

Every source segment must bind original-source SHA-256, integer native-frame
bounds and source FPS. Reject seconds-only cuts, rejected source ranges, stale
evidence and incomplete speech. Keep the requested batch scope; technical decode
or a generated pass alone cannot authorize completion. Render through the bundled
portable frame renderer and apply the selected workflow's quality gates.

Honor the current user's packaging request. Do not add music, captions, overlays
or extra audio without that request. Packaging uses a validated clean video to
create a separate output and preserves the clean video. Read the
[packaging workflow](references/packaging/workflow.md) for configuration and
reburn commands. Subtitles burn into the picture only; the default style uses
the calibrated WenYue W8 yellow, black-outlined OTF from
`assets/packaging/fonts`.

New deliveries use `work/自动化混剪_YYYYMMDD_HHMMSS_ffffff` in Beijing time:

- `成片/`: packaged MP4s.
- `混剪（无包装）/`: clean MP4s.
- `字幕/`: editable `subtitle-xx.txt` and the extensionless file
  `修改字幕后让AI重新烧录`.
- `临时文件/`: job state, logs, evidence, reviews, manifests, reports and
  completion receipts, plus necessary rendering/editing files.
- `临时文件/配置/`: reusable packaging configuration.

Keep state, logs, reviews, evidence, manifests, validation reports and completion
receipts in `work/<delivery-name>/临时文件/`, alongside reusable configuration and
necessary render/edit files. Do not create a root-level `.runtime` for new jobs
or duplicate configuration/subtitle snapshots. For autonomous
init, work-order `output_root` is the project `work/` directory; use the returned
`job_dir` and read the actual generated delivery path from status.
Keep older jobs bound to their actual paths and hash-bound evidence.

Repairs and subtitle reburns reuse the existing delivery directory. Render and
check temporary videos before replacing MP4s, invalidate stale completion
receipts, and refresh manifests, reviews and verification before delivery.
Use the selected workflow's repair and reburn commands and stopping conditions.
Never report a partial batch as complete or invent approvals.
