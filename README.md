# video-montage

版本 `0.2.0`。用于批量制作中文口播混剪视频的 OpenAI 插件，支持 Windows 10/11 x64 的本地执行环境。

## 使用与发布

`plugin/` 是完整发布目录，包含标准 `plugin.json`、`skills/video-montage/`、业务脚本及小型包装素材。直接将整个 `plugin/` 文件夹压缩成 ZIP 即可上传 ChatGPT 插件目录，或运行 `python dev/build_plugin.py` 生成并检查 `dist/video-montage-0.2.0.zip`。平台要求压缩包不超过 100 MB；上传后仍需开发者身份验证和市场审核。

插件采用仅包含技能和脚本的 Desktop only 形式。`.cmd` 入口负责转发业务参数和 JSON stdin，PowerShell 启动器负责准备环境，以兼容 Windows PowerShell 5.1 的独立 `-` 参数限制。安装时不下载运行环境，首次调用相关功能时自动准备；无需预装 Python，不需修改系统 PATH。AI 阅读 `skills/video-montage/SKILL.md` 后通过统一入口操作：

```powershell
& "plugin/scripts/video_montage.cmd" --help
& "plugin/scripts/video_montage.cmd" runtime --feature base
```

流程：`init` → `prepare` → `annotate` → `check` → `render` → `package`。业务参数、工单格式与已有任务文件兼容；详细格式见技能内的 `references/workflow.md`。

成片默认放在桌面的 `自动化混剪_YYYYMMDD_HHMMSS/`，旁边只有任务目录 `temp/`。修改字幕后再次执行 `package`，仅重做有变化的成片。首次下载会增加准备时间。

## 按需依赖与缓存

| 使用功能 | 自动准备 |
| --- | --- |
| 基础命令、计划检查、发布无包装成片 | 私有 Python 3.13.15 |
| 素材分析、渲染、BGM 处理 | NumPy 2.5.3、Pillow 12.3.0、FFmpeg/FFprobe 9.0.2 |
| 字幕动画包装 | 额外安装 OpenCV 5.0.0.93；仅下载实际用到的动画大表 |
| 识别、校正文字重新对齐 | PyTorch 2.11.0、Transformers 4.57.6、qwen-asr 0.0.6 及两个 Qwen 模型 |

模型为 `Qwen/Qwen3-ASR-1.7B` 和 `Qwen/Qwen3-ForcedAligner-0.6B`，优先 Hugging Face，失败后尝试官方 ModelScope 仓库；两个来源均须通过锁定的文件校验。动画大表从本仓库固定 GitHub 提交下载。依赖版本、传递依赖、文件大小和 SHA-256 均随插件锁定。

首次使用 ASR 时检测 NVIDIA 驱动，支持 CUDA 12.8 时安装 CUDA 版，否则安装 CPU 版；CUDA 运行失败自动回退 CPU。运行库随 PyTorch 获取，不安装显卡驱动。可通过 `VIDEO_MONTAGE_DEVICE=cpu` 强制使用 CPU。

缓存默认位于插件安装目录内的 `runtime-cache/`，删除插件目录时可一并清理依赖。可用 `VIDEO_MONTAGE_CACHE_DIR` 指定其他位置。已完整缓存的功能可离线运行。设置 `VIDEO_MONTAGE_OFFLINE=1` 可明确禁止联网。源码中的 `plugin/runtime-cache/` 已被 Git 忽略，发布前应使用不含运行缓存的干净目录。

```powershell
# 仅检查，不触发依赖或模型下载
& ./plugin/scripts/video_montage.cmd runtime --feature asr
# 提前下载指定功能所需依赖和模型
& ./plugin/scripts/video_montage.cmd runtime --feature asr --install
```

命令结果输出到 stdout，准备和下载进度输出到 stderr。退出码 `0` 表示成功，`2` 表示输入或剪辑规则错误，`3` 表示运行时故障。下载失败后解决提示中的网络、空间或权限问题，再运行原命令；成功文件会复用，未完成文件不会当作有效资源。

## 开发与验证

开发工具、测试和验证记录统一放在 `dev/`。运行 `python dev/run_tests.py` 执行不需模型的回归测试；需要媒体测试时通过启动器准备相应依赖。`python dev/build_plugin.py` 检查包结构、清单引用、文件数量及大小并生成 ZIP。`dev/smoke/e2e.py` 用本地 `.素材` 执行三条视频的端到端验证，输出留在外部临时目录。
