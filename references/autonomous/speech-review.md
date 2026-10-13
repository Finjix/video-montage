# Codex 台词审校（codex-speech-review/v1）

ASR 是定位和审校草稿，不是真实台词的最终裁决。Codex 在同一任务中检查原片完整语境、
原始转写、实际音频范围、画面及已核实素材文字，确认台词并修正误字、漏识别、多识别、
幻觉和时间码。不能把广告文字替换成未说出的台词，也不能为满足数量猜测含混原声。
不能声称人工听审、强制对齐或独立代理审核。程序检查审核范围、时间、原文件和全部哈希；
台词判断由 Codex 负责，程序通过不等于机器已经证明听感正确。

新 `init` 默认绑定本策略到工作单、状态、源索引和计划。历史任务及其字幕重烧保留已绑定
契约；现有 `semantic-continuity/v2` 自主任务可在规划前执行 `source-speech-evidence`
显式启用。已有计划先使用 `repair` 作废下游批准，不转换旧执行器任务。

## 原片

1. `prepare` 保存原始 ASR，空识别也保留；`asset-copy` 核实素材文字。
2. `source-speech-evidence --job-dir <目录>` 提取每条原片完整单声道 16k PCM WAV，保存
   原片 SHA-256、原始 ASR 文件哈希、原生帧数/FPS 和音频哈希，返回审校证据路径。
3. Codex 阅读实际证据，填写以下格式；`source-speech-review --job-dir <目录> --review <JSON>`
   核验后保存独立的 `reviewed_speech`。不覆盖原 ASR，不伪装成模型识别结果。
4. 规划依据审校后的完整话轮和时间。计划根对象声明 `speech_review_policy`；
   `batch_analysis.sources[]` 同时绑定原 `source_asr_sha256` 和 `source_speech_sha256`
   （源索引 `reviewed_speech.sha256`）。片段的全部台词必须对应其原生帧范围内的完整词，
   不能引用另一处相同台词，不能裁断词或省略范围内口播。

审核顶层：

```json
{
  "schema": "video-montage-codex-speech-review/v1",
  "stage": "source",
  "reviewer_role": "codex",
  "evidence_sha256": "<source-speech-evidence 返回文件的 SHA-256>",
  "asset_copy_sha256": "<已核实素材文案审核文件 SHA-256>",
  "results": [
    {
      "source_sha256": "<原片 SHA-256>",
      "audio_sha256": "<实际证据 WAV SHA-256>",
      "observed_text": "欢迎无尽东日",
      "speech_pass": true,
      "reason": "<完整语境核验及识别差异的具体结论>",
      "timing_reason": "<逐词时间审核依据及调整说明>",
      "reviewed_asr": {
        "text": "欢迎无尽冬日",
        "segments": [
          {"text": "欢迎无尽冬日", "start": 0.1, "end": 1.8,
           "words": [{"word": "欢迎", "start": 0.1, "end": 0.5},
                     {"word": "无尽冬日", "start": 0.5, "end": 1.8}]}
        ]
      },
      "corrections": [
        {"before_start": 4, "before_end": 5, "after_start": 4, "after_end": 5,
         "before": "东", "after": "冬", "start_ms": 500, "end_ms": 1800,
         "reason": "<结合原片语境及游戏名称文字核实同音字>",
         "evidence": [{"kind": "asset_copy", "sha256": "<实际图片哈希>", "text": "无尽冬日"}]}
      ]
    }
  ]
}
```

`results` 必须覆盖所有原片且每条只出现一次。`reviewed_asr` 使用音频局部秒数，完整文字、
分段文字、逐词文字归一化后完全对应；每词起止必须递增、落在对应分段和实际音频内。
不是强制对齐，时间仍须 Codex 审核。原声确实没有口播时可用空文字和空分段，删除识别
幻觉仍须逐项记录。没有充分依据的原片不得批准。

`corrections` 必须逐项覆盖归一化文本的所有改动，使用 Python
`speech_review.changes(before, after, autonomous_montage.normalized)` 提取确定的前后字符
索引（从零开始，结束排他，`SequenceMatcher(..., autojunk=False)`）。这只是差异定位，
不会生成理由或批准。插入或删除一侧为空，记录具体音频毫秒范围、理由和依据。
不是全局同音词白名单，也不按相似度自动放行；未改字填写空列表。

依据可以是实际 `source_asr`、`source_speech` 或 `asset_copy` 的哈希与原文引用；
`source_speech` 仅用于候选/底片核验。空识别或无法用文字引用的幻觉，可使用
`{"kind":"source_audio","sha256":"<本次 WAV 哈希>","start_ms":100,"end_ms":1800}`，
范围须与该条修正相同。这记录 Codex 对实际音频范围的判断，不宣称程序验证了字词。
每条修正仍须具体理由和完整上下文审校，不能只凭预期广告文案填字。

## 候选片段

`plan-evidence` 对每个实际裁切 PCM 做一次中性识别，保留原结果和 `asr_exact`，不靠
反复变速识别把模型输出凑成预期文本。源台词、原生裁切、PCM 和画面技术检查仍执行。
识别是否相等都不能代替审校：合格候选标记 `pending_speech_review`，进入计划审核。
技术失败仍拒绝，不能用台词审核覆盖削波、坏切点或画面缺陷。

`approve-plan` 的原有逐片段 `segments[]` 每项增加 `speech_review`，其字段与上述原片
finding 相同（不需 `source_sha256`），`audio_sha256` 绑定候选 PCM，时间使用候选局部
时间，`observed_text` 必须逐字等于实际候选 ASR。计划审核顶层也声明本策略。
审校后文字必须精确等于对应源范围的计划台词；若真实台词与计划不同，修复计划后重新
取证，不能在批准阶段替换叙事内容。渲染和最终交付重新核验这些审核与依赖。

## 底片及字幕

`clean-qc` 保留整片识别和每个真实渲染镜头的局部识别，执行音频、切点、时长、画面
重复和音量检查。技术合格后进入 `clean_speech_pending`，返回独立逐镜审校证据；
字幕和包装此时不能开始。

填写同一审核顶层格式，将 `stage` 改为 `clean`，证据哈希绑定返回的底片台词证据。
`results` 改为 `[{"plan_id":"P1","shots":[{"shot_index":1,...finding字段}]}]`。
全部成片、全部实际渲染镜头必须各审一次；WAV 哈希、原始识别和镜头输出帧范围均绑定。
逐词时间使用镜头局部时间，不能跨镜，审校文字必须等于该镜保留的源台词。
执行 `clean-speech-review --job-dir <目录> --review <JSON>` 后，程序保留 raw ASR 并
将审校后的词时间映射到底片时间轴，生成新的通过 QC，才允许 `subtitle-draft`。
审核文件有误被拒绝时，可修正审核文件并针对同一有效证据重新提交；不必为了改审核记录
重跑识别。若原片、音频或计划改变，则先返修并重新取证。

字幕审核继续核对全覆盖、分句、单行字数、镜头边界及哈希，可引用 `source_speech`
作为文字依据。最终混音的原始识别差异继续保留；已有逐采样混音保真证明及各项最终检查
仍独立执行。完成清理须保留源审校、底片逐镜审校及原 ASR、WAV、上下文依赖，重烧时
重新校验，不复用已丢失证据的批准。
