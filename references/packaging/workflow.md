# 成片包装

字幕审核后按 [AI 包装设计](design.md)制定逐视频方案，明确主字体、黄白配色、整词游戏名花字与既有入场动画，只选择已有资源和文档参数，不临时创造效果、动画、花字或装饰素材；没有合适效果时静态呈现。所有 `render`、`package`、`reburn` 配置均要求每条输出有 `subtitle_design`，不再回退到固定黄字、每批随机字体或随机冰字；不读取 `subtitle_srt` 旧字段。

最终成品输出前，在字幕、图层和音频混合完成后执行一次整片 **1.2 倍速**，再对加速后的成品取证、审核和交付。画面与完整混音同步加速，音频保持原音调，字幕及图层随画面同步；最终时长为原时长除以 1.2，保持 60 fps。保留未加速的纯净视频及其可编辑字幕、图层时间轴；重新烧录时仍从未加速输入合成，最后只加速一次，不能对上一版倍速成品再次加速。最终检查的字幕、图层和切点时间应换算到倍速后的时间轴，清单及输出哈希绑定实际倍速成品。

新建的 v260929 无人值守任务按[自动混剪流程](../autonomous/workflow.md)执行：
Codex 根据原片/成片转写和素材文案先纠错字幕，程序再烧录并对包装版复转写、检测 PCM 和抽取画面证据；最终校验使用与成片哈希绑定的自动审核文件，不填写独立审核授权。以下独立审核示例只用于已启动的 v260928 任务。

完成后的自主任务修改字幕，使用 `scripts/autonomous/scripts/autonomous_montage.py reburn --job-dir <任务目录> --plan-id <ID> --subtitle-txt <修改后的TXT>`，按返回的草稿和配置依次执行新的 `subtitle-review → package → final-evidence → complete`。完成清理会保留配置、纯净输入及 QC、计划、源转写和素材文案等重烧上下文；原始素材仍须可访问且哈希一致。新的字幕及包装画面必须重新审核。下文独立 `reburn` 和控制器 `packaging-reburn` 命令继续用于各自原流程。

包装先完成整批临时编码，再发布视频、字幕、配置和清单；替换异常会回滚已替换文件。自主任务还须通过 `complete` 的最终检查才发布到实际交付目录。用户未请求包装时，按自主流程运行 `final-evidence --clean` 和 `complete` 完成纯净交付。

包装只接受 1440×2560、60 fps、带音频的纯净成片。完整混剪任务先通过语义和控制器质检，再包装；现有视频可以用独立命令测试包装，但其回执标记为 `standalone_test`，不代表通过混剪交付门禁。

所有新输出目录必须放在项目 `work/` 下，按北京时间命名为 `自动化混剪_YYYYMMDD_HHMMSS`，不追加微秒；同秒重名拒绝覆盖。`--manifest` 位于 `临时文件/manifests/`，包装配置位于 `临时文件/config/`。`临时文件/` 内文件夹使用英文名。生产中临时生成审核、报告和证据，自动流程完成全部检查后清理，仅保留重烧必要清单、纯净输入校验、配置、工作单和最小任务状态。字幕目录保留无扩展名文件 `修改字幕后让AI重新烧录`，纯净视频保留在 `混剪（无包装）/`。

## 单条视频

以下命令从项目根目录运行，使用随包 Python。先在输出目录的 `字幕/` 生成 `subtitle-demo-01.txt`，检查并修改文字和时间；再在 `临时文件/config/` 编写配置。TXT 内使用带时间码的字幕格式，仅供编辑和烧录。每个时间点最多显示一行；同一条时间码内手工写入多行时，会按文字显示宽度分配时长，依次显示。每行至少需要 20 毫秒，不足时会拒绝该时间码。

```powershell
assets\dependencies\python\python.exe scripts\packaging\scripts\package_video.py draft --input D:\input.mp4 --plan-id demo-01 --output-dir D:\project\video-montage\work\自动化混剪_20260930_153000
assets\dependencies\python\python.exe scripts\packaging\scripts\package_video.py render --config D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\配置\packaging.json --output-dir D:\project\video-montage\work\自动化混剪_20260930_153000 --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json
assets\dependencies\python\python.exe scripts\packaging\scripts\package_video.py validate --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json --report D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\报告\packaging_validation.json
```

没有独立审核文件时，技术检查返回 `technical_pass_pending_review`。审核者观看最终视频并听取原声与 BGM 后，提供下文的审核授权和审核文件，再用 `validate --review D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\review.json --review-authority D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\review_authority.json` 取得完整验证结果。不要把自动生成的审核结论当作独立审核。

## 包装配置

配置文件为 UTF-8 JSON，素材相对路径以配置文件目录为基准。`outputs` 覆盖所有 `plan_id`，每条填写 `input_path`、`subtitle_txt`、已审核字幕哈希和 `subtitle_design`。主字体与花字范围只从逐输出设计中读取；旧顶层或逐输出字体/花字字段直接拒绝，须改写到 `subtitle_design`。设计覆盖全部字幕索引并绑定准确文字。完整配置合同见 [AI 包装设计](design.md)。

```json
{
  "schema": "video-montage-packaging/v1",
  "outputs": [{
    "plan_id": "demo-01",
    "input_path": "D:/input.mp4",
    "subtitle_txt": "../../字幕/subtitle-demo-01.txt",
    "subtitle_sha256": "审核后的字幕SHA-256",
    "subtitle_design": {
      "subtitle_sha256": "审核后的字幕SHA-256",
      "font": "w8", "game_names": ["无尽冬日"], "game_flower": "ice2",
      "reason": "厚实字形，黄字主叙述，完整游戏名冰字，静态便于阅读。",
      "cues": [{"index": 1, "text": "来玩无尽冬日", "color": "yellow", "entrance": {"effect": "none"}}]
    },
    "nameplate": {"path": "assets/nameplate.png"},
    "disclaimer": {"path": "assets/disclaimer.png"},
    "bgm": {"path": "assets/music.mp3", "gain_db": -18}
  }]
}
```

名牌省略区间时显示前192帧，免责覆盖全片，图片须为含透明区域的9:16 PNG。文字钉仅按明确要求添加，并写明起止帧；装饰卡片不支持。BGM循环到片尾，口播不自动减半。字体为W8、得意黑、方糖体，花字为冰1、冰2、火1。所有选择由设计明确指定，配置与清单保存实际资源哈希；重烧要求原设计快照及哈希，字幕改字或条数改变须重新审查设计，不从旧清单推断字体/花字。

独立生成可换字的透明 PNG：`assets/dependencies/python/python.exe tools/render_subtitle_flower.py --font w8 --text "冰雪世界" --style ice1 --scale 2 --output work/ice-text.png`。同一接口支持单字、较长文案、中英文混排和标点。

默认冰1/冰2继续采用 H.264 `yuv420p`、CRF 18。显式使用火1且字幕中确有该花字时，使用 RGB 合成后转为 H.264 `yuv444p`、CRF 8，保留细红边的颜色精度；否则 4:2:0 色度采样会明显降低该风格的 RGB SSIM。火1的编码体积较大，且部分硬件播放器不支持 H.264 4:4:4；默认随机冰字不受影响。交付清单记录实际 `video_encoding`。

运行 `assets/dependencies/python/python.exe tools/compare_subtitle_flower.py --output-dir work/flower-comparison`，对三套花字进行检查；也可用 `--style ice1 --reference <原图路径>` 单独指定参考图。工具调用生产中的字体渲染、着色和 FFmpeg RGB overlay，在参考尺寸及黑背景下计算前景并集（向外扩 2 像素）的 RGB SSIM；另外调用完整包装流程，以 1440×2560、60 fps 的 H.264 实际烧录输出测量，再截取花字区域并等比还原到参考尺寸。两项阈值均为 0.95，任一风格任一项未通过即返回失败。对照 PNG 三列依次为原图、字体生成、实际视频还原；JSON 记录分数、编码配置、效果参数及字体渲染器哈希。95% 是对所提供三张参考图的可测验收，并非没有参考图的新文案逐字百分比。不同背景仍需检查混排、透明边缘和遮挡。回归测试还检查换字确实产生新字形，并禁止渲染器打开任何固定字样图片。

开发时可用 `tools/build_subtitle_flowers.py --fire1 <火1原图> --ice1 <冰1原图> --ice2 <冰2原图> --optimizer-path <本地SciPy目录> --refit-geometry` 重新标定数值参数。SciPy 仅用于此标定工具，不加入便携运行依赖；生产字体渲染不需要它。标定结果还须通过上面的实际视频验收，不能用标定分数代替最终烧录分数。

字幕只烧录进画面；输出 MP4 只有一条视频流和一条音频流，没有可开关的字幕轨。渲染记录保存字体路径、哈希和字幕样式，验证时重新核对字体哈希。合成使用的 ASS 和临时字体副本会在完成后删除；项目中的原始字体保留。包装 MP4、可编辑的 `subtitle-xx.txt` 和重烧配置分别放在 `成片/`、`字幕/` 和 `临时文件/config/`。清单和校验 JSON 放在项目 `work/<交付目录名>/临时文件/manifests/` 和 `报告/`，生产期间使用，完成后只保留重烧必要的清单和纯净输入校验。不保留重复的配置或字幕快照；清单中的快照字段引用分类后的文件并保留 SHA-256 绑定。修改配置、输入视频、字幕、字体或素材会使旧验证结果失效；返修及重新烧录在原输出目录覆盖更新，临时视频检查通过后替换已有成片，并重新生成验证结果。
从其他目录提供配置时，存入 `临时文件/config/` 的副本会将输入视频和素材路径改为绝对路径，并改为引用 `字幕/` 中的字幕副本，因此两个配置文件的 SHA-256 可以不同；两者均由清单分别绑定。

## 修改字幕后重新烧录

编辑上一版输出目录 `字幕/` 中的 `subtitle-demo-01.txt`，或将其复制出来再编辑。然后使用 `reburn`，它会读取上次的包装记录，从纯净成片和原素材重新合成，自动记录新版字幕哈希，并根据上一版字体哈希使用项目内同一种字体；旧记录中的外部字体路径无需继续存在。旧记录没有字幕样式时采用新的随机字体默认值，生成后的选择会保存。无需重新运行 Whisper，也无需手工修改原配置。新视频覆盖原目录 `成片/` 中的对应 MP4，字幕 TXT、配置和清单同步原位更新，不新建交付文件夹。编码失败时保留原 MP4；旧质检与完成回执失效，新版须重新验证。

```powershell
assets\dependencies\python\python.exe scripts\packaging\scripts\package_video.py reburn --previous-manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json --plan-id demo-01 --subtitle-txt D:\project\video-montage\work\自动化混剪_20260930_153000\字幕\subtitle-demo-01.txt --output-dir D:\project\video-montage\work\自动化混剪_20260930_153000 --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json
assets\dependencies\python\python.exe scripts\packaging\scripts\package_video.py validate --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json --report D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\报告\packaging_validation.json
```

完整任务用控制器命令 `packaging-reburn`，参数相同但以 `--job-dir D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件` 代替 `--previous-manifest`。它会重新合成整批包装版，并使旧包装验证及完成回执失效；新版仍须运行 `packaging-validate` 和 `complete`。

## 完整任务

在 `controller-validate` 之后运行：

```powershell
assets\dependencies\python\python.exe scripts\executor\scripts\three_suite_ff.py packaging-draft --job-dir D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件 --output-dir D:\project\video-montage\work\自动化混剪_20260930_153000
assets\dependencies\python\python.exe scripts\executor\scripts\three_suite_ff.py packaging-finalize --job-dir D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件 --config D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\配置\packaging.json --output-dir D:\project\video-montage\work\自动化混剪_20260930_153000 --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json
assets\dependencies\python\python.exe scripts\executor\scripts\three_suite_ff.py packaging-validate --job-dir D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件 --manifest D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\清单\packaging_manifest.json --review D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\review.json --review-authority D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\review_authority.json --report D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件\报告\packaging_validation.json
assets\dependencies\python\python.exe scripts\executor\scripts\three_suite_ff.py complete --job-dir D:\project\video-montage\work\自动化混剪_20260930_153000\临时文件
```

`packaging-draft` 在 `字幕/` 生成全部 `subtitle-xx.txt`，并在项目 `work/<交付目录名>/临时文件/reports/` 生成 `subtitle_draft.json`；修改后的字幕应由配置引用。`packaging-finalize` 核对全批次纯净视频 SHA-256，再生成包装版。若没有运行 `packaging-draft`，`complete` 沿用原来的纯净成片流程。
直接使用包装模块的 `render --delivery-manifest` 时，还必须提供同一清单对应的 `--controller-validation` 通过回执；缺少回执的输出只能按独立测试模式生成。

独立审核授权和审核文件示例：

```json
{
  "schema": "video-montage-independent-packaging-review-authority/v1",
  "decision": "authorized",
  "reviewer": {"role": "independent_packaging_reviewer", "review_id": "审核者标识"}
}
```

```json
{
  "schema": "video-montage-packaging-review/v1",
  "manifest_sha256": "包装清单的 SHA-256",
  "authority_sha256": "审核授权文件的 SHA-256",
  "reviewer_role": "independent_packaging_reviewer",
  "reviewer_id": "审核者标识",
  "results": [
    {
      "plan_id": "demo-01",
      "output_sha256": "包装成片的 SHA-256",
      "visual_pass": true,
      "audio_pass": true,
      "subtitle_pass": true,
      "overlay_pass": true
    }
  ]
}
```

审核者应检查片头、片尾、字幕换行、每处文字钉起止、人名条消失位置、全程免责图，以及口播、BGM 音量和切点声音。审核文件必须针对实际播放的成片与当前包装清单填写；程序只核验绑定关系和结论，不替审核者观看或听取。


当前成品组织规则：新交付目录按北京时间命名为 `work/自动化混剪_YYYYMMDD_HHMMSS/`，不追加微秒，同秒重名拒绝覆盖。`临时文件/` 内目录使用英文。生产期间可以生成审核、证据和报告；自动流程通过全部交付检查后清理这些过程文件，仅保留 `config/`、重烧必要的 `manifests/`、最小工作单和任务状态。纯净视频和字幕继续分别保存在 `混剪（无包装）/`、`字幕/`。完整剪辑返工重新生成证据、审核和校验；字幕重烧复用纯净视频和绑定的纯净输入校验文件。旧任务仍使用其原有路径，避免破坏哈希绑定。此规则替代上文关于完成后保留全部运行记录的要求。

新字体可单独预览，例如：

```powershell
assets/dependencies/python/python.exe tools/render_subtitle_flower.py --font smiley --text "冰雪世界" --style ice1 --output work/smiley-ice.png
assets/dependencies/python/python.exe tools/render_subtitle_flower.py --font fangtang --text "全新挑战" --style fire1 --output work/fangtang-fire.png
```

参考图的 95% SSIM 回归验收固定使用 W8，以免更换字体本身的字形差异影响比较。新字体另以实际烧录、透明轮廓、任意文案、混排间距、实际字高/中心位置以及字体选择/重新烧录一致性测试验收。
