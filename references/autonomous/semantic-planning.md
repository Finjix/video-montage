# 语义优先混剪（semantic-continuity/v1）

新任务及完整剪辑返修恢复原始 FF v20.2.5 的分析顺序：先阅读原片完整转写、人物、场景与动作上下文，确认完整话轮、命题和语境依赖，再决定相邻话轮和整条叙事。不得仅按相同游戏名、关键词或降重目标拼接。取消 `diversify-plan`，不要求新任务生成多样化报告或填写 `diversity_reason`；合理复用合格素材。

## 时长、镜头与人物

- 底片按原速选取，60 fps 渲染后须为 **1296–2160 帧（21.6–36 秒）**；最后统一 1.2 倍速，实际交付须为 **1080–1800 帧（18–30 秒）**。计划与渲染共用同源区间合并和帧数舍入规则，编码后核验实测帧数。21.5 秒底片无法保证加速后达到 18 秒，不通过。
- 每条至少四个实际视觉镜头，不限制最多镜头数。原片内部真实机位、角度或景别变化可计数；连续镜头拆分台词、ID 或范围不能重复计数。镜头表完整覆盖合并后的渲染时间轴，不能有空隙或重复覆盖。最长与最短镜头相差不超过 3 秒；不足 0.8 秒的闪帧、微镜头不计为有效镜头。
- 每条指定一个经实际画面确认的 `person:<stable-id>` 主角，整批可以制作不同主角。每个选取区间只能有该主角可见，禁止其他人物同框、单独出镜或身份不明。允许语义相关的画外问话、对白；说话者与可见人物分开记录。
- 时长不足只能补完整且语义相连的素材；超长须重新组片。禁止停帧、慢放、重复源帧、截半句话或无关内容凑数。

## 计划接口

沿用 `video-montage-autonomous-plan/v260929` schema，顶层必须声明 `planning_policy: "semantic-continuity/v1"`。新 `init` 在工作单快照和状态中固化同一版本；完整 `repair` 升级规划规则、撤销原计划及下游审批。历史完成任务和单纯 `reburn` 保留原契约。

逐成片字段：

- `protagonist_id`：规范主角 ID，不得按文件名、服装猜身份。
- `segments`：保留源 SHA-256、原生帧界、精确原声 `text`，`speed: 1`；增加 `visible_person_ids: [protagonist_id]` 与 `purpose_contract`。
- `purpose_contract`：非空 `function`、本条内不重复的非空 `new_claim_ids`、`non_redundant: true`、非负整数 `narrative_stage`。最后一段增加 `closing_payoff: true` 和出自原声的 `closing_quote`，使用结果、收益、证明、剧情收束或相关 CTA 等有用结尾功能。
- 结果先行的 `hook` 如需返回机制，首个 `hook_response` 要引用后段原声中的 `content_evidence.how_why_quote`，包含如何、为什么或因为等明确桥接。
- `scenario_bound: true` 标注不能任意碎片化的场景依赖表演；承接此类素材时，转场 `content_evidence.scenario_continuity` 说明真实场景连续性。
- `transitions`：按片段顺序恰好 N−1 条；每条包含真实 `relation`、非空 `information_gain`、`content_evidence`。后者要求 `from_quote`、`to_quote` 分别出自相邻台词，以及具体 `new_information`、`entity_flow`、`state_flow`，不得用模板充当关系证明。

支持关系：`hook_response`、`question_answer`、`answer`、`explanation`、`problem_solution`、`claim_support`、`cause_effect`、`condition_result`、`progression`、`proof`、`benefit`、`payoff`、`contrast`、`counterpoint`、`escalation`、`punchline`、`speaker_experience`、`story_resolution`。连接词仍须有原声前提，关系标签本身不能证明连贯。

`visual_shots` 按顺序标注每个实际镜头：

```json
{"render_segment_index":1,"source_sha256":"<原片 SHA-256>","source_in_frame":0,"source_out_frame_exclusive":360,"source_visual_shot_id":"scene-a-camera-1","visible_person_ids":["person:lead"],"boundary_kind":"opening"}
```

`render_segment_index` 从 1 开始，指合并后的渲染片段，不是台词片段索引。首镜 `boundary_kind` 为 `opening`；新渲染片段首镜为 `edit_cut`；同一渲染片段内部真实换镜为 `source_camera_change`。连续原片镜头用稳定的 `source_visual_shot_id`，不能给拆分后的同一镜头换 ID。程序检查血缘、覆盖、时长与数量，Codex 看连续帧确认换镜真实性。

## 证据与审核

候选 `continuous_visual` 包含全部选取原生帧的序号、960 像素宽原图、SHA-256、源身份与半开帧区间。16 帧导航图只供索引，判断以原图为准；不清楚时不能批准。相同源哈希和帧范围复用证据；缺帧、文件或血缘变化均拒绝。原有首尾切点证据继续检查残音、原片转场及连续跳镜。

计划审核保留 schema、角色、计划和证据哈希，增加 `planning_policy`，并提交：

- `segments`：原有审核字段之外增加 `protagonist_only_pass`、精确 `visible_person_ids`、`continuous_visual_sha256`、具体 `protagonist_reason`，覆盖整个区间。
- `transitions`：逐项填写 `plan_id`、从 1 开始的 `transition_index`、`continuity_pass`、具体 `reason`，检查连接词前提、指代、信息推进、语气和动作连续性。
- `visual_shots`：逐镜填写 `plan_id`、`shot_index`、`source_visual_shot_id`、`shot_pass`、`protagonist_only_pass`、`visible_person_ids`、`reason`、`boundary_reason`。
- `outputs`：逐成片填写 `plan_id`、`protagonist_id`、整数 `visual_shot_count`、`semantic_pass`、`protagonist_only_pass`、`visual_shots_pass`，及非空 `semantic_reason`、`protagonist_reason`、`shot_reason`。

程序只能核验结构、台词引用、帧时间轴、信号和哈希。Codex 必须实际分析完整语境与成片、检查连续画面，不能把布尔值或关键词匹配当作语义证明。原始机制到结果桥接、第一人称主体承接、同义结果去重、具体福利与结尾规则继续执行。

最终取证覆盖倍速输出全部帧，记录实际帧数及镜头到最终时间轴的映射。最终审核增加与计划 `outputs` 相同的叙事、主角、镜头字段及 `continuous_visual_sha256`，并保留 `output_sha256` 和原有画面、字幕、包装检查。缺项或失效不得发布。

完成后保留绑定已验证底片、计划与规则版本的 `editing_context`，供字幕重烧复用底片授权；不宣称已删除的源证据仍在。字幕重烧仍须重新生成全帧最终证据与审核，完整剪辑返修清除上下文后重新分析取证。旧多样化报告只保留历史读取与哈希核验。
