# 语义优先的整批审核（semantic-batch-review/v1）

这是原 FF 批次验收检查在自主流程中的恢复，不是选片排序器。先分析完整原片语境并建立真实连贯的路线，再审核整批内容差异。不得恢复为降重而打乱依赖话轮的 `diversify-plan`。包装、字幕和 BGM 的变化不算底片内容变化。

2026-10-10 起，`init` 将 `batch_review_policy: "semantic-batch-review/v1"` 固化在工作单和状态。完整 `repair` 升级工作单快照并撤销原计划及下游审批。历史状态没有该版本时按原契约读取；单纯 `reburn` 保留任务原有批次版本，不把新审核强加给已完成的旧视频。新规则不能从工作单或状态单方删掉。

## 原片分析和计划

仍先满足 [单条语义规划](semantic-planning.md) 中的叙事、人物、时长和镜头规则。新计划增加：

- 顶层 `batch_review_policy`：上述固定版本。
- `batch_analysis.sources`：逐项覆盖工作单素材哈希；每项包含 `source_sha256`、当前完整转写的 `source_asr_sha256`、`extraction_status: "exhausted"`、具体 `reason` 和 `candidates`。没有分析完的素材不能标为 exhausted；partial/not_started、缺源、陈旧转写和缺理由均拒绝。
- `candidates`：记录发现的完整话轮，包括 `source_in_frame`、`source_out_frame_exclusive`、精确 `text`、`status`（eligible/conditional/rejected）和具体 `reason`。选取区间必须精确匹配 eligible 单元；conditional/rejected 不能参与容量计算。不得把未查看的片段、孤立安全区或文件数量当作完整候选。
- 每个 `segments` 增加稳定的 `semantic_cluster_id`，表示实际完整命题。相同命题的不同说法也应同属一类；不得用编号、源文件名或每条新建的 ID 充当语义分类。同一归一化台词不能改用不同分类。
- 每个 `visual_shots` 增加稳定的 `visual_family_id`，按实际场景、机位、服装、景别和连续源镜头分类。同一镜头不同时间段、改候选 ID 或轻微移动切点不创造新片头类别。程序拒绝同一源镜头或重叠源画面改名，Codex 从完整连续帧核实类别和真实变化。

库存示例（实际任务要完整覆盖所有原片和发现的话轮）：

```json
{"source_sha256":"<原片哈希>","source_asr_sha256":"<完整转写文件哈希>","extraction_status":"exhausted","reason":"完整分析人物、场景和前后话轮；列明可用与拒绝项","candidates":[{"source_in_frame":120,"source_out_frame_exclusive":480,"text":"<完整精确口播>","status":"eligible","reason":"完整命题且整个区间仅主角出镜"}]}
```

## 批次拒绝条件

恢复原 FF 反馈契约中的以下验收上限，适用于当前请求数量，不继承历史成片数量或历史时长：

| 检查 | 上限 |
| --- | --- |
| 相同完整源内容组合、相同整条口播 | 每批一次 |
| 相同候选内容、相同精确台词 | 各最多 6 次 |
| 相同开场台词、实际片头画面类别 | 各最多 3 次 |
| 片头与第二个实际镜头的类别组合 | 最多 2 次 |
| 相同结尾台词 | 最多 4 次 |
| 相同有序语义路线 | 最多 2 次 |
| 两条路线同位置命题重合 | 不超过 40% |
| 两条完整口播三字组相似度（Jaccard） | 不超过 0.88 |
| 只改头尾、相同中段命题路线 | 拒绝 |

源组合身份绑定源 SHA-256 和归一化精确台词；相邻同源台词合并后比较，忽略候选 ID、文件名、音量、字体及微小切点变化。分拆同一话轮、给同一命题改名、换一遍相同台词的素材，都不能冒充不同内容。仍必须通过真实语义衔接检查；差异不能豁免逻辑错误。

请求 N 条时必须实际提供 N 条通过以上检查且各自语义成立的完整方案，至少有 `ceil(N/3)` 类合格片头。40 条需要至少 14 类。容量由实际完整方案和审核后的类别推导，不能靠声称“素材足够”或增加编号过关。不足时继续分析原片和建立完整路线；禁止循环复制少量方案凑数，也禁止把群演画面、半句话或不连贯片段纳入容量。

## 审核和渲染后复核

`plan-evidence` 在模型和渲染启动前执行整批检查，将自动报告写入绑定计划的 `batch_review_report`。`approve-plan`、渲染、包装、最终取证和 `complete` 均重算或核验此报告；旧单条审批不能批准新批次版本。

计划与最终 Codex review 顶层声明 `batch_review_policy`，增加 `batch_review`：`report_digest` 是 `batch_review.digest(evidence["batch_review_report"])`，并含 `source_inventory_pass`、`semantic_routes_pass`、`visual_families_pass`、`reuse_pass` 全部为 true，以及具体 `reason`。这些结论必须来自真实原片分析、完整区间画面与整批比较，不得抄通用模板。

逐成片 `outputs` 增加 `opening_visual_family_id`、`second_visual_family_id`、`opening_visual_pass: true`、具体 `opening_visual_reason`；最终还增加整数 `reviewed_opening_frame_count >= 60`。必须查看实际倍速成品的至少前 60 帧，并确认类别与计划一致；完整成片帧审核继续执行。

`clean-qc` 在口播、切点和帧数检查后，对每条未包装底片完整解码，生成 240px 宽、逐帧 MD5 序列与全序列摘要。相同整条画面即使配音、增益、编码文件哈希不同，也拒绝整批。未完整解码、指纹缺帧、证据变化或批次范围缺项均不能进入字幕、包装或交付。`require_clean_qc` 在后续阶段重验此证据和实际输入哈希。

发布时迁移底片授权与指纹对应关系；完成后在 `临时文件/manifests/batch_frames/` 保留小型指纹文件，供新版本字幕重烧沿用底片授权。重新编码的倍速成品仍需新的完整最终审核。原始归档项目保持只读，已有成片不自动返工。

单独诊断计划（返回码 2 表示拒绝，不生成审批）：

```text
assets/dependencies/python/python.exe scripts/autonomous/scripts/batch_review.py --plan <计划JSON> --source-index <原片索引JSON> --report <报告JSON>
```
