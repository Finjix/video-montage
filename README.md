# video-montage

版本：`0.1.0`。中文口播明星混剪智能体技能（Agent Skill），可在 Codex、Claude Code 等支持 SKILL.md 的环境中使用。

## 目录与发布

- `skill/`：完整技能，包含 `SKILL.md`、`agents/`、`scripts/`、`references/` 和 `assets/`（含运行依赖与模型）。
  - `scripts/video_montage.py`：唯一入口（`<command> --job-dir ...`）；`scripts/verify_runtime.py`：运行时自检。
  - `scripts/montage/`：核心包。`cli` 命令行，`job` 任务与用户决定，`catalog` 素材分析（镜头切点、语音单元、可剪点、联系表），
    `plan` 计划解析与剪点求解，`rules`/`batch` 编辑与整批规则，`render` 无包装渲染与质检，`package` 包装、校验与发布，
    `asr`/`audio`/`bgm` 语音识别、响度与背景音乐；`packaging/` 为字幕设计与花字动画渲染。
  - `references/`：`workflow`（命令与文件格式）、`editing`（编辑规则）、`packaging`（包装、BGM、字幕）。
  - `assets/`：文本库、用户否决区间、包装素材与 `dependencies/` 运行依赖。
- `dev/`：本地开发专用目录（测试、调试脚本）。

流程：`init` → `prepare`（约 1–2 分钟）→ `annotate` → `check`（不到 1 秒，反复修改计划）→ `render` → `package`（发布）。
成片直接放在当前用户桌面的 `自动化混剪_YYYYMMDD_HHMMSS/`，旁边只有 `temp/`（任务目录，可在工单中用 `output_root` 指定其他位置）。
修改字幕后重新运行 `package` 即可重新烧录，只重做有变化的成片。

发布时直接将整个 `skill` 文件夹压缩为 ZIP，再按需改名。

测试：`skill/assets/dependencies/python/python.exe dev/run_tests.py [pattern]`。新增开发工具和测试统一放入 `dev/`，不要在仓库根目录或 `skill/` 中建立开发工具、测试或临时产物目录。

## 依赖版本

运行平台：64 位 Windows。

| 依赖 | 版本 |
|---|---|
| Python | 3.13.15 |
| FFmpeg / FFprobe | 9.0.2 |
| PyTorch | 2.11.0+cu128 |
| Transformers | 4.57.6 |
| qwen-asr | 0.0.6 |
| NumPy | 2.5.3 |
| Pillow | 12.3.0 |
| OpenCV | 5.0.0.93 |

语音识别模型：`Qwen/Qwen3-ASR-1.7B`（识别文字）+ `Qwen/Qwen3-ForcedAligner-0.6B`（字级时间戳，也用于校正文字后的重新对齐），位于 `skill/assets/dependencies/models/`。CUDA 运行库随 PyTorch 自带；无可用 GPU 时自动退回 CPU。
