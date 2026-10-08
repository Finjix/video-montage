# Autonomous video montage

Use this skill for **new** jobs. Read [the complete workflow](../autonomous/workflow.md)
and run `scripts/autonomous/scripts/autonomous_montage.py` with
`assets/dependencies/python/python.exe`. The work order must name original sources,
the asset pack and requested output count. Deliver under the project work/ directory;
keep job state, evidence, reviews, logs, manifests, validation reports and completion
receipts under `work/<delivery-name>/临时文件/`. Use the job_dir returned by init.
Keep records there throughout the job, alongside necessary render/edit files and
reusable packaging config; do not create a root-level `.runtime` for new jobs.

For a batch, read [batch diversity](../autonomous/batch-diversity.md).
Build coherent whole-edit alternatives across usable hooks, narrative routes,
middle segments and endings, then run `diversify-plan` before `plan-evidence`.
Minimize opening reuse first, then whole-sequence and source/combination reuse.
When sources are limited, balance necessary reuse to fill the requested count,
including batches of 40–50; there are no hard reuse quotas. Never invent capacity,
shuffle dependent speech, or treat renamed IDs, cut jitter or new packaging as
new footage. Inspect the hash-bound diversity report and explain remaining reuse
in the plan review's `diversity_reason`. After rejecting a candidate, rebuild the
whole batch from the remaining usable alternatives instead of copying one safe
opening into every output. All normal quality gates still apply.

Codex itself examines source ASR, native decoded frames, the asset copy pack,
candidate evidence and final packaged frame evidence. Submit honest,
hash-bound `reviewer_role: codex` findings. Never claim an independent reviewer,
human listening, or forced word alignment. Machine gates re-run ASR on source,
candidate, clean and packaged media, inspect PCM, validate subtitles and
recheck output hashes. Reject ambiguous speech, visuals or copy rather than
guessing. After every rejected round, adjust the edit, replace unsuitable footage,
or rebuild and run the required checks again until complete delivery passes.
There is no three-round stopping rule and no repeated continuation approval.
Use `repair --reason <defect>` to resume an existing failed job, preserving
all previous failures, evidence, and round numbers. Continue
repairing or rebuilding until every requested video passes; never replace
quality checks with invented approvals or deliver partial results as completion.
Reuse the original delivery directory for every repair and reburn; do not create
new version folders. Render and check temporary video files before replacing
existing MP4s, update subtitles and manifests in place, and invalidate stale
completion receipts until the replacement batch passes all delivery checks.
Use `final-evidence --clean` after `clean-qc` for requests without packaging,
then complete with a fresh hash-bound final visual review. Skip subtitle and
packaging stages in that case. For a completed compact packaged job, use
`reburn --plan-id <ID> --subtitle-txt <TXT>`, obtain a new subtitle review,
package using the returned config, regenerate final evidence and review, then
complete. Bound clean QC and small source/plan/copy contexts are retained for
this route. Require accessible unchanged original sources and assets.
Autonomous processing uses staging files; only completion publishes the entire
approved batch, and replacement failures roll back already replaced files.
For every candidate, inspect the first and last 30 native frames in order,
including the required first and last 13 frames and any original source
transition near them. Report entry and exit visual findings separately.
Compare the last frames of each outgoing segment with the first frames of the
incoming segment for subject position, scale, and shot size. If the cut makes
those change abruptly, choose a compatible cut or an explicit transition.
Inspect the rendered video frame by frame around every splice; two shot changes
in quick succession are a defect even when each candidate edge looks stable.
Move a candidate boundary beyond any residual transition only when a complete
spoken phrase remains intact.

Do not migrate a job containing `three_suite_ff_state.json` or v260928 review
receipts. The [legacy workflow](legacy-workflow.md) and its
hash-bound work orders remain untouched for those jobs.


当前成品组织规则：新交付目录按北京时间命名为 `work/自动化混剪_YYYYMMDD_HHMMSS/`，不追加微秒，同秒重名拒绝覆盖。`临时文件/` 内目录使用英文。生产期间可以生成审核、证据和报告；自动流程通过全部交付检查后清理这些过程文件，仅保留 `config/`、重烧必要的 `manifests/`、最小工作单和任务状态。纯净视频和字幕继续分别保存在 `混剪（无包装）/`、`字幕/`。完整剪辑返工重新生成证据、审核和校验；字幕重烧复用纯净视频和绑定的纯净输入校验文件。旧任务仍使用其原有路径，避免破坏哈希绑定。此规则替代上文关于完成后保留全部运行记录的要求。
