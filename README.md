# video-montage

版本：`0.0.1`。中文口播混剪通用智能体技能（Agent Skill），可在 Codex、Claude Code 等支持 SKILL.md 的环境中使用。

## 目录与发布

- `skill/`：完整技能，包含 `SKILL.md`、`agents/`、`scripts/`、`references/` 和 `assets/`（含运行依赖与模型）。
  - `scripts/video_montage.py`：唯一入口（`<command> --job-dir ...`）；`scripts/verify_runtime.py`：运行时自检。
  - `scripts/montage/`：核心包。`workflow` 状态机与命令，`planning`/`batch`/`cast`/`speech`/`text_library` 等审阅策略；`media/` 为 ASR、裁剪渲染、音频清理与响度；`packaging/` 为字幕包装与设计渲染。
  - `references/`：按阶段查阅的说明文档；`assets/`：文本库、无效区间、包装素材与 `dependencies/` 运行依赖。
- `dev/`：本地开发专用目录。

成品与临时文件默认输出到当前用户桌面的 `自动化混剪_YYYYMMDD_HHMMSS/`（可在工单中用 `output_root` 指定其他目录）。

发布时直接将整个 `skill` 文件夹压缩为 ZIP，再按需改名。

测试：`skill/assets/dependencies/python/python.exe dev/run_tests.py [--group montage|media|packaging]`。新增开发工具和测试统一放入 `dev/`，不要在仓库根目录或 `skill/` 中建立开发工具、测试或临时产物目录。技能运行所需的脚本、资源和依赖保留在 `skill/`。

## 依赖版本

运行平台：64 位 Windows。

| 依赖 | 版本 |
|---|---|
| Python | 3.13.15 |
| FFmpeg / FFprobe | 9.0.2 |
| PyTorch | 2.11.0+cu128 |
| Transformers | 4.57.6 |
| qwen-asr | 0.0.6 |
| PyAV | 18.1.0 |
| NumPy | 2.5.3 |
| Pillow | 12.3.0 |
| OpenCV | 5.0.0.93 |
| ONNX Runtime | 1.30.0 |

语音识别模型：`Qwen/Qwen3-ASR-1.7B`（识别文字）+ `Qwen/Qwen3-ForcedAligner-0.6B`（字级时间戳），位于 `skill/assets/dependencies/models/`。CUDA 运行库随 PyTorch 自带，不再单独打包；无可用 GPU 时自动退回 CPU。音频清理使用的 Silero VAD v6（ONNX）位于 `models/silero-vad/`。
