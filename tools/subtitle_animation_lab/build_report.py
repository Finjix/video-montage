"""Build a local, portable video gallery from decoded-frame acceptance reports."""
from __future__ import annotations

from pathlib import Path
import argparse
import html
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import EFFECTS, read_json


def build(output: Path) -> Path:
    cards, rows = [], []
    for effect, label in EFFECTS.items():
        report = read_json(output / "对照" / label / "comparison.json")
        low = report["minimum"]
        entry, stable = report["entry_mean"] * 100, report["stable_mean"] * 100
        rows.append(f"<tr><td>{label}</td><td>{entry:.3f}%</td><td>{stable:.3f}%</td>"
                    f"<td>{low['ssim'] * 100:.3f}% · 第{low['frame']}帧</td><td>"
                    f"{'通过' if report['quantitative_pass'] else '未通过'}</td></tr>")
        links = " · ".join(f'<a href="对照/{label}/{name}">{caption}</a>' for name, caption in [
            ("同步对照.mp4", "原速对照"), ("同步对照_四倍慢放.mp4", "四倍慢放"),
            ("关键帧对照.jpg", "关键帧"), ("frames.csv", "逐帧CSV"), ("comparison.json", "完整报告")])
        cards.append(f'<article><h2>{label}</h2><video data-label="{label}" src="演示/{label}.mp4" controls muted loop playsinline></video>'
                     f'<p>{links}</p></article>')
    samples = []
    replacements = output / "替换文字" / "验证报告.json"
    if replacements.exists():
        for key, item in read_json(replacements).items():
            effect, kind = key.split("/")
            label = EFFECTS[effect]
            samples.append(f'<li>{label} / {kind} / {html.escape(item["text"])}：'
                           f'<a href="替换文字/{kind}/{label}.mp4">播放</a></li>')
    sizes = " · ".join(f'<a href="1440版/{label}.mp4">{label}</a>' for label in EFFECTS.values())
    document = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>三种字幕入场动画 · 独立试验验收</title>
<style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#10131a;color:#e8ebf2}}
body{{max-width:1200px;margin:35px auto;padding:0 22px;line-height:1.7}}h1{{font-size:27px}}
a{{color:#9bc9ff}}table{{border-collapse:collapse;width:100%;margin:22px 0}}
th,td{{padding:10px 14px;border-bottom:1px solid #303849;text-align:left}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}}article{{background:#1b2130;border-radius:12px;padding:16px}}
article h2{{margin-top:0;font-size:21px}}video{{display:block;width:100%;height:440px;background:black;object-fit:contain}}
article p{{font-size:14px}}button,select{{background:#263b5d;color:white;border:1px solid #55729b;border-radius:6px;padding:8px 16px}}
pre{{white-space:pre-wrap;background:#1b2130;padding:14px;border-radius:8px;font-size:13px}}
.small{{color:#aab7cb;font-size:14px}}@media(max-width:750px){{.cards{{grid-template-columns:1fr}}video{{height:550px}}}}
</style><h1>三种字幕入场动画独立试验</h1>
<p>“无尽冬日” · W8 / 冰2 · 1920×3414 · 60 fps · 60帧 · 无音轨。动画在前半秒完成，第30帧起稳定。</p>
<p><button id="play">同步重播</button> <select id="mode" aria-label="预览内容"><option value="demo">演示</option><option value="compare">参考与复刻对照</option></select> <select id="speed" aria-label="播放速度"><option value="1">原速</option><option value="0.25">四倍慢放</option></select></p>
<p><label>暂停检查帧 <input id="frame" type="range" min="0" max="59" value="0"><output id="frame-label">0</output></label></p>
<section class="cards">{''.join(cards)}</section>
<h2>编码后验收</h2><table><thead><tr><th>效果</th><th>0–29帧平均</th><th>30–59帧平均</th><th>最低分</th><th>两段≥95%</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="small">逐帧解码最终MP4；RGB SSIM / 11×11高斯窗 / σ=1.5 / 前景并集外扩2像素。黑背景不参与得分；同帧号、同画布坐标，无逐帧对齐。95%是两段平均值要求，最低帧分数另列。原速和慢放对照左边为参考、右边为复刻。</p>
<p><a href="验收汇总.json">验收汇总</a> · <a href="观感复核.json">观感复核记录</a></p>
<h2>1440×2560</h2><p>{sizes} · <a href="1440版/规格验证.json">规格及相似度验证</a></p>
<h2>替换文字</h2><ul>{''.join(samples)}</ul><p><a href="替换文字/排版对照.jpg">第12、24、40帧排版对照</a> · <a href="替换文字/验证报告.json">规格、确定性及稳定帧报告</a></p>
<h2>独立调用</h2>
<pre>.\\assets\\dependencies\\python\\python.exe tools\\subtitle_animation_lab\\cli.py render --effect ice_drift --text 'ABC冰雪2026' --output-dir work\\动画测试</pre>
<p class="small">代码位于 tools/subtitle_animation_lab/。本试验没有接入成片包装、字幕选择、自动任务或整片倍速流程。</p>
<script>
const videos=[...document.querySelectorAll('video')];
document.querySelector('#play').onclick=()=>{{videos.forEach(v=>{{v.pause();v.currentTime=0;v.playbackRate=Number(document.querySelector('#speed').value);v.play();}});}};
document.querySelector('#speed').onchange=e=>videos.forEach(v=>v.playbackRate=Number(e.target.value));
document.querySelector('#mode').onchange=e=>videos.forEach(v=>{{v.pause();v.src=e.target.value==='demo'?`演示/${{v.dataset.label}}.mp4`:`对照/${{v.dataset.label}}/同步对照.mp4`;v.load();}});
document.querySelector('#frame').oninput=e=>{{document.querySelector('#frame-label').value=e.target.value;videos.forEach(v=>{{v.pause();v.currentTime=(Number(e.target.value)+.5)/60;}});}};
</script></html>'''
    target = output / "预览与验收.html"
    target.write_text(document, encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(build(args.output_dir.resolve()))
