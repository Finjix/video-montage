---
name: video-montage
description: Produce and repair Chinese spoken-video montage batches with source-frame editing, semantic review, FFmpeg delivery, and independent post-encode QC. Use for the bundled video-montage workflow.
metadata:
  version: "v260928"
---

# Video montage

Run this skill with the bundled Python at `dependencies/python/python.exe`. The
suite root is the directory containing this file when installed, or two
directories above it in the source package. Start with
`components/executor/scripts/three_suite_ff.py preflight`,
then use its `init`, `semantic-run`, `semantic-complete`, `controller-preflight`,
`controller-finalize`, `controller-validate`, and `complete` commands in order.
When packaging is requested, run `packaging-draft`, edit the `subtitle-xx.txt` and per-output
configuration, then run `packaging-finalize` and `packaging-validate` after
`controller-validate` and before `complete`. Read the
[packaging workflow](../../components/packaging/README.md). Without packaging,
keep the clean delivery behavior.
Burn subtitles into the video picture only. Put the packaged MP4, editable
`subtitle-xx.txt`, configuration, manifest, and validation JSON directly in one output directory.
Do not make `packaged/` or `subtitles/` subdirectories or duplicate config/subtitle snapshots.
When the user edits that TXT, run `packaging-reburn` (or the standalone packager's
`reburn`) into a new directory, then validate the new MP4.

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
Packaging creates a separate output from a controller-validated clean video.
Never replace the clean output or claim a standalone packaging test passed the
semantic release. An independent reviewer must inspect final picture, subtitles,
overlays, and audio before the optional packaging gate can complete.

Task evidence belongs in the job directory. The installed suite does not create
package manifests or deployment reports.
