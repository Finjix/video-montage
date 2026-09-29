# 无人值守混剪流程（新任务，v260929）

入口：`dependencies/python/python.exe components/autonomous/scripts/autonomous_montage.py`。这是后续任务的新契约；已有 `three_suite_ff_state.json`、v260928 审核文件和正在运行的 D 盘任务继续使用原状态机，绝不转换成新版通过回执。

Codex 在同一个任务中完成语义与画面判断，不委派独立代理，也不要求人听音频。它阅读原片及候选片段转写、逐帧画面和 PCM 报告，再提交 `reviewer_role: "codex"` 的审核文件。程序独立核验源文件、帧范围、转写、音频指标、字幕、包装版成片和所有哈希。词时间戳只作为定位线索；本流程不声称强制对齐或人工听审。证据不充分时换片或停机，最多三轮。每轮输出独立目录，失败件不覆盖。

## 阶段

1. `init --job-dir <新目录> --work-order <JSON>`，然后 `prepare --job-dir <目录>`。工作单使用 `video-montage-autonomous-work-order/v260929`，包含 `requested_outputs`、`sources: [{"path": "原片绝对路径", "sha256": "可选校验值"}]`、`asset_root` 和 D 盘的 `output_root`。`prepare` 对每条原片重新转写，记录原生帧率、源哈希、说明文字和图片哈希。
2. Codex 查看素材图片并提交 `video-montage-codex-asset-copy/v260929`：`reviewer_role: "codex"`、`sources_sha256` 指向 `asset_copy_sources.json`，`assets` 对每个素材哈希给出可见文字。说明文件必须逐字保留；看不清的图片填空，不能猜测。
3. Codex 依据原片转写与画面提交 `video-montage-autonomous-plan/v260929`，其中 `outputs` 每条含 `plan_id` 和源帧范围 `segments`。依次运行 `plan-evidence --plan <JSON>`、`approve-plan --review <JSON>`。后者的 `video-montage-codex-review/v260929` 需声明 `stage: "plan"`、`reviewer_role: "codex"`、`evidence_sha256`、`plan_sha256`，并逐片段给出 `semantic_pass`、`visual_pass`、`entry_visual_pass`、`exit_visual_pass`、具体 `reason` 和 `boundary_reason`。机器另查完整转写、首尾 40 毫秒低能量、削波、孤立突发声和逐帧证据；比较片段首尾各 13 帧，并扫描首尾半秒内是否藏有原片转场。Codex 必须看首尾连续 30 帧及相邻片段交界，比较人物位置、景别和画面尺寸；成片后逐帧复查每个拼接点，避免短时间内连续跳镜。不合格片段不可批准。
4. `render-clean` 用原生整数帧范围渲染 1440×2560、60 fps 的纯净成片；`clean-qc` 复转写并检查口播、削波和每处切点前后 40 毫秒的残音与突发声。`subtitle-draft` 生成草稿及不可变快照。Codex 提交 `video-montage-codex-subtitle-review/v260929`，其中 `draft_sha256`、`asset_copy_sha256`、每条字幕的原文、新文、原时间码和更改依据都完整绑定；运行 `subtitle-review` 后才允许烧录。改字必须援引实际源转写、计划口播或素材文案的哈希和文字；全片字幕归一化后必须与原片口播一致，不能照抄与口播相异的广告文案。字幕不能跨越对应口播的渲染镜头边界，ASR 词时间戳偏早时以镜头边界为准。
5. `package --config <JSON>` 使用已纠错的 `subtitle-<plan_id>.txt` 及其哈希，按现有包装配置叠加铭牌、文字钉、免责和 BGM。包装版清单标记为 `complete_autonomous`，并绑定纯净成片及自动 QC。`final-evidence` 对包装版再次转写，检查削波、切点 PCM 和口播对 BGM 的声级差；抓取每条字幕中间帧与起止前后帧、每处切点前后 72 帧、片头片尾及图层起止帧。Codex 检查这些画面并提交 `stage: "final"` 的逐成片审核。`complete --review <JSON>` 重新执行包装器技术与自动审核校验，全部通过才写 `video_montage_autonomous_completion.json`。

所有命令都带 `--job-dir`。某阶段拒绝后记录 `reports/repair_round_N.json`；Codex 可以在新一轮重选片段，或在纯净成片已通过时重做包装。第三次仍无法确认即停机。`status` 可读取当前阶段和失败原因。最终交付位置在工作单 `output_root/attempt-NN/`，包含 MP4、字幕、配置和清单。

## 审核文件示例

计划审核：

```json
{"schema":"video-montage-codex-review/v260929","stage":"plan","reviewer_role":"codex","evidence_sha256":"<plan_evidence SHA-256>","plan_sha256":"<plan SHA-256>","segments":[{"plan_id":"demo-01","segment_index":1,"semantic_pass":true,"visual_pass":true,"entry_visual_pass":true,"exit_visual_pass":true,"reason":"完整一句，人物和画面含义一致","boundary_reason":"已查看原生首尾连续帧，画面稳定且没有残留转场"}]}
```

字幕纠错的每条 `cues` 必须给出 `index`、原草稿的 `draft_start_ms`、`draft_end_ms`、`before`，以及纠错后的 `start_ms`、`end_ms`、`after`；新时间要与成片 ASR 的词时间戳相符。改字时还需 `evidence: [{"kind":"asset_copy|source_asr|plan_speech","sha256":"<对应哈希>","text":"<原证据中的字词>"}]`。未改字可留空依据。最终审核的 `outputs` 每条给出 `plan_id`、`output_sha256`、`visual_pass`、`subtitle_pass`、`overlay_pass` 和具体 `reason`。

失败回执不会生成“听过原声”“强制对齐”或“独立审核者已批准”的声明。ASR 和信号分析无法消除所有主观听感风险，故含混切点与无法确认的同音字按失败处理。
