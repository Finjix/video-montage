# Legacy video montage workflow

Apply only to existing jobs with `three_suite_ff_state.json` or v260928 review
receipts. New jobs use [autonomous workflow](autonomous-workflow.md).

Run this skill with the bundled Python at `assets/dependencies/python/python.exe`. The
suite root is the directory containing the root `SKILL.md`. Start with
`scripts/executor/scripts/three_suite_ff.py preflight`,
then use its `init`, `semantic-run`, `semantic-complete`, `controller-preflight`,
`controller-finalize`, `controller-validate`, and `complete` commands in order.
When packaging is requested, run `packaging-draft`, edit the `subtitle-xx.txt` and per-output
configuration, then run `packaging-finalize` and `packaging-validate` after
`controller-validate` and before `complete`. Read the
[packaging workflow](../packaging/workflow.md). Without packaging,
keep the clean delivery behavior.
Packaging burns the calibrated WenYue W8 yellow, black-outlined subtitle style
by default, using the fixed OTF in `assets/packaging/fonts`.
Burn subtitles into the video picture only. All new deliveries must use a
`work/自动化混剪_YYYYMMDD_HHMMSS_ffffff` directory (Beijing time). Put packaged MP4s in
`成片/`, clean MP4s in `混剪（无包装）/`, editable `subtitle-xx.txt` in
`字幕/`, and reusable configuration in `临时文件/配置/`.
Keep logs, job state, manifests, reviews, evidence and validation reports in
`work/<delivery-name>/临时文件/`, alongside necessary rendering/editing files.
Preserve existing hash-bound job paths; do not create root-level `.runtime` for new records. Always keep the
extensionless file `字幕/修改字幕后让AI重新烧录`.
When the user edits that TXT, run `packaging-reburn` (or the standalone packager's
`reburn`) into the same directory, then validate the replacement MP4. Render and
check temporary video files before replacing existing MP4s. Invalidate stale
completion receipts and refresh manifests and verification before delivery.

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
