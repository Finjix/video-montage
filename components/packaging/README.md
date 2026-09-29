# 成片包装

包装只接受 1440×2560、60 fps、带音频的纯净成片。完整混剪任务先通过语义和控制器质检，再包装；现有视频可以用独立命令测试包装，但其回执标记为 `standalone_test`，不代表通过混剪交付门禁。

## 单条视频

以下命令从项目根目录运行，使用随包 Python。先在输出目录的 `subtitles/` 生成 `subtitle-demo-01.txt`，检查并修改文字和时间；再在 `config/` 编写配置。TXT 内使用带时间码的字幕格式，仅供编辑和烧录。每个时间点最多显示一行；同一条时间码内手工写入多行时，会按文字显示宽度分配时长，依次显示。每行至少需要 20 毫秒，不足时会拒绝该时间码。

```powershell
dependencies\python\python.exe components\packaging\scripts\package_video.py draft --input D:\input.mp4 --plan-id demo-01 --output-dir D:\job\output
dependencies\python\python.exe components\packaging\scripts\package_video.py render --config D:\job\output\config\packaging.json --output-dir D:\job\output --manifest D:\job\output\manifests\packaging_manifest.json
dependencies\python\python.exe components\packaging\scripts\package_video.py validate --manifest D:\job\output\manifests\packaging_manifest.json --report D:\job\output\reports\packaging_validation.json
```

没有独立审核文件时，技术检查返回 `technical_pass_pending_review`。审核者观看最终视频并听取原声与 BGM 后，提供下文的审核授权和审核文件，再用 `validate --review D:\job\review.json --review-authority D:\job\review_authority.json` 取得完整验证结果。不要把自动生成的审核结论当作独立审核。

## 包装配置

配置文件为 UTF-8 JSON。相对素材路径以配置文件所在目录为基准。`outputs` 中每个 `plan_id` 对应一条成片；完整任务必须覆盖纯净交付清单的所有 `plan_id`。`input_sha256` 和素材 `sha256` 可用于提前拒绝被替换的文件；渲染记录总会写入实际哈希。字幕 TXT 可以在生成后编辑；若配置中填写了 `subtitle_sha256`，编辑后须同步更新该值。旧配置的 `subtitle_srt` 字段仍可读取，但新配置应使用 `subtitle_txt`。字幕字体默认使用随项目提供的 `components/packaging/assets/fonts/WenYue-XinQingNianTi-W8.otf`，无需在配置中指定；顶层可选的 `subtitle_font_path` 只接受与该文件 SHA-256 完全一致的副本。

```json
{
  "schema": "video-montage-packaging/v1",
  "outputs": [
    {
      "plan_id": "demo-01",
      "input_path": "D:/input.mp4",
      "subtitle_txt": "../subtitles/subtitle-demo-01.txt",
      "nameplate": {"path": "assets/nameplate.png"},
      "text_pins": [
        {"path": "assets/pin.png", "start_frame": 300, "end_frame_exclusive": 480}
      ],
      "disclaimer": {"path": "assets/disclaimer.png"},
      "bgm": {"path": "assets/music.mp3", "gain_db": -18}
    }
  ]
}
```

名牌省略区间时显示从第 0 帧到第 192 帧之前；短于 192 帧的视频显示到结尾。免责图覆盖全片。文字钉必须写明起止帧，结束帧不包含在显示区间中。图片作为独立透明图层处理，必须使用包含可见像素和透明区域的 9:16 PNG。BGM 循环到片尾，默认 `-18 dB`，原口播音量不自动减半。字幕默认使用文悦新青年体 W8、`#FFDE00` 黄字、黑色描边。样式按提供的 1920×3414 剪映参考视频标定为字号 12、描边粗细 40、Y=-1300；字幕里每处“无尽冬日”单独使用字号 13（对应 ASS 字号 203），并叠加参考花字的青蓝填充、斜向浅色条纹及奶黄、粉、白、蓝多层描边。其他文字保持字号 12（ASS 字号 187）的黄字黑描边。包装到 1440×2560 时按画布比例缩放，字幕中心距画面顶端约 1768 像素，水平居中。字幕每次只显示一行，显示宽度不超过 28 个半角字符单位；自动草稿把过长文字拆成依次出现的单行字幕，手工字幕中超宽的行须修改。字体文件缺失、变更或 FFmpeg 回退到其他字体时，渲染会失败。

花字 v3 使用逐字起算的浅青斜纹、白色字面边缘、向下错位的蓝色立体层和粉黄外轮廓。黄色外边加粗，并分别标定横向、纵向厚度。混排时在花字两侧添加排版留白，单独成行时不增加边缘空格；字号仍为 13。渲染测试检查普通字幕的黑描边与花字轮廓之间至少留出 8 个输出像素。

对照用户提供的 264×115 原图，可运行 `dependencies/python/python.exe tools/compare_subtitle_flower.py --reference <原图路径> --output-dir <对比输出目录>`。工具调用实际字幕渲染器，把字形等比缩放并平移到参考画布，计算前景并集（向外扩 2 像素）的 RGB SSIM；不以大面积黑色背景提高分数。阈值为 0.90，低于阈值返回失败。输出左右对照 PNG、原图、渲染图及包含字体和渲染器哈希的 JSON。这是明确的图像测量指标，仍需检查实际视频的混排与遮挡。

字幕只烧录进画面；输出 MP4 只有一条视频流和一条音频流，没有可开关的字幕轨。渲染记录保存字体路径、哈希和字幕样式，验证时重新核对字体哈希。合成使用的 ASS 和临时字体副本会在完成后删除；项目中的原始字体保留。包装 MP4、可编辑的 `subtitle-xx.txt`、配置、清单和校验 JSON 分别放在输出根目录、`subtitles/`、`config/`、`manifests/` 和 `reports/`。不保留重复的配置或字幕快照；清单中的快照字段引用分类后的文件并保留 SHA-256 绑定。修改配置、输入视频、字幕、字体或素材会使旧验证结果失效；重新生成应使用新输出目录，程序拒绝覆盖已有成片。
从其他目录提供配置时，存入 `config/` 的副本会将输入视频和素材路径改为绝对路径，并改为引用 `subtitles/` 中的字幕副本，因此两个配置文件的 SHA-256 可以不同；两者均由清单分别绑定。

## 修改字幕后重新烧录

编辑上一版输出目录 `subtitles/` 中的 `subtitle-demo-01.txt`，或将其复制出来再编辑。然后使用 `reburn`，它会读取上次的包装记录，从纯净成片和原素材重新合成，自动记录新版字幕哈希，并使用项目内同一份 W8 字体；旧记录中的外部字体路径无需继续存在。旧记录没有字幕样式时也使用新默认值。无需重新运行 Whisper，也无需手工修改原配置。新视频放进新目录根部，字幕 TXT 和配置分别放进 `subtitles/` 和 `config/`；旧 MP4 不会被覆盖。

```powershell
dependencies\python\python.exe components\packaging\scripts\package_video.py reburn --previous-manifest D:\job\output\manifests\packaging_manifest.json --plan-id demo-01 --subtitle-txt D:\job\output\subtitles\subtitle-demo-01.txt --output-dir D:\job\output-v2 --manifest D:\job\output-v2\manifests\packaging_manifest.json
dependencies\python\python.exe components\packaging\scripts\package_video.py validate --manifest D:\job\output-v2\manifests\packaging_manifest.json --report D:\job\output-v2\reports\packaging_validation.json
```

完整任务用控制器命令 `packaging-reburn`，参数相同但以 `--job-dir D:\job` 代替 `--previous-manifest`。它会重新合成整批包装版，并使旧包装验证及完成回执失效；新版仍须运行 `packaging-validate` 和 `complete`。

## 完整任务

在 `controller-validate` 之后运行：

```powershell
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-draft --job-dir D:\job --output-dir D:\job\output
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-finalize --job-dir D:\job --config D:\job\output\config\packaging.json --output-dir D:\job\output --manifest D:\job\output\manifests\packaging_manifest.json
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-validate --job-dir D:\job --manifest D:\job\output\manifests\packaging_manifest.json --review D:\job\review.json --review-authority D:\job\review_authority.json --report D:\job\output\reports\packaging_validation.json
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py complete --job-dir D:\job
```

`packaging-draft` 在 `subtitles/` 生成全部 `subtitle-xx.txt`，并在 `reports/` 生成 `subtitle_draft.json`；修改后的字幕应由配置引用。`packaging-finalize` 核对全批次纯净视频 SHA-256，再生成包装版。若没有运行 `packaging-draft`，`complete` 沿用原来的纯净成片流程。
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
