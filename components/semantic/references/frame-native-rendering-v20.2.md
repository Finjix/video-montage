# V20.2 帧级剪辑契约

最终画面裁切只认解码帧序号。每个计划镜头必须提供：

- `source_in_frame`：包含的首帧；
- `speech_end_frame`：完整语音结束帧；
- `source_out_frame_exclusive`：不包含的尾帧；
- `source_fps_num/source_fps_den`：源帧率有理数；
- `source_path` 与可选的 `source_sha256`。

`source_in/source_out` 秒数可以作为 ASR 或展示信息存在，但不能驱动 FFmpeg。缺少帧字段时，`v20_frame_plan_gate.py`、`v9_batch_render.py` 和 `portable_frame_renderer.py` 都必须失败，禁止回退到时间裁切。

候选边界证据也必须使用这些帧号：窗口中的每张图记录真实 `source_frame_number`，PCM起止由帧号换算成采样点。禁止用 `-ss` 近似寻址生成边界证据。生成的bundle永久保持`pending_review`，独立通过写入另一份哈希绑定回执。

每个入点后及出点前必须至少连续稳定30帧；不足30帧就出现新的镜头/机位，属于短头尾镜头并拒绝。13帧、16帧或29帧都不能因为来自原片、持续超过一帧或紧靠正式切点而放行。成片首帧、尾帧及所有拼接点使用同一规则。

用户判坏的素材区间必须按`源SHA-256/内容指纹 + 原始帧区间`追加到失效区间注册表。任何后续任务都要扫描完整批次的全部计划和全部候选依赖；“本次没改的成片”、改名候选、旧通过回执及修复底片名称都不是豁免。

## 正常渲染

```powershell
python scripts/portable_frame_renderer.py --plan plan.json --output out.mp4 --evidence render.json
```

画面使用 `select(n)` 选择源帧；音频起止只能由 `帧号 × fps_den / fps_num` 推导。禁止 `tpad`、冻结帧、插值帧和按秒输入裁切。

相邻计划段若来自同一源文件、帧率与速度一致、源时间单调向前，且帧区间重叠或首尾相接，渲染器必须先合并为一个 `[最早入帧, 最晚出帧)` 原片区间，再一次性处理画面和音频。禁止分别裁切后 concat；否则公共尾音会被重复，或相接帧会被重复/丢失。倒序取段、速度不同或中间存在未选择帧的组合不得自动合并。

## 成片夹帧修复

删除清单必须使用 60fps 包含端点帧区间：

```json
{"schema":"frame-delete-registry/v260928","fps":60,"ranges":[[758,759],[1013,1032]],"protected_spoken_ranges_60fps_inclusive":[[0,740],[1033,1400]],"spoken_alignment_evidence":{"path":"alignment.json","sha256":"REPLACE_64_HEX_SHA256"}}
```

执行：

```powershell
python scripts/frame_range_repair.py --input WZ-02.mp4 --output WZ-02-clean.mp4 --delete-registry delete.json --evidence repair.json
```

工具同步删除由同一帧边界推导出的音频采样。删除后时长不合格时应重新选择更长的干净源帧，不得用局部停帧补足。

删帧清单必须绑定当前输出的语音/音素对齐证据，并列出所有有效语音帧保护区。缺失证据、证据哈希不符、尾字未完整，或删除区与保护区有任何交集，都必须拒绝。画面问题如果发生在有效台词期间，应从原片重新选取完整连续帧段，不能通过同步删掉台词来“修画面”。

## 外部包运行时

`portable_frame_renderer.py` 与 `winky_ledger.py` 都随安装包交付。安装器检查必需运行文件，剪辑任务的计划与审核证据继续使用 SHA-256 绑定。
