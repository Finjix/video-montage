---
name: video-montage
description: Produce and repair Chinese spoken-video montage batches with source-frame editing, semantic review, FFmpeg delivery, and independent post-encode QC. Use for the bundled video-montage workflow.
metadata:
  version: "v260928"
---

# Video montage

Run this skill with the bundled Python at `dependencies/python/python.exe`. The
suite root is two directories above this file. Start with
`components/executor/scripts/three_suite_ff.py preflight`,
then use its `init`, `semantic-run`, `semantic-complete`, `controller-preflight`,
`controller-finalize`, `controller-validate`, and `complete` commands in order.

The semantic component owns source ASR, candidate evidence, independent candidate
review, batch planning, frame-plan validation, and the portable frame renderer.
Read [semantic workflow](semantic-workflow.md) and its relevant task-specific
contract before production. The FFmpeg controller owns final encoding, exact-cut
evidence, independent post-encode review, and validation. Read
[controller workflow](controller-workflow.md) when handling encoding or QC.
The [executor workflow](executor-workflow.md) documents the completion gates.

Every source segment must have integer source-frame bounds and source FPS.
Seconds-only cuts, stale evidence, rejected source ranges, partial batch scope,
generator-written approvals, and incomplete speech are hard failures. Preserve
source lineage and hash-bound task evidence. Render only through the bundled
portable frame renderer; require a complete semantic release before controller
finalization and a passing independent post-encode QC before completion. Do not
add music, captions, overlays, or extra audio unless the current user requests them.

Task evidence belongs in the job directory. The installed suite does not create
package manifests or deployment reports.
