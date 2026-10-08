# video-montage

`video-montage` 是一个 Codex 技能，用随包的 Python、Whisper 模型和 FFmpeg 完成中文口播混剪。内部模块分别负责语义分析、逐帧渲染、成片编码和独立质检。

版本变化见 [版本更新](docs/版本更新.md)。

新无人值守任务使用[自动混剪流程](references/autonomous/workflow.md)，整批选片遵循[多样化规则](references/autonomous/batch-diversity.md)：优先使用不同开头，再减少整条组合、中段和结尾的重复；三条及四五十条批次都按请求数量规划，素材有限时均衡复用，不设固定使用次数上限。程序生成绑定计划的重复报告，语义与成片质量检查继续执行。已有旧状态和审核仍沿用原流程。

## 安装

项目根目录和安装后的技能目录使用相同的组织结构：

```text
video-montage/
├── SKILL.md                 # 唯一技能入口
├── agents/openai.yaml       # 技能元数据
├── scripts/                 # 工作流脚本与运行校验
├── references/              # 工作流说明、审核合同与配置模板
└── assets/                  # 字体、Python、FFmpeg 和模型等运行资源
```

源包额外保留 `docs/`、`tools/` 和安装/卸载/打包 CMD，供维护与分发使用；这些文件不进入 Codex 技能目录。

在 64 位 Windows 上完整解压项目，双击 `install.cmd`。安装器先检查随包依赖并在源包中运行发布回归，再复制必要文件到临时安装副本，检查其运行环境和执行器预检；通过后安装到 `%CODEX_HOME%\skills\video-montage`（未设置 `CODEX_HOME` 时为 `%USERPROFILE%\.codex\skills\video-montage`）。安装目录仅保留技能入口与元数据、工作流说明、组件脚本及所需合同/素材、随包依赖，以及运行环境和技能校验脚本；不安装 `docs/`、Git 文件、根目录 README、CMD 脚本、测试、任务记录或发布工具。重新安装会替换本项目已有安装，清除上次安装中的多余文件。完成或失败后会显示结果并等待按键；脚本调用可加 `-NoPause`。

如果检测到 `%USERPROFILE%\video-montage` 中已有本项目安装，安装器会清理该目录及其中的全部文件，并移除其技能连接。安装后彻底退出并重新打开 Codex。

只检查源包而不改动安装目录：`install.cmd -PreflightOnly`。

卸载：从源包运行 `uninstall.cmd`，删除 Codex 技能目录中的 `video-montage` 安装；安装目录不再包含 CMD 脚本。双击运行时会显示结果并等待按键，命令返回实际结果。自动化调用可使用 `uninstall.cmd -NoPause`。

## 使用

流程新增最后的整片倍速步骤：剪辑及所需包装完成后，画面和音频统一加速至 **1.2 倍**，保持音调与字幕同步，再检查并输出成品。无包装交付同样适用；字幕重烧从未加速的纯净输入重新合成，最后只加速一次。最终证据和审核须绑定倍速后的实际成品。

自主流程的 `package` 和 `final-evidence --clean` 已自动执行该步骤。旧任务无包装交付时，在 `controller-finalize` 添加 `--final-speed`；要做包装时不添加该参数，由包装器在合成后加速。无包装交付的未加速输入保存在 `临时文件/manifests/clean_inputs/`，避免和交付的倍速 MP4 混用。字幕 TXT 与图层配置继续按未加速输入编辑。

在 Codex 中统一调用 `video-montage`，项目根目录 `SKILL.md` 是唯一技能入口。新任务及其返修按[自主流程](references/workflows/autonomous-workflow.md)使用 `scripts/autonomous/scripts/autonomous_montage.py`，由 Codex 审核语义、画面和字幕，程序核验 ASR、PCM 与哈希。已有 `three_suite_ff_state.json` 或 v260928 审核回执的任务按[旧任务流程](references/workflows/legacy-workflow.md)继续运行，不转换状态或回执。旧任务由执行器 `scripts/executor/scripts/three_suite_ff.py` 按 `preflight → init → semantic-run → semantic-complete → controller-preflight → controller-finalize → controller-validate → complete` 顺序运行。语义模块制定和审核帧计划，帧渲染器剪出预成片，FFmpeg 控制器编码并复检最终视频。

生产裁切使用 `source_in_frame`、`speech_end_frame`、`source_out_frame_exclusive` 和源帧率；只有秒数的计划会被拒绝。解码后的帧时间戳须为从零开始的恒定帧率，变帧率及非零视频起点会在编码前被拒绝。素材须绑定原片 SHA-256 和原始帧区间，用户判坏的区间不能通过改名或新候选 ID 绕过。计划 ID 在忽略大小写后仍须唯一。两种流程按各自契约执行语义、切点和成片复检；自主流程不声明人工听审、独立审核或强制对齐，旧流程保留原审核门禁。

默认成片为 H.264/AAC、1440×2560、60 fps，不额外添加背景音乐、字幕或叠加元素。新任务合同见[自动混剪流程](references/autonomous/workflow.md)；旧任务合同见 [语义流程](references/workflows/semantic-workflow.md)、[渲染流程](references/workflows/executor-workflow.md) 和 [控制器流程](references/workflows/controller-workflow.md)。

未请求包装的新任务，在 `clean-qc` 后运行 `final-evidence --clean`，由 Codex 查看成片画面并提交最终审核，再执行 `complete --review <JSON>`。整批通过后交付 `混剪（无包装）/` 中的 MP4。

### 可选成片包装

新任务按自主流程执行 `subtitle-draft → subtitle-review → package → final-evidence → complete`，由 Codex 审核包装版画面和字幕；下述控制器及 `packaging-*` 命令用于旧任务。

包装模块生成可编辑中文字幕草稿，并按每条视频的 JSON 配置加入文字钉、BGM、免责图和明星名牌。字幕只烧录进视频，保留 `字幕/subtitle-xx.txt` 供修改后重新烧录，不生成外挂字幕轨或 `.srt` 文件；包装视频放 `成片/`，纯净视频放 `混剪（无包装）/`，配置放 `临时文件/config/`，清单和重烧上下文放 `临时文件/manifests/`。字幕目录固定包含无扩展名文件 `修改字幕后让AI重新烧录`。包装版另存，包装配置、素材、字幕和输出均以 SHA-256 绑定。旧任务在 `controller-validate` 后依次运行 `packaging-draft`、`packaging-finalize`、`packaging-validate`，再运行 `complete`；没有包装配置时继续原流程。已有 1440×2560、60 fps 视频也可用独立命令包装，结果标为单条测试。命令、配置和审核格式见 [包装说明](references/packaging/workflow.md)。

完成后的自主任务修改字幕时，运行 `reburn --job-dir <任务目录> --plan-id <ID> --subtitle-txt <修改后的TXT>`。按命令返回的草稿和配置完成新的 `subtitle-review → package → final-evidence → complete`。保留的纯净输入及 QC 可复用，新的字幕和包装画面必须重新审核；原始素材仍需可访问且哈希一致。

## 随包依赖与检查

`assets/dependencies/python/` 包含 64 位 Python 3.13.15 和模块，`assets/dependencies/ffmpeg/` 包含 FFmpeg、FFprobe，`assets/dependencies/models/` 包含完整的 `faster-whisper-large-v3-turbo` 模型，`assets/dependencies/cuda/` 包含可选的 CUDA 推理 DLL。GPU 推理需要兼容的 NVIDIA 驱动；无法使用 CUDA 时，ASR 可回退 CPU。精确版本与模型 revision 见 [依赖版本](docs/依赖版本.md)。

运行环境检查：`assets\dependencies\python\python.exe scripts\verify_runtime.py`。完整发布回归：`assets\dependencies\python\python.exe tools\validate_release.py`。

## 打包分发

运行 `package.cmd`，在项目的 `release/` 目录生成 `video-montage-v261008.zip`，解压后顶层文件夹为 `video-montage-v261008`。可传入压缩包文件名版本，例如 `package.cmd v261009` 会生成 `release/video-montage-v261009.zip`，但包内顶层文件夹仍按当前项目版本命名。完成或失败后会显示结果并等待按键；脚本调用可加 `-NoPause`。


所有新任务的交付目录放在项目 `work/`，时间戳使用北京时间（`YYYYMMDD_HHMMSS`），同秒重名拒绝覆盖：

```text
work/
└── 自动化混剪_20261008_153000/
    ├── 成片/                       # 包装、烧录后的 MP4
    ├── 混剪（无包装）/             # 供重新烧录的纯净 MP4
    ├── 字幕/
    │   ├── subtitle-xx.txt
    │   └── 修改字幕后让AI重新烧录
    └── 临时文件/                   # 完成后保留的必要文件
        ├── config/                 # 重烧需要的包装配置
        ├── manifests/              # 清单、纯净输入校验和重烧上下文
        ├── autonomous_state.json
        └── work_order.json
```

运行 `init --work-order <JSON>` 自动创建交付目录与项目临时运行目录，返回的 `job_dir` 为 `work/<交付目录名>/临时文件/`；后续命令使用这个路径。工作单 `output_root` 可省略，填写时必须指向 `work`。过程状态、日志、审核、证据和报告统一保存在 `临时文件/` 的英文子目录。自主任务完成后清理过程记录、临时 MP4、ASS 和字体副本，仅保留配置、清单、纯净输入授权、计划/转写/素材文案等重烧上下文及最小工作单和状态。返修和字幕重烧复用同一交付目录；整批临时视频通过最终检查后才替换交付文件，替换失败会回滚已替换文件。新任务不再创建根目录 `.runtime/`。Git、安装和分发排除 `work/`，并继续排除兼容旧任务的 `.runtime/`。已有旧流程任务保留原路径和绑定。
