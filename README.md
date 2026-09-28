# video-montage

`video-montage` 是一个 Codex 技能，用随包的 Python、Whisper 模型和 FFmpeg 完成中文口播混剪。内部模块分别负责语义分析、逐帧渲染、成片编码和独立质检。

版本变化见 [版本更新](docs/版本更新.md)。

## 安装

在 64 位 Windows 上完整解压项目，双击 `install.cmd`。安装器先检查随包依赖，再在临时副本中运行回归与控制器预检；通过后清空 `%USERPROFILE%\video-montage`（包括其中的个人文件）并安装新副本。

安装器在 `%CODEX_HOME%\skills`（未设置时为 `%USERPROFILE%\.codex\skills`）和 `%USERPROFILE%\.agents\skills` 中注册同一个 `video-montage` 技能。安装后彻底退出并重新打开 Codex。

只检查源包而不改动安装目录：`install.cmd -PreflightOnly`。

卸载：运行 `uninstall.cmd`，删除 `%USERPROFILE%\video-montage` 和两个技能目录中指向该安装的 `video-montage` 连接。从安装目录运行时，自删除在后台完成，命令返回 3 表示仍在进行，不能视为卸载成功；从项目目录运行时会等待完成并返回实际结果。

## 使用

在 Codex 中调用 `video-montage`。新任务和返修由执行器 `components/executor/scripts/three_suite_ff.py` 按 `preflight → init → semantic-run → semantic-complete → controller-preflight → controller-finalize → controller-validate → complete` 顺序运行。语义模块制定和审核帧计划，帧渲染器剪出预成片，FFmpeg 控制器编码并复检最终视频。

生产裁切使用 `source_in_frame`、`speech_end_frame`、`source_out_frame_exclusive` 和源帧率；只有秒数的计划会被拒绝。素材须绑定原片 SHA-256 和原始帧区间，用户判坏的区间不能通过改名或新候选 ID 绕过。候选首词对齐、切点两侧画面与 PCM 检查、独立审核和成片后复检都是交付门禁。工作单、来源追溯、语义审核、帧计划和质检回执保存在任务目录中。

默认成片为 H.264/AAC、1440×2560、60 fps，不额外添加背景音乐、字幕或叠加元素。详细任务合同见 [语义流程](skill/video-montage/semantic-workflow.md)、[渲染流程](skill/video-montage/executor-workflow.md) 和 [控制器流程](skill/video-montage/controller-workflow.md)。

## 随包依赖与检查

`dependencies/python/` 包含 64 位 Python 3.13.15 和模块，`dependencies/ffmpeg/` 包含 FFmpeg、FFprobe，`dependencies/models/` 包含完整的 `faster-whisper-large-v3-turbo` 模型，`dependencies/cuda/` 包含可选的 CUDA 推理 DLL。GPU 推理需要兼容的 NVIDIA 驱动；无法使用 CUDA 时，ASR 可回退 CPU。精确版本与模型 revision 见 [依赖版本](docs/依赖版本.md)。

运行环境检查：`dependencies\python\python.exe tools\verify_runtime.py`。完整发布回归：`dependencies\python\python.exe tools\validate_release.py`。

## 打包分发

运行 `package.cmd`，在项目的 `release/` 目录生成 `video-montage-v260928.zip`，解压后顶层文件夹为 `video-montage-v260928`。可传入压缩包文件名版本，例如 `package.cmd v260929` 会生成 `release/video-montage-v260929.zip`，但包内顶层文件夹仍按当前项目版本命名。
