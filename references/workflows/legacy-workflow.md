# Legacy video montage workflow

Apply the shared final 1.2x whole-video speed-up in the root `SKILL.md` after
editing and requested packaging, before final output QC and completion. Refresh
all hash-bound final evidence and approvals for the accelerated output. Retain
unaccelerated inputs for reburns and apply the speed-up only once.

For delivery without packaging, add `--final-speed` to `controller-finalize`.
It applies 1.2x before generating the new post-encode evidence and independent
review. For packaged delivery, omit that flag: the packager applies 1.2x after
burning subtitles and mixing audio. Accelerated controller outputs are rejected
as packaging input to prevent applying the speed-up twice. Changed media require
fresh QC; existing receipts remain bound to their original media.

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
Packaging randomly selects bundled WenYue W8, Smiley Sans or FangTang once per
task and burns yellow, black-outlined subtitles by default. The selected OTF in
`assets/packaging/fonts` is shared by the batch and retained for reburns.
Burn subtitles into the video picture only. All new deliveries must use a
`work/自动化混剪_YYYYMMDD_HHMMSS` directory (Beijing time). Put packaged MP4s in
`成片/`, clean MP4s in `混剪（无包装）/`, editable `subtitle-xx.txt` in
`字幕/`, and reusable configuration in `临时文件/config/`.
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


当前成品组织规则：新交付目录按北京时间命名为 `work/自动化混剪_YYYYMMDD_HHMMSS/`，不追加微秒，同秒重名拒绝覆盖。`临时文件/` 内目录使用英文。生产期间可以生成审核、证据和报告；自动流程通过全部交付检查后清理这些过程文件，仅保留 `config/`、重烧必要的 `manifests/`、最小工作单和任务状态。纯净视频和字幕继续分别保存在 `混剪（无包装）/`、`字幕/`。完整剪辑返工重新生成证据、审核和校验；字幕重烧复用纯净视频和绑定的纯净输入校验文件。旧任务仍使用其原有路径，避免破坏哈希绑定。此规则替代上文关于完成后保留全部运行记录的要求。
