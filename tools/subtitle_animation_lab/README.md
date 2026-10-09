# 字幕入场动画独立实验工具

复刻三种参考效果，支持替换单行字幕文字。参考视频与离线校准工具位于本目录；运行代码共享 `scripts/packaging/scripts/reference_animation.py`，数值参数位于 `assets/packaging/animations/parameters/`。生产包装通过 `subtitle_design` 接入透明动画，并在合成后统一倍速，见 [AI 包装设计](../../references/packaging/design.md)。

## 使用

在项目根目录运行随包 Python，无须安装依赖：

```powershell
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\cli.py render --effect bounce_up --text '全新挑战' --output-dir work\动画测试\弹入
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\cli.py render --effect shout_wave --text '冰雪世界' --size 1440x2560 --output-dir work\动画测试\声波
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\cli.py render --effect ice_drift --text 'ABC冰雪2026' --output-dir work\动画测试\冰雪
```

效果标识为 `bounce_up`（向上弹入）、`shout_wave`（呐喊声波）、`ice_drift`（冰雪飘动）。默认文字“无尽冬日”，W8 字体与冰2花字，1920×3414、60 fps、60 帧、H.264 MP4，无音轨。支持 1440×2560；单行最多32个字符，长字幕整体等比缩小。拒绝覆盖已有演示 MP4。第30帧起保持普通静态花字。

一次生成全部参考演示与对照：

```powershell
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\cli.py suite
```

默认交付目录为 `work/字幕入场动画试验_<北京时间戳>/`，包含演示、原速/四倍慢放对照、关键帧、CSV 和 JSON 报告。

验证替换文字、1440版和视频规格，再生成本地预览页（将示例目录替换为实际suite输出目录）：

```powershell
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\validate_delivery.py --output-dir work\字幕入场动画试验_20261009_122100
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\build_report.py --output-dir work\字幕入场动画试验_20261009_122100
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\serve_preview.py --output-dir work\字幕入场动画试验_20261009_122100
```

预览服务器仅监听本机 `http://127.0.0.1:8789/`，支持MP4逐帧拖动；也可直接在浏览器打开交付目录里的HTML。预览页可切换原速、四倍慢放、参考与复刻对照，并暂停检查任意帧。

单独验收：

```powershell
.\assets\dependencies\python\python.exe tools\subtitle_animation_lab\cli.py compare --reference tools\subtitle_animation_lab\references\ice_drift.mp4 --rendered work\动画测试\冰雪\冰雪飘动.mp4 --output-dir work\动画测试\冰雪对照
```

95%验收仅针对相同参考文字的复刻。其他文字用于验证通用渲染能力，不会与“无尽冬日”作相似度比较。

## 验收口径

- 比较最终 MP4 解码后的60帧，不比较编码前缓存图。
- 沿用项目花字的 RGB SSIM：11×11 高斯窗口、σ=1.5、RGB任一通道大于25的前景并集，外扩2像素。
- 第0–29帧和30–59帧分别求平均，两段均须达到0.95。完整记录每帧分数、最低分、输入/输出哈希与视频规格。
- 空帧约定：若两边都没有RGB通道大于25的前景，则该帧记为1；忽略阈值以下的压缩噪点，不平均黑背景。只有一边出现字幕时仍按前景并集扣分。原静态花字指标在空前景下没有定义，这里明确扩展为视频口径。
- 保持相同画布坐标、相同帧号，不按帧平移、缩放、调整亮度或时间。1440版比较时，仅对全部参考帧施加同一个固定缩放。
- 量化通过与观感复核分别记录。关键帧采用两边共享的相同裁切坐标，第三列为放大4倍的像素差异。

## 可复用的渲染方式

`renderer.py` 根据输入字符串生成超采样字体轮廓。静态花字来自签名距离、字形高度及边缘法线的颜色函数。弹入使用位移关键帧和纵向模糊核；声波使用缩放、扩散残影及局部闪亮；冰雪使用逐字显现、水平漂移、扫光与解析粒子。

NPZ 文件保存数值颜色函数的系数，并绑定 SHA-256；它们不保存参考画面、固定文字图像或像素坐标画布。光照函数的横向坐标随输入文字长度变化，描边、轮廓与字腔仍由调用者的字体和文字决定。雪点由高斯光晕与解析星芒生成，位置绑定字幕的相对坐标，使用固定的校准序列（种子0）。运行时不打开参考视频。

更换文字时，雪点按各字符的字体宽度重建字形局部坐标；重复字符的微小位置变化由种子0确定。入场参数按60 fps逐帧保存，渲染不使用随机时钟、逐帧参考图或视频贴片。参数总计约306 MiB，主要是冰雪和声波光照传递函数的浮点系数；本工具侧重95%复刻验收，未做发布包压缩。

`references/` 中的三个视频为用户参考的原样副本，仅供离线校准和比较。工具不会修改它们。

## 回归

```powershell
.\assets\dependencies\python\python.exe -m unittest discover -s tools\subtitle_animation_lab -p test_lab.py
.\assets\dependencies\python\python.exe -m unittest discover -s scripts\packaging\tests -p test_flower_similarity.py
```

测试覆盖：前景指标与现有实现一致、黑色背景不能提高分数、空帧/误显、运行时禁止读取视频、替换文字与确定性、两种尺寸、稳定帧一致、长句适配及错误输入。

离线拟合脚本只用于开发与校准，演示渲染只需要已经保存的参数。离线校准、参考对照和演示工具位于 `tools/`，不进入技能安装；生产运行模块及参数分别位于 `scripts/`、`assets/`，随技能安装。
