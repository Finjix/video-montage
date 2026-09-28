# video-montage

`video-montage` v260928 是一个 Codex 技能，用随包的 Python、Whisper 模型和 FFmpeg 完成中文口播混剪。内部模块分别负责语义分析、逐帧渲染、成片编码和独立质检；安装后只注册 `video-montage` 一个技能。

## 使用

- 安装：双击 `install.cmd`。每次安装会清空 `%USERPROFILE%\video-montage`，再部署新版本。
- 只检查运行环境：`install.cmd -PreflightOnly`。
- 在 Codex 中调用 `video-montage` 技能，按 [使用说明](docs/使用说明.md) 开始任务。
- 运行环境检查：`dependencies\python\python.exe tools\verify_runtime.py`。
- 运行回归：`dependencies\python\python.exe tools\validate_release.py`。

安装包不生成 `.manifests/`、`artifacts/` 或部署报告。剪辑任务的素材追溯、审核回执和 SHA-256 证据仍写在对应任务目录中。跨电脑安装见[部署说明](docs/跨电脑部署说明.md)，随包依赖见[便携依赖说明](docs/便携依赖说明.md)。
