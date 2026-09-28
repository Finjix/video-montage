# V15内容落地语义门禁

V14 继承 V13 已验证的 Windows 安全前台租约、watchdog 和原生帧证据阶段：`SOURCE_ASR → CANDIDATE_PROPOSAL → SOURCE_FRAME_EVIDENCE → CANDIDATE_BUILD`。候选提案不是已批准候选；只有 FFmpeg 精确解码得到的 in/mid/out 帧、源哈希和解码 PTS 均存在时，才能进入正式候选构建。WPF 截帧一律不能作为原生证据。

前台执行器写入 `${task_root}/.v14/front_status.json`。界面可据 `started_at` 连续显示已耗时，并读取阶段、进度和租约；状态读取绝不接管或取消后台工作。心跳继续只作 watchdog，且只能在前台租约和任务进度同时过期后恢复。

V14 新增冻结候选池：只有来源 SHA-256、处理阶段、风格档案和全部证据哈希都不变时，才复用候选、身份和边界证据。`v14_candidate_pool.py plan` 仅把新增、变更或未审计来源写入增量来源清单，避免重复 ASR、取帧和身份审核。

真人内容仍排除全屏游戏、后续游戏解说和尾板；V19允许明星真人画面中的来源口播CTA、点击即玩与礼包福利。时长与产量只读取当前工单，不继承历史批次；未明确时保持自然语速和自然内容长度，禁止慢放、停帧或 filler 凑时长。V19同时保留组合容量、画面语义、全程明星在镜、边界帧/音频窗口、复用污染全局失效、镜头时长差和默认禁用so easy门禁。全部指定编号进入 `semantic-delivery-manifest/v19` 才视为完成。
