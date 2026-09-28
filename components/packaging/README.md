# 成片包装

包装只接受 1440×2560、60 fps、带音频的纯净成片。完整混剪任务先通过语义和控制器质检，再包装；现有视频可以用独立命令测试包装，但其回执标记为 `standalone_test`，不代表通过混剪交付门禁。

## 单条视频

以下命令从项目根目录运行，使用随包 Python。先生成 `subtitle-demo-01.txt`，检查并修改文字、换行和时间；再编写每条视频的配置。TXT 内使用带时间码的字幕格式，仅供编辑和烧录。

```powershell
dependencies\python\python.exe components\packaging\scripts\package_video.py draft --input D:\input.mp4 --plan-id demo-01 --output-dir D:\job\subtitles
dependencies\python\python.exe components\packaging\scripts\package_video.py render --config D:\job\packaging.json --output-dir D:\job\packaged --manifest D:\job\packaging_manifest.json
dependencies\python\python.exe components\packaging\scripts\package_video.py validate --manifest D:\job\packaging_manifest.json --report D:\job\packaging_validation.json
```

没有独立审核文件时，技术检查返回 `technical_pass_pending_review`。审核者观看最终视频并听取原声与 BGM 后，提供下文的审核授权和审核文件，再用 `validate --review D:\job\review.json --review-authority D:\job\review_authority.json` 取得完整验证结果。不要把自动生成的审核结论当作独立审核。

## 包装配置

配置文件为 UTF-8 JSON。相对素材路径以配置文件所在目录为基准。`outputs` 中每个 `plan_id` 对应一条成片；完整任务必须覆盖纯净交付清单的所有 `plan_id`。`input_sha256` 和素材 `sha256` 可用于提前拒绝被替换的文件；渲染记录总会写入实际哈希。字幕 TXT 可以在生成后编辑；若配置中填写了 `subtitle_sha256`，编辑后须同步更新该值。旧配置的 `subtitle_srt` 字段仍可读取，但新配置应使用 `subtitle_txt`。

```json
{
  "schema": "video-montage-packaging/v1",
  "outputs": [
    {
      "plan_id": "demo-01",
      "input_path": "D:/input.mp4",
      "subtitle_txt": "subtitles/subtitle-demo-01.txt",
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

名牌省略区间时显示从第 0 帧到第 192 帧之前；短于 192 帧的视频显示到结尾。免责图覆盖全片。文字钉必须写明起止帧，结束帧不包含在显示区间中。图片作为独立透明图层处理，必须使用包含可见像素和透明区域的 9:16 PNG。BGM 循环到片尾，默认 `-18 dB`，原口播音量不自动减半。字幕默认底部白字黑描边、最多两行，位置避开示例免责图。

字幕只烧录进画面；输出 MP4 只有一条视频流和一条音频流，没有可开关的字幕轨。合成使用的 ASS 会在完成后删除。可编辑的 `subtitle-xx.txt` 放在包装目录的 `subtitles/` 子目录，与 MP4 分开；另保留配置副本及输出 SHA-256。修改配置、输入视频、字幕或素材会使旧验证结果失效；重新生成应使用新输出目录，程序拒绝覆盖已有成片。

## 修改字幕后重新烧录

编辑上一版输出目录 `subtitles/` 下的 `subtitle-demo-01.txt`，或将其复制出来再编辑。然后使用 `reburn`，它会读取上次的包装记录，从纯净成片和原素材重新合成，自动记录新版字幕哈希。无需重新运行 Whisper，也无需手工修改原配置。新的视频、字幕 TXT 和配置会放进新目录；旧 MP4 不会被覆盖。

```powershell
dependencies\python\python.exe components\packaging\scripts\package_video.py reburn --previous-manifest D:\job\packaging_manifest.json --plan-id demo-01 --subtitle-txt D:\job\packaged\subtitles\subtitle-demo-01.txt --output-dir D:\job\packaged-v2 --manifest D:\job\packaging_manifest-v2.json
dependencies\python\python.exe components\packaging\scripts\package_video.py validate --manifest D:\job\packaging_manifest-v2.json --report D:\job\packaging_validation-v2.json
```

完整任务用控制器命令 `packaging-reburn`，参数相同但以 `--job-dir D:\job` 代替 `--previous-manifest`。它会重新合成整批包装版，并使旧包装验证及完成回执失效；新版仍须运行 `packaging-validate` 和 `complete`。

## 完整任务

在 `controller-validate` 之后运行：

```powershell
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-draft --job-dir D:\job --output-dir D:\job\subtitles
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-finalize --job-dir D:\job --config D:\job\packaging.json --output-dir D:\job\packaged --manifest D:\job\packaging_manifest.json
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py packaging-validate --job-dir D:\job --manifest D:\job\packaging_manifest.json --review D:\job\review.json --review-authority D:\job\review_authority.json --report D:\job\packaging_validation.json
dependencies\python\python.exe components\executor\scripts\three_suite_ff.py complete --job-dir D:\job
```

`packaging-draft` 生成全部 `subtitle-xx.txt` 和 `subtitle_draft.json`；修改后的字幕应由配置引用。`packaging-finalize` 核对全批次纯净视频 SHA-256，再生成包装版。若没有运行 `packaging-draft`，`complete` 沿用原来的纯净成片流程。
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
