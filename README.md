# video-montage

video-montage 是 FF 三件套（V20.2.5）项目，包含 FFmpeg 控制器、语义分析包和完整执行器。三个可安装的 Codex 技能位于 `components/`；Python、FFmpeg 和模型集中在 `dependencies/`；维护脚本位于 `tools/`，中文使用文档位于 `docs/`。

## 使用入口

- 工作流：在 Codex 中使用 `montage-three-part-orchestrator-ff` 技能
- 部署：双击 `start.cmd`；部署与技能注册均由此文件完成
- 只做部署预检：`start.cmd -PreflightOnly`
- 校验三件套：`dependencies\python\python.exe tools\build_ff_suite.py verify`
- 运行回归与发布检查：`dependencies\python\python.exe tools\validate_v20_release.py`

`suite_registry.json`、`runtime-lock.json` 和 `.manifests/` 是运行时清单。维护脚本生成的本机报告写入 `artifacts/`，不纳入版本控制。详细规则见 [使用说明](docs/使用说明.md) 和 [跨电脑部署说明](docs/跨电脑部署说明.md)。

Python、运行所需模块、Visual C++ DLL、FFmpeg 和模型均在仓库内；启动脚本不查找或安装系统 Python。依赖来源见 [便携依赖说明](docs/便携依赖说明.md)。
