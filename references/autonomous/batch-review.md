# 语义优先的整批审核（semantic-batch-review/v2）

这是原 FF 批次验收检查在自主流程中的恢复，不是选片排序器。先分析完整原片语境并建立真实连贯的路线，再审核整批内容差异。不得恢复为降重而打乱依赖话轮的 `diversify-plan`。包装、字幕和 BGM 的变化不算底片内容变化。

新 `init` 将 `batch_review_policy: "semantic-batch-review/v2"` 固化在工作单、状态及原片索引。完整 `repair` 升级工作单快照并撤销原计划及下游审批。历史v1和未绑定批次策略的任务按原契约读取；单纯 `reburn` 保留任务原有批次版本。v2不能删除标记或降级为v1计划和审批。

## 原片分析和计划

绑定 `codex-speech-review/v1` 时，候选库存和完整话轮依据[已审校源台词](speech-review.md)。
每个 `batch_analysis.sources[]` 同时保留 `source_asr_sha256` 并增加 `source_speech_sha256`，
后者绑定源索引的 `reviewed_speech.sha256`，不能退回含误识别的原始文字或丢失审核绑定。

批次以单明星成片为主，默认目标为单明星约80%、多明星约20%，按成片条数统计，不能用镜头数量或出镜时长代替。40条通常规划约32条单明星、8条多明星；多明星指该条实际出现两位或更多所选明星。比例是规划偏好，不是严格配额或机器拒绝条件。AI可根据有效素材容量和语义连贯性围绕目标小幅调整，各位明星在单明星成片中的数量也由有效素材决定。整批审核的具体理由中记录实际单明星/多明星数量、占比及偏离目标的素材或叙事依据；调整不得放宽连贯性、复用、镜头或包装门禁。

仍先满足 [单条语义规划](semantic-planning.md) 中的叙事、人物、时长和镜头规则。新计划增加：

- 顶层 `batch_review_policy`：上述固定版本。
- `batch_analysis.sources`：逐项覆盖工作单素材哈希；每项包含 `source_sha256`、当前完整转写的 `source_asr_sha256`、`extraction_status: "exhausted"`、具体 `reason` 和 `candidates`。没有分析完的素材不能标为 exhausted；partial/not_started、缺源、陈旧转写和缺理由均拒绝。
- `candidates`：记录发现的完整话轮，包括 `source_in_frame`、`source_out_frame_exclusive`、精确 `text`、`status`（eligible/conditional/rejected）和具体 `reason`。选取区间必须精确匹配 eligible 单元；conditional/rejected 不能参与容量计算。不得把未查看的片段、孤立安全区或文件数量当作完整候选。
- `spoken_turns`：每个原片另列规范完整话轮，包含原生帧范围、精确 `text`、`complete: true`、布尔 `opening_eligible` 及具体 `reason`。同一原片的话轮区间不能重叠；不能为微移切点或拆句另建话轮。适合作开头的项还须有经画面审核的 `opening_visual_family_id`；需要前文的对白不能标为合格开头。程序核验引文出自绑定的完整ASR；Codex核实话轮完整性及开头适用性。
- 每个 `segments` 增加稳定的 `semantic_cluster_id`，表示实际完整命题。相同命题的不同说法也应同属一类；不得用编号、源文件名或每条新建的 ID 充当语义分类。同一归一化台词不能改用不同分类。
- 每个 `visual_shots` 增加稳定的 `visual_family_id`，按实际场景、机位、服装、景别和连续源镜头分类。同一镜头不同时间段、改候选 ID 或轻微移动切点不创造新片头类别。程序拒绝同一源镜头或重叠源画面改名，Codex 从完整连续帧核实类别和真实变化。

库存示例（实际任务要完整覆盖所有原片和发现的话轮）：

```json
{"source_sha256":"<原片哈希>","source_asr_sha256":"<完整转写文件哈希>","extraction_status":"exhausted","reason":"完整分析人物、场景和前后话轮；列明可用与拒绝项","candidates":[{"source_in_frame":120,"source_out_frame_exclusive":480,"text":"<完整精确口播>","status":"eligible","reason":"完整命题且整个区间仅主角出镜"}],"spoken_turns":[{"source_in_frame":120,"source_out_frame_exclusive":480,"text":"<完整精确口播>","complete":true,"opening_eligible":true,"opening_visual_family_id":"scene-a-camera-1","reason":"完整介绍，不依赖被省略的前文，已核实实际片头机位"}]}
```

## 批次拒绝条件

恢复原 FF 反馈契约中的以下验收上限，适用于当前请求数量，不继承历史成片数量或历史时长：

| 检查 | 上限 |
| --- | --- |
| 相同完整源内容组合、相同整条口播 | 每批一次 |
| 相同候选内容、相同精确台词 | 各最多 6 次 |
| 相同完整开场台词、实际片头画面类别 | 各最多4次，无超限例外 |
| 片头与第二个实际镜头的类别组合 | 最多 2 次 |
| 相同结尾台词 | 最多 4 次 |
| 相同有序语义路线 | 最多 2 次 |
| 两条路线同位置命题重合 | 不超过 40% |
| 两条完整口播三字组相似度（Jaccard） | 不超过 0.88 |
| 只改头尾、相同中段命题路线 | 拒绝 |

源组合身份绑定源 SHA-256 和归一化精确台词；相邻同源台词合并后比较，忽略候选 ID、文件名、音量、字体及微小切点变化。分拆同一话轮、给同一命题改名、换一遍相同台词的素材，都不能冒充不同内容。仍必须通过真实语义衔接检查；差异不能豁免逻辑错误。

开场台词从首个完整规范话轮计数，跨多个相邻同源片段时合并；完全相同台词在不同素材中也合并计数。原声完整话轮、同一连续镜头、拆段、改ID和微移切点均不能创造新开头类别。首个实际画面类别还须与规范话轮的开头画面绑定一致。

请求N条时必须实际提供N条通过检查且各自语义成立的完整方案，至少有 `ceil(N/4)` 种完整开场台词和实际片头类别；40条至少各需要10种。报告 `opening_usage.speech/visual` 列出开场台词或类别、次数、对应成片ID、上限及超限项；`capacity` 区分库存开头容量上界、计划唯一数量和缺口。库存上界不证明存在足够连贯路线。不足时继续分析原片，确实不足则报告缺口，不能把部分批次标为完整交付或突破4次上限。

## 用户明确授权的合理复用

用户明确选择“允许为满足数量增加合理复用，但避免大量重复”时，工作单记录
`batch_reuse_authorization: {"policy":"explicit-user-reasonable-reuse/v1", "authorized_by":"user", "instruction":"用户的实际授权原文"}`。
prepare 将授权绑定进原片索引，计划必须携带相同授权；未授权、缺少原文或授权不一致时不能放宽。
该授权允许候选及精确台词最多16次、前两镜组合最多5次；开场台词及片头类别仍各最多4次，
结尾最多10次、语义路线最多4次，同位置命题重合不超过60%；相同中段最多出现3条，
整条三字组相似度仍不得超过0.88。40条至少各需要10种完整开场台词和实际片头类别。
必须先建立语义成立的完整路线，再检查复用；这些上限不是要求用满。
相同整条源内容组合、整条口播和完整底片画面序列仍每批最多一次，
不能循环复制少量成品、只换字体/BGM或靠改ID满足数量。人物、真实镜头、时长、
切点、字幕和最终成品质检继续执行。未获用户明确授权的任务沿用上表原有严格上限。

## 审核和渲染后复核（绑定当前授权）

`plan-evidence` 在模型和渲染启动前执行整批检查，将自动报告写入绑定计划的 `batch_review_report`。`approve-plan`、渲染、包装、最终取证和 `complete` 均重算或核验此报告；旧单条审批不能批准新批次版本。

每轮规划在 `临时文件/reports/batch_review_round_N.json` 保存诊断，包括超限项和容量缺口；拒绝轮次也保存。v1历史报告仍保持原格式和旧上限，不能用v1报告批准v2任务。

计划与最终 Codex review 顶层声明 `batch_review_policy`，增加 `batch_review`：`report_digest` 是 `batch_review.digest(evidence["batch_review_report"])`，并含 `source_inventory_pass`、`opening_turns_pass`、`semantic_routes_pass`、`visual_families_pass`、`reuse_pass` 全部为true，以及具体 `reason`。必须逐项核实规范话轮和适用开头，结论来自真实原片分析、完整区间画面与整批比较，不得抄通用模板。

逐成片 `outputs` 增加 `opening_visual_family_id`、`second_visual_family_id`、`opening_visual_pass: true`、具体 `opening_visual_reason`；最终还增加整数 `reviewed_opening_frame_count >= 60`。必须查看实际倍速成品的至少前 60 帧，并确认类别与计划一致；完整成片帧审核继续执行。

`clean-qc` 在口播、切点和帧数检查后，对每条未包装底片完整解码，生成 240px 宽、逐帧 MD5 序列与全序列摘要。相同整条画面即使配音、增益、编码文件哈希不同，也拒绝整批。未完整解码、指纹缺帧、证据变化或批次范围缺项均不能进入字幕、包装或交付。`require_clean_qc` 在后续阶段重验此证据和实际输入哈希。

发布时迁移底片授权与指纹对应关系；完成后在 `临时文件/manifests/batch_frames/` 保留小型指纹文件，供新版本字幕重烧沿用底片授权。重新编码的倍速成品仍需新的完整最终审核。原始归档项目保持只读，已有成片不自动返工。

单独诊断计划（返回码 2 表示拒绝，不生成审批）：

```text
assets/dependencies/python/python.exe scripts/autonomous/scripts/batch_review.py --plan <计划JSON> --source-index <原片索引JSON> --report <报告JSON>
```
