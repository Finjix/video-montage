---
name: video-montage
description: Produce, repair and reburn batches of Chinese spoken-word celebrity montage videos with native-frame editing, agent-driven speech, semantic and visual review, subtitle and overlay packaging (default) and post-encode QC.
metadata:
  version: "0.0.1"
---

# Video montage

## Runtime

- Run every script with the bundled `assets/dependencies/python/python.exe`. The suite root is the
  directory containing this file; resolve scripts, assets and dependencies from it.
- Never substitute system Python, task-local renderers, hand-edited state or plans, or direct
  replacement of delivered files.
- Independent FFmpeg work (clean renders, packaging encodes, frame evidence) runs concurrently,
  by default `min(4, CPU cores / 4)` jobs. Set `VIDEO_MONTAGE_WORKERS=1` on low-memory machines.

## Workflow

Run every job through `scripts/video_montage.py <command>`; read the
[workflow](references/workflow.md) first. `init` binds the current policy versions into
the work order, state, source index, plan, evidence and reviews; never delete, rename or change a
policy marker to pass a gate. Jobs from earlier versions of this skill are not supported.

## Non-negotiable rules

1. **Honest review.** The agent itself inspects the actual ASR, native frames, asset copy, candidate
   evidence and final encoded evidence, and signs reviews with `reviewer_role: "agent"`. Never claim
   an independent reviewer, human listening or forced alignment. Booleans and keyword matches are
   not judgments: every finding needs a concrete reason.
2. **ASR is a draft.** Keep raw ASR immutable and review real speech separately. When wording cannot
   be fully confirmed, infer the most likely words from ASR, adjacent dialogue, scene context and
   fixed spellings, record the inference and its basis, and continue. Never ask the user for a
   transcript, and never replace real speech with ad copy.
3. **Frame authority.** Every source segment binds original-source SHA-256, integer native frame
   bounds and source FPS. Seconds-only cuts, stale evidence and incomplete speech are hard failures,
   as are ranges in `assets/rejected-source-intervals.json` (matched by source hash
   and frames; renaming never bypasses them). Sources must decode at a constant frame rate from
   timestamp 0; otherwise create a traceable CFR copy and prepare again.
4. **Edit shape.** Clean input and final delivery are both 1020–1800 frames (17–30 s at 60 fps,
   natural speed); at least four real visual shots. No freezes, slow motion, repeated
   source frames, half sentences or filler.
5. **Final delivery, no speed-up.** The packaged video keeps the clean timeline (speed 1.0); picture
   and mix are never accelerated. Final ASR, audio, frames, manifests and hashes describe the output.
6. **Full packaging by default.** A request to run a batch means complete packaged finished videos
   (subtitles, nameplates, text pins, disclaimer, BGM from the asset pack); never ask the user whether
   to package, and never ask for confirmation of defaults. Deliver unpackaged videos only when the
   user explicitly says no packaging. Packaging writes a separate output and preserves the clean video;
   subtitles are burned into the picture only. Add extra audio or overlays beyond the asset pack only
   when the user asks.
7. **Whole batch.** Deliver exactly the requested count; coherent, logical before-and-after meaning
   comes first, and the permitted reasonable reuse (never ask the user for it) is used whenever the
   inventory needs it. There is no single/multi-celebrity ratio. Never report a partial batch as complete,
   invent approvals, or bypass a gate.
8. **Persist until done.** Once a task starts, keep repairing and rebuilding until every requested
   video passes; there is no retry ceiling and no need to ask permission to continue. A rejected
   command or review means "fix and continue". Stop when the user says so. Ask the user only when an
   absolute blocker prevents all remaining useful work after repairs and alternatives are exhausted;
   then report the blocker, what was tried and the minimum input needed. Uncertain speech, ASR
   disagreement, being unable to listen, a failed round or long processing are never blockers.

## Delivery layout

New jobs deliver to `<Desktop>/自动化混剪_YYYYMMDD_HHMMSS/` (the user's Desktop, or the work order's
`output_root`; Beijing time; a same-second collision is refused, never overwritten):

| Path | Content |
| --- | --- |
| `成片/` | packaged MP4s |
| `混剪（无包装）/` | clean MP4s (unaccelerated reburn inputs when packaged; the accelerated delivery when not) |
| `字幕/` | editable `subtitle-<plan_id>.txt` and the extensionless file `修改字幕后让AI重新烧录` |
| `临时文件/` | the job directory (`job_dir`): state, work order, `config/`, `manifests/`, evidence, reviews, reports |

- All subdirectories of `临时文件/` use English names. Never write loose files next to the
  batch directories: put helper scripts in `<job_dir>/scripts/`, input JSON in `<job_dir>/config/`,
  previews, contact sheets and scans in `<job_dir>/evidence/`, and logs in an English subdirectory.
  Before a job exists, pass the work order to `init --work-order -` on stdin. Resolve helper paths
  from explicit project/job roots, not the helper's own location.
- Repairs and reburns reuse the same delivery directory. Work happens in staging; `complete`
  publishes the whole approved batch at once, rolls back on a replacement error, and invalidates
  stale completion receipts.
- After a successful completion the workflow deletes disposable evidence and keeps the completion
  receipt with its final findings, `config/`, the reburn `manifests/`, the work order and minimal
  state. Delete your own disposable helpers too and confirm nothing was left outside the batch directory. Deleted
  evidence is never reused; a full repair regenerates it.

## References

Read the workflow first; open the others when the stage needs them:

- [workflow](references/workflow.md): commands, stage gates, review formats, recovery, repair, reburn
- [speech review](references/speech-review.md) and [text library](references/text-library.md): source, candidate and clean speech reviews
- [planning](references/planning.md): plan format and editorial rules
- [batch review](references/batch-review.md): inventory, reuse limits, opening capacity
- [celebrities](references/celebrities.md): cast, supporting actors, nameplates
- [audio cleanup](references/audio-cleanup.md) and [audio balance](references/audio-balance.md)
- [packaging](references/packaging.md) and [packaging design](references/packaging-design.md): needed for every packaged batch (the default)
