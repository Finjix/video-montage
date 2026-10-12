# 纯口播残音清理

新自主任务由 `init` 将 `audio_cleanup_policy: "spoken-audio-cleanup/v1"`
写入工作单和状态。计划根对象及每个片段也必须包含该字段。历史工作单与状态均没有
此字段时保持旧行为，不能用旧审核授权新清理。原声不明、词边界不明时继续补充证据或
返修，不得声称人工听审或强制对齐。

## 取证与规划

`plan-evidence` 在整段选用音频前后各扩展一秒，从原片解码 48 kHz 多声道 PCM，
用最强声道运行随包 Silero VAD，以 20 毫秒窗、10 毫秒步长扫描全部区间。输出包含
`context.wav`、`waveform.png`、能量/VAD 数值、原片上下句逐词转写、选中台词的
`target_words` 和 `candidates`。原片 SHA-256、源帧范围、转写摘要、模型哈希、参数及
证据文件字节全部绑定；相同来源/帧界/台词/增益/策略/转写可复用本轮扫描。

波形有声音或 ASR 没有文字都不构成静音授权。VAD 也可能把上一句残余音节认作人声。
Codex 查看上下文和波形，区分前后句残音、非语音杂音、自然表达、有效口播和不确定
声音；每个疑点都须明确保留或静音。呼吸、笑声、感叹及弱辅音默认保留。
原音首尾能量、突发声和削波记录保留；启用清理时允许先进入音频审核，实际底片仍须
通过原有切点/削波检查以及新的静音复检，不允许以计划审核代替输出检查。

计划阶段带清理标记的片段保持独立，不合并相邻来源区间，使审核、镜头时间轴和实际
渲染分组一致。此类区间不得重叠；需要连续长镜头时直接选用完整连续区间，不能重复
源帧或把一句话随意切成多段。

## 计划审核接口

计划审核顶层增加 `audio_cleanup_policy`；原有每条 `segments` 审核增加
`audio_cleanup`。无疑点的片段也必须确认受保护的逐词范围，`decisions` 可为空。
以下样例的数字仅展示接口，必须替换为实际证据，不能照抄到生产任务。

```json
{
  "policy": "spoken-audio-cleanup/v1",
  "evidence_digest": "<该片段 audio_cleanup.evidence_digest>",
  "natural_expression_preserved": true,
  "protected_speech_ranges": [
    {"word_index": 0, "text": "口播", "start_sample": 24000, "end_sample": 38400}
  ],
  "decisions": [
    {
      "candidate_id": "event-001",
      "classification": "previous_next_speech",
      "action": "mute",
      "start_sample": 4800,
      "end_sample": 9600,
      "reason": "原片上下句及波形显示属于上一句残留，随后低谷与本句分隔"
    }
  ]
}
```

所有样本位置均为解码后 **48 kHz 原片绝对采样位置**，区间 `[start_sample, end_sample)`。
`protected_speech_ranges` 按 `target_words` 顺序逐项提供，`text` 与 `word_index` 必须一致，
范围须在所选源帧对应的音频内。默认采用词范围与片段的交集。需要修正粗略时间戳时，
增加具体 `boundary_reason`，并在波形中提供对应低谷；单次改动不得超过 750 毫秒，
修正范围仍须与原词范围相交，不能把词丢掉。

`classification` 允许 `previous_next_speech`、`non_speech_noise`、`natural_expression`、
`valid_speech`、`uncertain`；只有前两者可 `mute`。其余有效表达须 `retain`，
`uncertain` 不获通过，进入返修。静音范围须包含于对应疑点区间、不重叠、长于
两侧共 10 毫秒过渡，并且与任何受保护的口播区间完全不相交。

`approve-plan` 编译独立、哈希绑定的 `audio_cleanup_authorization`。原始计划不能自行
携带未经审核的 `audio_cleanup` 或 `audio_mute_ranges`。渲染前重新编译审核，拒绝
过期证据、遗漏的疑点、篡改参数和被替换的证据字节。

## 渲染、复检和重烧

渲染器先重采样、按源样本裁切，再将审核区间对所有声道一致静音。每侧 5 毫秒的
渐变位于静音授权区间内部；保持原画面帧数、时长和字幕时间轴，随后沿用增益、包装
混音和单次 1.2 倍速。渲染清单记录完整清理授权和源样本范围。

`clean-qc` 核对渲染实际采用的授权，检查各静音区间内部的所有声道残差不高于
-60 dBFS，并检查边界突变。AAC/倍速处理会扩散边界，因此复检避开渐变及约一个
AAC 包的余量，短区间按可用长度缩小余量；没有可检查内部的区间不获通过。`final-evidence` 按逐片段
速度和最终 1.2 倍速映射区间，重建同一清理底轨与授权 BGM 的混音，区间混音残差
须不高于 -45 dBFS，背景音乐本身无需静音。原有 ASR、画面、字幕及完整混音检查保留。

完成后在 `manifests/audio_cleanup_authorization.json` 保留最小清理区间、保护范围和
来源证明，并绑定保留的计划与纯净底片清单。字幕重烧只复用这些已检查的底轨；新的
最终输出仍须重新检查。清理方案变动需要重新审核和渲染，不能沿用旧完成回执。
