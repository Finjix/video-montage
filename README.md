# video-montage

版本：`0.0.1`。中文口播混剪通用智能体技能（Agent Skill），可在 Codex、Claude Code 等支持 SKILL.md 的环境中使用。

## 目录与发布

- `skill/`：完整技能，包含 `SKILL.md`、`agents/`、`scripts/`、`references/` 和 `assets/`（含运行依赖与模型）。
  - `scripts/video_montage.py`：唯一入口（`<command> --job-dir ...`）；`scripts/verify_runtime.py`：运行时自检。
  - `scripts/montage/`：核心包。`workflow` 状态机与命令，`planning`/`batch`/`cast`/`speech`/`text_library` 等审阅策略；`media/` 为 ASR、裁剪渲染、音频清理与响度；`packaging/` 为字幕包装与设计渲染。
  - `references/`：按阶段查阅的说明文档；`assets/`：文本库、无效区间、包装素材与 `dependencies/` 运行依赖。
- `dev/`：本地开发专用目录。
- `work/`：本地任务产物。

发布时直接将整个 `skill` 文件夹压缩为 ZIP，再按需改名。

测试：`skill/assets/dependencies/python/python.exe dev/run_tests.py [--group montage|media|packaging]`。新增开发工具和测试统一放入 `dev/`，不要在仓库根目录或 `skill/` 中建立开发工具、测试或临时产物目录。技能运行所需的脚本、资源和依赖保留在 `skill/`。

## 依赖版本

运行平台：64 位 Windows。

| 依赖 | 版本 |
|---|---|
| Python | 3.13.15 |
| FFmpeg / FFprobe | 9.0.2 |
| faster-whisper | 1.2.1 |
| CTranslate2 | 4.8.2 |
| PyAV | 18.1.0 |
| NumPy | 2.5.3 |
| Pillow | 12.3.0 |
| OpenCV | 5.0.0.93 |
| ONNX Runtime | 1.30.0 |
| CUDA cuBLAS | 12.9.0.13 |
| CUDA Runtime | 12.9.37 |

Whisper 模型：`mobiuslabsgmbh/faster-whisper-large-v3-turbo`。
