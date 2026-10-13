# 语义优先混剪（semantic-continuity/v2）

新任务及完整剪辑返修恢复原始 FF v20.2.5 的分析顺序：先阅读原片完整转写、人物、场景与动作上下文，确认完整话轮、命题和语境依赖，再决定相邻话轮和整条叙事。不得仅按相同游戏名、关键词或降重目标拼接。取消 `diversify-plan`，不要求新任务生成多样化报告或填写 `diversity_reason`；合理复用合格素材。

绑定 `codex-speech-review/v1` 的任务先执行[源台词审校](speech-review.md)，保留原始 ASR，
用审核后的完整文字和词时间确认话轮与原生帧范围；识别草稿的错字或幻觉不能成为叙事依据。
源范围内完整口播必须与计划精确一致，候选和实际渲染镜头仍须分别审校。

同时执行[整批审核](batch-review.md)，新任务及完整返修固定 `semantic-batch-review/v2`。合理复用须满足整批组合唯一性、片头/前两镜和内容复用检查；同一开场台词和实际片头画面类别每批各最多4次，不设超限例外。语义成立在先，批次验收在后，不恢复会打乱语境的选片排序步骤。

## 时长、镜头与人物

- 底片按原速选取，60 fps 渲染后须为 **1296–2160 帧（21.6–36 秒）**；最后统一 1.2 倍速，实际交付须为 **1080–1800 帧（18–30 秒）**。计划与渲染共用同源区间合并和帧数舍入规则，编码后核验实测帧数。21.5 秒底片无法保证加速后达到 18 秒，不通过。
- 每条至少四个实际视觉镜头，不限制最多镜头数。原片内部真实机位、角度或景别变化可计数；连续镜头拆分台词、ID 或范围不能重复计数。镜头表完整覆盖合并后的渲染时间轴，不能有空隙或重复覆盖。最长与最短镜头相差不超过 3 秒；不足 0.8 秒的闪帧、微镜头不计为有效镜头。
- 绑定[多明星策略](selected-celebrities.md)的任务每条可选择一位或多位已登记明星；v3允许群演在所选明星旁边陪衬同框，但所选明星必须是画面主体，不能出现群演独立占镜或成为主体的区间。实际人物集合必须与所选明星及群演声明一致，逐片段、逐镜及整片检查主体关系。历史人物策略保持原合同。允许语义相关的画外问话、对白；说话者与可见人物分开记录。
- 时长不足只能补完整且语义相连的素材；超长须重新组片。禁止停帧、慢放、重复源帧、截半句话或无关内容凑数。

## 计划接口

沿用 `video-montage-autonomous-plan/v260929` schema，顶层必须声明 `planning_policy: "semantic-continuity/v2"`。新 `init` 在工作单快照和状态中固化同一版本；完整 `repair` 升级规划规则、撤销原计划及下游审批。历史完成任务和单纯 `reburn` 保留原契约。

程序按任务绑定版本派发：历史 `semantic-continuity/v1`、历史批次v1及未绑定策略的旧任务仍按原契约运行，不自动升级；v2不能删除标记、改用v1计划或旧审核通过。工作单、状态、原片索引、计划、证据和保留的重烧上下文须保持对应版本与哈希一致。

逐成片字段：

- 多明星策略使用 `protagonist_ids`、`cast_policy`、逐段 `speaker_id` 和人物审核字段，详见[接口合同](selected-celebrities.md)；以下 `protagonist_id` 与单主角审核字段仅适用于历史未绑定人物策略的任务。不得按文件名、服装猜身份。
- `segments`：保留源 SHA-256、原生帧界、精确原声 `text`，`speed: 1`；增加完整 `visible_person_ids` 与 `purpose_contract`。v3有群演时必须填写 `primary_person_ids`，接口和逐层主体审核见[人物合同](selected-celebrities.md)；历史单主角字段保留原规则。
- `purpose_contract`：非空 `function`、本条内不重复的非空 `new_claim_ids`、`non_redundant: true`、非负整数 `narrative_stage`。最后一段增加 `closing_payoff: true` 和出自原声的 `closing_quote`，使用结果、收益、证明、剧情收束或相关 CTA 等有用结尾功能。
- 结果先行的 `hook` 如需返回机制，首个 `hook_response` 要引用后段原声中的 `content_evidence.how_why_quote`，包含如何、为什么或因为等明确桥接。
- `scenario_bound: true` 标注不能任意碎片化的场景依赖表演；承接此类素材时，转场 `content_evidence.scenario_continuity` 说明真实场景连续性。
- `transitions`：按片段顺序恰好 N−1 条；每条包含真实 `relation`、非空 `information_gain`、`content_evidence`。后者要求 `from_quote`、`to_quote` 分别出自相邻台词，以及具体 `new_information`、`entity_flow`、`state_flow`，不得用模板充当关系证明。

支持关系：`hook_response`、`question_answer`、`answer`、`explanation`、`problem_solution`、`claim_support`、`cause_effect`、`condition_result`、`progression`、`proof`、`benefit`、`payoff`、`contrast`、`counterpoint`、`escalation`、`punchline`、`speaker_experience`、`story_resolution`。连接词仍须有原声前提，关系标签本身不能证明连贯。

### 保留口播中的前提和桥接

每条 `content_evidence` 增加非空 `premise_refs`，每项为 `{"segment_index":1,"quote":"<实际保留的前提台词>"}`。片段索引从1开始，只能引用当前转场左侧或更早的已保留片段，不能引用未来台词、未剪入成片的原文或审核者的补充说明。引文至少包含4个归一化字符，应引用完整前提；产品名和关键词本身不能证明逻辑关系。

跨主题需要桥接时增加 `bridge_ref`，格式相同，只能引用当前相邻两段的保留原声。入坑经历、刷广告或玩家规模宣传不能直接接“只有砍树才能获取资源”等具体机制。须保留真实的玩法介绍或相关问题，例如“这个游戏开局先让幸存者砍树”；审核者写“从入坑经历转向木材用途”、换 `explanation` 标签或补写字幕都不能补上口播缺失的前提。程序检查这类明确跳跃，Codex仍须逐项判断其他主题转换、主体与因果关系。

`entity_flow` 解释前后实体和指代如何承接，`state_flow` 解释行动、条件与结果如何推进，两者必须具体且不能复制同一句说明。缺少桥接时重选素材、加入原片中真实桥接话轮或重组路线；不能拼半句话或为了降重强接台词。

转场示例（其余计划字段仍须完整）：

```json
{"relation":"cause_effect","information_gain":"温度提升吸引更多幸存者","content_evidence":{"from_quote":"把取得的资源用于升级熔炉，提升营地温度","to_quote":"温度提升以后才能吸引更多的幸存者","new_information":"更高温度使营地能够吸引更多居民","entity_flow":"前段的营地温度在后段继续作为幸存者到来的条件","state_flow":"升级熔炉提升温度后，幸存者数量随之增加","premise_refs":[{"segment_index":1,"quote":"把取得的资源用于升级熔炉，提升营地温度"}]}}
```

`visual_shots` 按顺序标注每个实际镜头：

```json
{"render_segment_index":1,"source_sha256":"<原片 SHA-256>","source_in_frame":0,"source_out_frame_exclusive":360,"source_visual_shot_id":"scene-a-camera-1","visible_person_ids":["person:lead"],"boundary_kind":"opening"}
```

`render_segment_index` 从 1 开始，指合并后的渲染片段，不是台词片段索引。首镜 `boundary_kind` 为 `opening`；新渲染片段首镜为 `edit_cut`；同一渲染片段内部真实换镜为 `source_camera_change`。连续原片镜头用稳定的 `source_visual_shot_id`，不能给拆分后的同一镜头换 ID。程序检查血缘、覆盖、时长与数量，Codex 看连续帧确认换镜真实性。

## 证据与审核

候选 `continuous_visual` 包含全部选取原生帧的序号、960 像素宽原图、SHA-256、源身份与半开帧区间。16 帧导航图只供索引，判断以原图为准；不清楚时不能批准。相同源哈希和帧范围复用证据；缺帧、文件或血缘变化均拒绝。原有首尾切点证据继续检查残音、原片转场及连续跳镜。

计划审核保留 schema、角色、计划和证据哈希，增加 `planning_policy`，并提交：

- `segments`：原有审核字段之外增加 `protagonist_only_pass`、精确 `visible_person_ids`、`continuous_visual_sha256`、具体 `protagonist_reason`，覆盖整个区间。
- `transitions`：逐项填写 `plan_id`、从1开始的 `transition_index`、`continuity_pass: true`、`relation_supported: true`、`missing_premise: false` 和具体 `reason`，检查原声是否支持关系、连接词前提、指代、信息推进、语气和动作连续性。不能仅写“同一游戏同一人物”等通用理由。
- `visual_shots`：逐镜填写 `plan_id`、`shot_index`、`source_visual_shot_id`、`shot_pass`、`protagonist_only_pass`、`visible_person_ids`、`reason`、`boundary_reason`。
- `outputs`：逐成片填写 `plan_id`、`protagonist_id`、整数 `visual_shot_count`、`semantic_pass`、`protagonist_only_pass`、`visual_shots_pass`，及非空 `semantic_reason`、`protagonist_reason`、`shot_reason`。v2还必须有 `narrative_pass: true` 和具体 `narrative_reason`，说明整条主线、主体承接和因果顺序。

程序只能核验结构、台词引用、帧时间轴、信号和哈希。Codex 必须实际分析完整语境与成片、检查连续画面，不能把布尔值或关键词匹配当作语义证明。原始机制到结果桥接、第一人称主体承接、同义结果去重、具体福利与结尾规则继续执行。

最终取证覆盖倍速输出全部帧，记录实际帧数及镜头到最终时间轴的映射。最终审核增加与计划 `outputs` 相同的叙事、主角、镜头字段及 `continuous_visual_sha256`，并保留 `output_sha256` 和原有画面、字幕、包装检查。缺项或失效不得发布。

v2最终证据的每条 `results[].transitions` 列出全部转场、保留的前提与桥接，以及绑定实际倍速输出、ASR和原声保留证据的 `speech_evidence_digest`。最终审核顶层必须重新提交全部 `transitions`，保留计划审核的关系与前提字段，并逐项填写该条实际成品的 `output_sha256` 及证据中的 `speech_evidence_digest`。缺项、重复、旧输出或旧口播摘要均拒绝。计划通过不替代实际成片审核；无包装输出和字幕重烧同样执行。

完成后保留绑定已验证底片、计划与规则版本的 `editing_context`，供字幕重烧复用底片授权；不宣称已删除的源证据仍在。字幕重烧仍须重新生成全帧最终证据与审核，完整剪辑返修清除上下文后重新分析取证。旧多样化报告只保留历史读取与哈希核验。
