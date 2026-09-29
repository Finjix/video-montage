"""Optional, hash-bound packaging of an already completed montage video."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "dependencies/ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = ROOT / "dependencies/models"
SCHEMA = "video-montage-packaging/v1"
PLAN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
DEFAULT_SUBTITLE_FONT_PATH = ROOT / "components/packaging/assets/fonts/WenYue-XinQingNianTi-W8.otf"
SUBTITLE_FONT_SHA256 = "20b03dfe8dc982a19946726fe4acf156f9bb8b45adae8aac22a4a3590bb9a6bf"
SUBTITLE_FONT_FAMILY = "WenYue XinQingNianTi J W8"
SUBTITLE_FONT_POSTSCRIPT = "WenYue_XinQingNianTi_J-W8"
SUBTITLE_REFERENCE = {"canvas_width": 1920, "canvas_height": 3414, "font_size": 12,
                      "color": "#FFDE00", "outline_color": "#000000", "outline_width": 40, "y": -1300}
SUBTITLE_ASS = {"play_res_x": 1920, "play_res_y": 3414, "font_size": 187, "outline": 10,
                "alignment": 5, "position_x": 960, "position_y": 2357}
SUBTITLE_EMPHASIS = {
    "text": "无尽冬日", "capcut_font_size": 13,
    "ass_font_size": round(SUBTITLE_ASS["font_size"] * 13 / SUBTITLE_REFERENCE["font_size"]),
    "effect": "pastel_cyan_extrusion_v3", "gap_font_size": 100, "gap_scale_x": 280,
    "reference_geometry": {"size": 85.5, "x": 131, "y": 52.3125,
        "outer_x": 10.8125, "outer_y": 8.5, "pink_x": 7.6875, "pink_y": 5.1875,
        "white": 3.8625, "depth": 5.5875, "blue_depth_ratio": .55625,
        "expand": .1, "blur": .375, "shadow_border": 7.1875, "shadow_x": 5.625, "shadow_y": .0625},
    "textures": [
        {"slope": .61, "period": 12.25, "phase": 7.46484375, "band": 5.21582},
        {"slope": .605, "period": 12.35, "phase": .9166, "band": 5.11367},
        {"slope": .605, "period": 12.25, "phase": 5.16797, "band": 5.21582},
        {"slope": .615, "period": 12.15, "phase": 9.68203, "band": 5.17324},
    ],
}


def subtitle_filename(plan_id: str) -> str:
    return f"subtitle-{plan_id}.txt"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {result.stderr[-3000:]}")
    return result


def probe(path: Path) -> dict:
    return json.loads(run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout)


def video_spec(path: Path) -> dict:
    info = probe(path)
    videos = [row for row in info["streams"] if row["codec_type"] == "video"]
    audios = [row for row in info["streams"] if row["codec_type"] == "audio"]
    subtitles = [row for row in info["streams"] if row["codec_type"] == "subtitle"]
    video = videos[0] if videos else None
    audio = audios[0] if audios else None
    if video is None or audio is None:
        raise ValueError(f"video and audio streams required: {path}")
    rate_num, rate_den = map(int, video["avg_frame_rate"].split("/"))
    if (rate_num, rate_den) != (60, 1):
        raise ValueError("packaging requires a 60 fps clean video")
    frames = int(video.get("nb_frames") or round(float(info["format"]["duration"]) * 60))
    if frames <= 0:
        raise ValueError("video has no frames")
    return {"frames": frames, "duration": frames / 60, "width": int(video["width"]), "height": int(video["height"]),
            "video_codec": video["codec_name"], "audio_codec": audio["codec_name"],
            "video_streams": len(videos), "audio_streams": len(audios), "subtitle_streams": len(subtitles)}


def packaged_streams_ok(spec: dict) -> bool:
    return (spec.get("video_streams") == 1 and spec.get("audio_streams") == 1
            and spec.get("subtitle_streams") == 0)


def resolve_file(base: Path, value: str, expected_sha: str | None = None) -> dict:
    if not isinstance(value, str) or not value:
        raise ValueError("asset path required")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha(path)
    if expected_sha and digest != expected_sha.lower():
        raise ValueError(f"SHA-256 mismatch: {path}")
    return {"path": str(path), "sha256": digest}


def subtitle_style(config: dict, base: Path) -> dict:
    font_path = config.get("subtitle_font_path", str(DEFAULT_SUBTITLE_FONT_PATH))
    font = resolve_file(base, font_path, SUBTITLE_FONT_SHA256)
    if Path(font["path"]).suffix.lower() != ".otf":
        raise ValueError("subtitle font must be the supplied W8 OTF file")
    return {"font": font, "font_family": SUBTITLE_FONT_FAMILY,
            "capcut_reference": dict(SUBTITLE_REFERENCE), "ass": dict(SUBTITLE_ASS),
            "emphasis": dict(SUBTITLE_EMPHASIS)}


def resolve_overlay(base: Path, value: str, expected_sha: str | None = None) -> dict:
    from PIL import Image
    result = resolve_file(base, value, expected_sha)
    path = Path(result["path"])
    if path.suffix.lower() != ".png":
        raise ValueError(f"overlay must be a transparent PNG: {path}")
    with Image.open(path) as image:
        if image.width * 16 != image.height * 9:
            raise ValueError(f"overlay must have a 9:16 aspect ratio: {path}")
        minimum, maximum = image.convert("RGBA").getchannel("A").getextrema()
        if minimum == 255 or maximum == 0:
            raise ValueError(f"overlay must contain visible content and transparency: {path}")
    return result


def frame_window(value: dict, frames: int, *, default_start: int = 0, default_end: int | None = None) -> tuple[int, int]:
    start = value.get("start_frame", default_start)
    end = value.get("end_frame_exclusive", default_end if default_end is not None else frames)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= frames:
        raise ValueError(f"invalid output-frame interval: {start}, {end}; video has {frames} frames")
    return start, end


def parse_timestamp(value: str) -> int:
    match = SRT_TIME.fullmatch(value)
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value}")
    hour, minute, second, milli = map(int, match.groups())
    if minute >= 60 or second >= 60:
        raise ValueError(f"invalid SRT timestamp: {value}")
    return ((hour * 60 + minute) * 60 + second) * 1000 + milli


def format_timestamp(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, milli = divmod(rem, 1000)
    return f"{hour:02}:{minute:02}:{second:02},{milli:03}"


def parse_srt(path: Path, total_ms: int) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if not raw:
        raise ValueError("subtitle file is empty")
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.rstrip() for line in block.splitlines()]
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2 or " --> " not in lines[0]:
            raise ValueError("invalid SRT cue")
        start_text, end_text = lines[0].split(" --> ", 1)
        start, end = parse_timestamp(start_text), parse_timestamp(end_text)
        caption_lines = [line.strip() for line in lines[1:]]
        if (not caption_lines or any(not line or display_width(line) > 28 for line in caption_lines)
                or not 0 <= start < end <= total_ms):
            raise ValueError("subtitle cue is empty, too wide, or outside video")
        if cues and start < cues[-1]["end_ms"]:
            raise ValueError("subtitle cues overlap or are out of order")
        for line_start, line_end, line in timed_lines(caption_lines, start, end):
            cues.append({"start_ms": line_start, "end_ms": line_end, "text": line})
    return cues


def display_width(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def single_line_chunks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    while text:
        width = 0
        index = 0
        for char in text:
            char_width = display_width(char)
            if width + char_width > 28:
                break
            width += char_width
            index += 1
        word = SUBTITLE_EMPHASIS["text"]
        word_start = text.find(word, max(0, index - len(word) + 1))
        if 0 < word_start < index and word_start + len(word) > index:
            index = word_start
        chunks.append(text[:index].rstrip())
        text = text[index:].lstrip()
    return chunks


def timed_lines(lines: list[str], start_ms: int, end_ms: int) -> list[tuple[int, int, str]]:
    minimum_line_ms = 20  # ASS uses centiseconds; each line must span a 60 fps frame.
    if end_ms - start_ms < minimum_line_ms * len(lines):
        raise ValueError("subtitle cue is too short to show each line")
    total_width = sum(display_width(line) for line in lines)
    flexible_ms = end_ms - start_ms - minimum_line_ms * len(lines)
    result = []
    elapsed_width = 0
    cursor = start_ms
    for index, line in enumerate(lines):
        elapsed_width += display_width(line)
        remaining = len(lines) - index - 1
        boundary = end_ms if not remaining else (start_ms + minimum_line_ms * (index + 1)
            + round(flexible_ms * elapsed_width / total_width))
        result.append((cursor, boundary, line))
        cursor = boundary
    return result


def draft_one(input_path: Path, plan_id: str, output_dir: Path) -> dict:
    if not PLAN_ID.fullmatch(plan_id):
        raise ValueError(f"unsafe plan ID: {plan_id}")
    video_spec(input_path)
    output = output_dir / subtitle_filename(plan_id)
    if output.exists():
        raise FileExistsError(output)
    source_script = ROOT / "components/semantic/scripts/v9_source_asr.py"
    spec = importlib.util.spec_from_file_location("packaging_source_asr", source_script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    model, device, _ = module.build_model("large-v3-turbo", MODEL_ROOT, "auto", "int8", "float16")
    try:
        asr, _ = module.transcribe(model, str(input_path), "zh")
    except Exception as error:
        if device != "cuda" or not any(token in str(error).lower() for token in ("cuda", "out of memory", "memoryerror")):
            raise
        del model
        model, device, _ = module.build_model("large-v3-turbo", MODEL_ROOT, "cpu", "int8", "float16")
        asr, _ = module.transcribe(model, str(input_path), "zh")
    rows = []
    for segment in asr["segments"]:
        words = segment.get("words") or [{"start": segment["start"], "end": segment["end"], "word": segment["text"]}]
        current = []
        for word in words:
            current_text = "".join(item["word"] for item in current)
            candidate = current_text + word["word"]
            title_start = candidate.find(SUBTITLE_EMPHASIS["text"])
            title = SUBTITLE_EMPHASIS["text"]
            cuts_title = (0 <= title_start < len(current_text) < title_start + len(title)
                          or any(current_text.endswith(title[:prefix]) and str(word["word"]).startswith(title[prefix])
                                 for prefix in range(1, len(title))))
            if current and not cuts_title and float(word["end"]) - float(current[0]["start"]) > 3.5:
                rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": "".join(item["word"] for item in current)})
                current = []
            current.append(word)
            if re.search(r"[。！？!?]$", str(word["word"])) and float(word["end"]) - float(current[0]["start"]) >= 0.7:
                rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": "".join(item["word"] for item in current)})
                current = []
        if current:
            rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": "".join(item["word"] for item in current)})
    if not rows:
        raise RuntimeError("Whisper produced no subtitle cues")
    duration_ms = round(video_spec(input_path)["duration"] * 1000)
    blocks = []
    previous_end = 0
    for row in rows:
        start = max(previous_end, round(row["start"] * 1000))
        end = min(duration_ms, max(start + 100, round(row["end"] * 1000)))
        if start >= end:
            continue
        chunks = single_line_chunks(row["text"])
        for chunk_start, chunk_end, chunk in timed_lines(chunks, start, end):
            blocks.append(f"{len(blocks) + 1}\n{format_timestamp(chunk_start)} --> {format_timestamp(chunk_end)}\n{chunk}")
        previous_end = end
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    parse_srt(output, duration_ms)
    return {"plan_id": plan_id, "input_path": str(input_path.resolve()), "input_sha256": sha(input_path),
            "subtitle_txt_path": str(output.resolve()), "subtitle_txt_sha256": sha(output),
            "cue_count": len(blocks), "asr_device": device}


def draft_batch(delivery_manifest: Path, output_dir: Path) -> dict:
    manifest = read(delivery_manifest)
    if manifest.get("schema") != "ffmpeg-controller-delivery/v260928":
        raise ValueError("controller delivery manifest required")
    for row in manifest["results"]:
        path = Path(row["output_path"])
        if not path.is_file() or sha(path) != row.get("output_sha256"):
            raise ValueError(f"clean delivery input changed: {row.get('plan_id')}")
    rows = [draft_one(Path(row["output_path"]), row["plan_id"], output_dir) for row in manifest["results"]]
    report = {"schema": "video-montage-subtitle-draft/v1", "delivery_manifest_path": str(delivery_manifest.resolve()),
              "delivery_manifest_sha256": sha(delivery_manifest), "results": rows}
    atomic(output_dir / "subtitle_draft.json", report)
    return report


def ass_time(ms: int) -> str:
    centis = round(ms / 10)
    hour, rem = divmod(centis, 360_000)
    minute, rem = divmod(rem, 6000)
    second, centi = divmod(rem, 100)
    return f"{hour}:{minute:02}:{second:02}.{centi:02}"


def ass_color(rgb: str) -> str:
    return "&H" + rgb[5:7] + rgb[3:5] + rgb[1:3] + "&"


def diagonal_stripe_clip(ass: dict, emphasis: dict, texture: dict) -> str:
    geometry = emphasis["reference_geometry"]
    scale = emphasis["ass_font_size"] / geometry["size"]
    y0, y1 = -200, 300
    paths = []
    def point(x, y):
        return f"{ass['position_x'] + (x - geometry['x']) * scale:.4f} {ass['position_y'] + (y - geometry['y']) * scale:.4f}"
    for k in range(-100, 120):
        x = k * texture["period"] + texture["phase"] + y0 / texture["slope"]
        x1 = x + texture["band"]
        shift = (y1 - y0) / texture["slope"]
        paths.append(f"m {point(x, y0)} l {point(x1, y0)} {point(x1 + shift, y1)} {point(x + shift, y1)}")
    return "\\clip(1," + " ".join(paths) + ")"


def flower_layers(style: dict, render_width: int) -> list[dict]:
    ass, emphasis = style["ass"], style["emphasis"]
    geometry = emphasis["reference_geometry"]
    scale = emphasis["ass_font_size"] / geometry["size"]
    # ASS border widths are output pixels when ScaledBorderAndShadow is disabled.
    pixel_scale = scale * render_width / ass["play_res_x"]
    layers = []
    def add(dx, dy, border, rgb, fill=None, extra="", glyph=None, border_x=None, border_y=None):
        tags = (f"\\blur{geometry['blur'] * pixel_scale:.4f}\\bord{border * pixel_scale:.4f}"
                f"\\3c{ass_color(rgb)}\\1c{ass_color(fill or rgb)}{extra}")
        if border_x is not None:
            tags += f"\\xbord{border_x * pixel_scale:.4f}\\ybord{border_y * pixel_scale:.4f}"
        layers.append({"dx": dx * scale, "dy": dy * scale, "tags": tags, "glyph": glyph})
    for dy in (0, geometry["depth"]):
        add(geometry["shadow_x"], dy + geometry["shadow_y"], geometry["shadow_border"], "#72D6E9")
    for rgb, edge in [("#FFEA94", "outer"), ("#FAA8D6", "pink")]:
        axes = dict(border_x=geometry[f"{edge}_x"], border_y=geometry[f"{edge}_y"])
        add(0, geometry["depth"], 0, rgb, **axes)
        add(0, 0, 0, rgb, **axes)
    add(0, geometry["depth"], geometry["white"], "#008396")
    add(0, geometry["depth"] * geometry["blue_depth_ratio"], geometry["white"], "#64B6F7")
    add(0, 0, geometry["white"], "#E7F8F4", "#26C0DD")
    add(0, 0, geometry["expand"], "#26C0DD")
    for glyph, texture in enumerate(emphasis["textures"]):
        add(0, 0, geometry["expand"], "#72D6E9", extra=diagonal_stripe_clip(ass, emphasis, texture), glyph=glyph)
    return layers


def pad_flower_word(clean: str, word: str, ass: dict, emphasis: dict) -> str:
    gap = f"{{\\fs{emphasis['gap_font_size']}\\fscx{emphasis['gap_scale_x']}}}\\h{{\\fs{ass['font_size']}\\fscx100}}"
    lines = []
    for line in clean.split("\\N"):
        tokens = [token for token in re.split(f"({re.escape(word)})", line) if token]
        padded = []
        for index, token in enumerate(tokens):
            if index and (token == word or tokens[index - 1] == word):
                padded.append(gap)
            padded.append(token)
        lines.append("".join(padded))
    return "\\N".join(lines)


def write_ass(path: Path, cues: list[dict], style: dict, *, render_width: int = 1440) -> None:
    if any("\n" in cue["text"] or "\r" in cue["text"] for cue in cues):
        raise ValueError("ASS subtitle cues must contain one line")
    ass = style["ass"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ass['play_res_x']}
PlayResY: {ass['play_res_y']}
WrapStyle: 2
ScaledBorderAndShadow: no

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font_family']},{ass['font_size']},&H0000DEFF,&H0000DEFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{ass['outline']},0,{ass['alignment']},0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    position = f"{{\\pos({ass['position_x']},{ass['position_y']})}}"
    emphasis = style["emphasis"]
    word = emphasis["text"]
    enlarged = f"{{\\fs{emphasis['ass_font_size']}\\alpha&HFF&}}{word}{{\\fs{ass['font_size']}\\alpha&H00&}}"
    effect_layers = flower_layers(style, render_width)
    for cue in cues:
        clean = cue["text"].replace("{", "（").replace("}", "）").replace("\\", "／").replace("\n", "\\N")
        timing = f"{ass_time(cue['start_ms'])},{ass_time(cue['end_ms'])},Default,,0,0,0,,"
        prefix = timing + position
        if word not in clean:
            lines.append(f"Dialogue: 0,{prefix}{clean}")
            continue
        clean = pad_flower_word(clean, word, ass, emphasis)
        lines.append(f"Dialogue: 0,{prefix}{clean.replace(word, enlarged)}")
        pieces = clean.split(word)
        for layer_number, layer in enumerate(effect_layers, start=1):
            painted_word = word if layer["glyph"] is None else "".join(
                ("{\\alpha&H00&}" if i == layer["glyph"] else "{\\alpha&HFF&}") + ch for i, ch in enumerate(word))
            visible = f"{{\\fs{emphasis['ass_font_size']}\\alpha&H00&{layer['tags']}}}{painted_word}{{\\fs{ass['font_size']}\\alpha&HFF&}}"
            layer_text = "{\\alpha&HFF&}" + visible.join(pieces)
            layer_position = f"{{\\pos({ass['position_x'] + layer['dx']:.4f},{ass['position_y'] + layer['dy']:.4f})}}"
            lines.append(f"Dialogue: {layer_number},{timing}{layer_position}{layer_text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")


def require_subtitle_font(log: str) -> None:
    request = f"fontselect: ({SUBTITLE_FONT_FAMILY},"
    selected = f"-> {SUBTITLE_FONT_POSTSCRIPT},"
    if not any(request in line and selected in line for line in log.splitlines()):
        raise RuntimeError(f"FFmpeg did not select the supplied subtitle font: {SUBTITLE_FONT_FAMILY}")


def prepared_rows(config_path: Path, expected_inputs: dict[str, str] | None = None) -> list[dict]:
    config = read(config_path)
    if config.get("schema") != SCHEMA or not isinstance(config.get("outputs"), list) or not config["outputs"]:
        raise ValueError(f"{SCHEMA} with nonempty outputs required")
    base = config_path.resolve().parent
    style = subtitle_style(config, base)
    rows = []
    for row in config["outputs"]:
        plan_id = row.get("plan_id")
        if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id) or any(item["plan_id"] == plan_id for item in rows):
            raise ValueError(f"unsafe or duplicate plan ID: {plan_id}")
        source = resolve_file(base, row.get("input_path"), row.get("input_sha256"))
        if expected_inputs is not None and expected_inputs.get(plan_id) != source["sha256"]:
            raise ValueError(f"clean delivery input mismatch: {plan_id}")
        spec = video_spec(Path(source["path"]))
        if (spec["width"], spec["height"]) != (1440, 2560):
            raise ValueError("clean video must be 1440x2560")
        if row.get("subtitle_txt") and row.get("subtitle_srt"):
            raise ValueError("choose one subtitle file")
        subtitles = resolve_file(base, row.get("subtitle_txt") or row.get("subtitle_srt"), row.get("subtitle_sha256"))
        cues = parse_srt(Path(subtitles["path"]), round(spec["duration"] * 1000))
        prepared = {"plan_id": plan_id, "input": source, "spec": spec, "subtitles": {**subtitles, "cue_count": len(cues)}, "cues": cues,
                    "subtitle_style": style,
                    "nameplate": None, "text_pins": [], "disclaimer": None, "bgm": None}
        if row.get("nameplate"):
            item = row["nameplate"]
            start, end = frame_window(item, spec["frames"], default_end=min(192, spec["frames"]))
            prepared["nameplate"] = {**resolve_overlay(base, item.get("path"), item.get("sha256")), "start_frame": start, "end_frame_exclusive": end}
        for item in row.get("text_pins", []):
            if "start_frame" not in item or "end_frame_exclusive" not in item:
                raise ValueError("text pin interval is required")
            start, end = frame_window(item, spec["frames"])
            prepared["text_pins"].append({**resolve_overlay(base, item.get("path"), item.get("sha256")), "start_frame": start, "end_frame_exclusive": end})
        if row.get("disclaimer"):
            item = row["disclaimer"]
            prepared["disclaimer"] = resolve_overlay(base, item.get("path"), item.get("sha256"))
        if row.get("bgm"):
            item = row["bgm"]
            gain = item.get("gain_db", -18)
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not -60 <= gain <= 0:
                raise ValueError("BGM gain must be between -60 and 0 dB")
            prepared["bgm"] = {**resolve_file(base, item.get("path"), item.get("sha256")), "gain_db": float(gain)}
        rows.append(prepared)
    if expected_inputs is not None and set(expected_inputs) != {row["plan_id"] for row in rows}:
        raise ValueError("packaging configuration must cover the complete delivery")
    return rows


def render_one(row: dict, output_dir: Path) -> dict:
    plan_id = row["plan_id"]
    output = output_dir / f"{plan_id}.mp4"
    partial = output_dir / f"{plan_id}.partial.mp4"
    ass = output_dir / f"{plan_id}.ass"
    if output.exists() or partial.exists() or ass.exists():
        raise FileExistsError(f"refusing overwrite: {output}")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=f".{plan_id}.subtitle-font-", dir=output_dir) as font_directory:
            local_font = Path(font_directory) / "subtitle.otf"
            shutil.copyfile(row["subtitle_style"]["font"]["path"], local_font)
            if sha(local_font) != row["subtitle_style"]["font"]["sha256"]:
                raise ValueError("subtitle font changed before rendering")
            write_ass(ass, row["cues"], row["subtitle_style"])
            command = [str(FFMPEG), "-hide_banner", "-loglevel", "verbose", "-nostdin", "-y", "-i", row["input"]["path"]]
            filters = []
            current = "[0:v]"
            index = 1
            layers = ([row["nameplate"]] if row["nameplate"] else []) + row["text_pins"] + ([row["disclaimer"]] if row["disclaimer"] else [])
            for layer in layers:
                command += ["-loop", "1", "-framerate", "60", "-i", layer["path"]]
                label = f"v{index}"
                scale = f"[{index}:v]scale=1440:2560:flags=lanczos,format=rgba[layer{index}]"
                filters.append(scale)
                if "start_frame" in layer:
                    start, end = layer["start_frame"], layer["end_frame_exclusive"]
                    enable = f":enable='gte(n,{start})*lt(n,{end})'"
                else:
                    enable = ""
                filters.append(f"{current}[layer{index}]overlay=0:0:format=auto{enable}[{label}]")
                current = f"[{label}]"
                index += 1
            filters.append(f"{current}ass=filename={ass.name}:fontsdir={Path(font_directory).name},format=yuv420p[vout]")
            if row["bgm"]:
                command += ["-stream_loop", "-1", "-i", row["bgm"]["path"]]
                filters.append(f"[{index}:a]volume={row['bgm']['gain_db']}dB[bgm]")
                filters.append("[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
            else:
                filters.append("[0:a]anull[aout]")
            frames = row["spec"]["frames"]
            command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-frames:v", str(frames),
                        "-r", "60", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                        "-t", f"{frames / 60:.6f}", "-movflags", "+faststart", str(partial)]
            completed = run(command, cwd=output_dir)
            require_subtitle_font(completed.stderr)
            spec = video_spec(partial)
            if (spec["frames"] != frames or (spec["width"], spec["height"]) != (1440, 2560)
                    or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac" or not packaged_streams_ok(spec)):
                raise RuntimeError(f"packaged video specification mismatch: {spec}")
            os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        ass.unlink(missing_ok=True)
    return {"plan_id": plan_id, "input": row["input"], "input_frames": frames, "output_path": str(output.resolve()),
            "output_sha256": sha(output), "output_spec": spec, "subtitles": row["subtitles"],
            "nameplate": row["nameplate"], "text_pins": row["text_pins"], "disclaimer": row["disclaimer"], "bgm": row["bgm"]}


def render(config_path: Path, output_dir: Path, manifest_path: Path, delivery_manifest: Path | None = None,
           controller_validation: Path | None = None) -> dict:
    if manifest_path.exists():
        raise FileExistsError(manifest_path)
    if bool(delivery_manifest) != bool(controller_validation):
        raise ValueError("complete montage packaging requires both clean delivery and controller validation")
    expected = None
    if delivery_manifest:
        delivery = read(delivery_manifest)
        if delivery.get("schema") != "ffmpeg-controller-delivery/v260928":
            raise ValueError("controller delivery manifest required")
        validation = read(controller_validation)
        if (validation.get("schema") != "ffmpeg-controller-validation/v260928"
                or validation.get("decision") != "pass"
                or Path(str(validation.get("manifest_path") or "")).resolve() != delivery_manifest.resolve()
                or validation.get("manifest_sha256") != sha(delivery_manifest)):
            raise ValueError("passing controller validation bound to clean delivery required")
        expected = {item["plan_id"]: item["output_sha256"] for item in delivery["results"]}
    rows = prepared_rows(config_path, expected)
    snapshots = [output_dir / subtitle_filename(row["plan_id"]) for row in rows]
    config_snapshot = output_dir / config_path.name
    copies = [(config_path, config_snapshot)] + [
        (Path(row["subtitles"]["path"]), snapshot) for row, snapshot in zip(rows, snapshots)
    ]
    reserved = {}
    for path, label in [(manifest_path, "manifest"), *[(target, "snapshot") for _, target in copies],
                        *[(output_dir / f"{row['plan_id']}{suffix}", "render")
                          for row in rows for suffix in (".mp4", ".partial.mp4", ".ass")]]:
        resolved = path.resolve()
        if resolved in reserved:
            raise ValueError(f"packaging paths collide: {reserved[resolved]} and {label}: {resolved}")
        reserved[resolved] = label
    for source, target in copies:
        if source.resolve() != target.resolve() and target.exists():
            raise FileExistsError(f"refusing to overwrite packaging file: {target}")
    results = [render_one(row, output_dir) for row in rows]
    for row, result, snapshot in zip(rows, results, snapshots):
        if Path(row["subtitles"]["path"]).resolve() != snapshot.resolve():
            snapshot.write_bytes(Path(row["subtitles"]["path"]).read_bytes())
        result["subtitle_snapshot"] = {"path": str(snapshot.resolve()), "sha256": sha(snapshot)}
    if config_path.resolve() != config_snapshot.resolve():
        config_snapshot.write_bytes(config_path.read_bytes())
    manifest = {"schema": "video-montage-packaging-delivery/v1", "mode": "complete_montage" if delivery_manifest else "standalone_test",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "subtitle_style": rows[0]["subtitle_style"],
                "config_path": str(config_path.resolve()), "config_sha256": sha(config_path),
                "config_snapshot_path": str(config_snapshot.resolve()), "config_snapshot_sha256": sha(config_snapshot),
                "clean_delivery_path": str(delivery_manifest.resolve()) if delivery_manifest else None,
                "clean_delivery_sha256": sha(delivery_manifest) if delivery_manifest else None,
                "controller_validation_path": str(controller_validation.resolve()) if controller_validation else None,
                "controller_validation_sha256": sha(controller_validation) if controller_validation else None,
                "output_count": len(results), "results": results}
    atomic(manifest_path, manifest)
    return manifest


def reburn(previous_manifest_path: Path, plan_id: str, subtitle_txt: Path, output_dir: Path,
           manifest_path: Path) -> dict:
    """Render a fresh version from the clean inputs and recorded overlay choices."""
    previous = read(previous_manifest_path)
    if previous.get("schema") != "video-montage-packaging-delivery/v1":
        raise ValueError("previous packaging manifest required")
    results = previous.get("results", [])
    if not results or plan_id not in {row.get("plan_id") for row in results}:
        raise ValueError(f"unknown plan ID in previous packaging: {plan_id}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing non-empty reburn output directory: {output_dir}")
    if manifest_path.exists():
        raise FileExistsError(f"reburn manifest must be a new file: {manifest_path}")
    edited = resolve_file(Path.cwd(), str(subtitle_txt))
    rows = []
    for old in results:
        selected = old["plan_id"] == plan_id
        subtitle = edited if selected else old["subtitle_snapshot"]
        row = {"plan_id": old["plan_id"], "input_path": old["input"]["path"],
               "input_sha256": old["input"]["sha256"], "subtitle_txt": subtitle["path"],
               "subtitle_sha256": subtitle["sha256"]}
        for key in ("nameplate", "disclaimer", "bgm"):
            if old.get(key):
                row[key] = old[key]
        if old.get("text_pins"):
            row["text_pins"] = old["text_pins"]
        rows.append(row)
    if previous.get("subtitle_style") is not None:
        font = previous["subtitle_style"]["font"]
        if font["sha256"] != SUBTITLE_FONT_SHA256:
            raise ValueError("previous subtitle font differs from the bundled W8 font")
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "reburn_config.json"
    config = {"schema": SCHEMA, "outputs": rows}
    atomic(config_path, config)
    delivery = Path(previous["clean_delivery_path"]) if previous.get("clean_delivery_path") else None
    controller = Path(previous["controller_validation_path"]) if previous.get("controller_validation_path") else None
    result = render(config_path, output_dir, manifest_path, delivery, controller)
    result["reburn_source"] = {"manifest_path": str(previous_manifest_path.resolve()),
                               "manifest_sha256": sha(previous_manifest_path), "edited_plan_id": plan_id}
    atomic(manifest_path, result)
    return result


def pcm_stats(path: Path) -> dict:
    import numpy as np
    command = [str(FFMPEG), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "2", "-ar", "48000", "-f", "s16le", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    clipped = count = 0
    peak = 0
    assert process.stdout is not None
    while block := process.stdout.read(2 * 48000):
        values = np.frombuffer(block, dtype="<i2").astype(np.int32)
        absolute = np.abs(values)
        clipped += int(np.count_nonzero(absolute >= 32760))
        peak = max(peak, int(absolute.max(initial=0)))
        count += len(values)
    stderr = process.stderr.read() if process.stderr else b""
    if process.wait():
        raise RuntimeError(f"audio decode failed: {stderr[-1000:]!r}")
    return {"samples_per_channel": count // 2, "channels_checked": 2, "clipped_samples": clipped, "peak_fraction": round(peak / 32768, 6)}


def validate(manifest_path: Path, report_path: Path, review_path: Path | None = None,
             authority_path: Path | None = None) -> dict:
    manifest = read(manifest_path)
    failures = []
    if bool(review_path) != bool(authority_path):
        raise ValueError("review and independent review authority must be provided together")
    review = read(review_path) if review_path else None
    authority = read(authority_path) if authority_path else None
    reviews = {}
    if review is not None:
        reviewer = authority.get("reviewer") if isinstance(authority.get("reviewer"), dict) else {}
        if (authority.get("schema") != "video-montage-independent-packaging-review-authority/v1"
                or authority.get("decision") != "authorized"
                or reviewer.get("role") != "independent_packaging_reviewer"
                or not reviewer.get("review_id")
                or review.get("reviewer_id") != reviewer.get("review_id")
                or review.get("authority_sha256") != sha(authority_path)):
            failures.append("review_authority")
        if (review.get("schema") != "video-montage-packaging-review/v1"
                or review.get("manifest_sha256") != sha(manifest_path)
                or review.get("reviewer_role") != "independent_packaging_reviewer"
                or not review.get("reviewer_id")):
            failures.append("review_binding")
        review_rows = review.get("results")
        if (not isinstance(review_rows, list)
                or any(not isinstance(row, dict) or not isinstance(row.get("plan_id"), str) for row in review_rows)):
            failures.append("review_scope")
        else:
            reviews = {row["plan_id"]: row for row in review_rows}
            if len(reviews) != len(review_rows):
                failures.append("review_scope")
    if manifest.get("schema") != "video-montage-packaging-delivery/v1":
        failures.append("schema")
    style = manifest.get("subtitle_style")
    if style is not None:
        font = style.get("font") if isinstance(style, dict) else None
        if not isinstance(font, dict) or not font.get("path") or not font.get("sha256"):
            failures.append("subtitle_font_record")
        else:
            font_path = Path(font["path"])
            if not font_path.is_file() or sha(font_path) != font["sha256"]:
                failures.append("subtitle_font_changed")
    source = manifest.get("reburn_source")
    if source:
        previous_path = Path(str(source.get("manifest_path") or ""))
        if not previous_path.is_file() or sha(previous_path) != source.get("manifest_sha256"):
            failures.append("reburn_source_changed")
    config_path = Path(str(manifest.get("config_path") or ""))
    if not config_path.is_file() or sha(config_path) != manifest.get("config_sha256"):
        failures.append("config_changed")
    config_snapshot = Path(str(manifest.get("config_snapshot_path") or ""))
    if not config_snapshot.is_file() or sha(config_snapshot) != manifest.get("config_snapshot_sha256") or manifest.get("config_snapshot_sha256") != manifest.get("config_sha256"):
        failures.append("config_snapshot_changed")
    clean_path = manifest.get("clean_delivery_path")
    if clean_path:
        clean = Path(clean_path)
        if not clean.is_file() or sha(clean) != manifest.get("clean_delivery_sha256"):
            failures.append("clean_delivery_changed")
        controller = Path(str(manifest.get("controller_validation_path") or ""))
        if not controller.is_file() or sha(controller) != manifest.get("controller_validation_sha256"):
            failures.append("controller_validation_changed")
        elif (read(controller).get("decision") != "pass"
              or read(controller).get("manifest_sha256") != manifest.get("clean_delivery_sha256")):
            failures.append("controller_validation_binding")
    checks = []
    results = manifest.get("results", [])
    if manifest.get("output_count") != len(results) or not results:
        failures.append("result_scope")
    for row in results:
        plan_id = row.get("plan_id")
        changes = []
        for key in ("input", "subtitles", "subtitle_snapshot", "ass", "nameplate", "disclaimer", "bgm"):
            item = row.get(key)
            if item:
                path = Path(item["path"])
                if not path.is_file() or sha(path) != item["sha256"]:
                    changes.append(f"{key}_changed")
        if row.get("subtitle_snapshot", {}).get("sha256") != row.get("subtitles", {}).get("sha256"):
            changes.append("subtitle_snapshot_mismatch")
        for item in row.get("text_pins", []):
            path = Path(item["path"])
            if not path.is_file() or sha(path) != item["sha256"]:
                changes.append("text_pin_changed")
        output = Path(row["output_path"])
        if not output.is_file() or sha(output) != row.get("output_sha256"):
            changes.append("output_changed")
        else:
            try:
                spec = video_spec(output)
                if (spec["frames"] != row["input_frames"] or (spec["width"], spec["height"]) != (1440, 2560)
                        or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac"
                        or not packaged_streams_ok(spec)):
                    changes.append("output_spec")
                run([str(FFMPEG), "-v", "error", "-i", str(output), "-f", "null", "NUL"])
                audio = pcm_stats(output)
                if audio["clipped_samples"] > 3:
                    changes.append("audio_clipping")
            except Exception as exc:
                changes.append(f"decode:{exc}")
                audio = None
        finding = reviews.get(plan_id)
        if review is not None and (not finding or finding.get("output_sha256") != row.get("output_sha256")
                                   or finding.get("visual_pass") is not True or finding.get("audio_pass") is not True
                                   or finding.get("subtitle_pass") is not True or finding.get("overlay_pass") is not True):
            changes.append("independent_review")
        checks.append({"plan_id": plan_id, "decision": "pass" if not changes else "reject", "failures": changes,
                       "audio": audio if output.is_file() and "output_changed" not in changes else None,
                       "visual_review": {"subtitle_cues": row["subtitles"]["cue_count"], "nameplate": bool(row.get("nameplate")),
                                         "text_pins": len(row.get("text_pins", [])), "disclaimer": bool(row.get("disclaimer")),
                                         "status": "pass" if finding and finding.get("visual_pass") is True else "pending_review"},
                       "sound_review": {"bgm": bool(row.get("bgm")), "status": "pass" if finding and finding.get("audio_pass") is True else "pending_review"}})
        failures += [f"{plan_id}:{change}" for change in changes]
    if review is not None and set(reviews) != {row.get("plan_id") for row in results} and "review_scope" not in failures:
        failures.append("review_scope")
    decision = "reject" if failures else "pass" if review is not None else "technical_pass_pending_review"
    report = {"schema": "video-montage-packaging-validation/v1", "decision": decision,
              "manifest_path": str(manifest_path.resolve()), "manifest_sha256": sha(manifest_path),
              "review_path": str(review_path.resolve()) if review_path else None,
              "review_sha256": sha(review_path) if review_path else None,
              "review_authority_path": str(authority_path.resolve()) if authority_path else None,
              "review_authority_sha256": sha(authority_path) if authority_path else None,
              "independent_review": "pass" if review is not None and not any("review" in failure for failure in failures) else "pending_or_rejected",
              "failures": failures, "results": checks}
    atomic(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    draft = sub.add_parser("draft")
    draft.add_argument("--input", type=Path, required=True)
    draft.add_argument("--plan-id", required=True)
    draft.add_argument("--output-dir", type=Path, required=True)
    batch = sub.add_parser("draft-batch")
    batch.add_argument("--delivery-manifest", type=Path, required=True)
    batch.add_argument("--output-dir", type=Path, required=True)
    render_cmd = sub.add_parser("render")
    render_cmd.add_argument("--config", type=Path, required=True)
    render_cmd.add_argument("--output-dir", type=Path, required=True)
    render_cmd.add_argument("--manifest", type=Path, required=True)
    render_cmd.add_argument("--delivery-manifest", type=Path)
    render_cmd.add_argument("--controller-validation", type=Path)
    reburn_cmd = sub.add_parser("reburn")
    reburn_cmd.add_argument("--previous-manifest", type=Path, required=True)
    reburn_cmd.add_argument("--plan-id", required=True)
    reburn_cmd.add_argument("--subtitle-txt", "--subtitle-srt", dest="subtitle_txt", type=Path, required=True)
    reburn_cmd.add_argument("--output-dir", type=Path, required=True)
    reburn_cmd.add_argument("--manifest", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--report", type=Path, required=True)
    check.add_argument("--review", type=Path)
    check.add_argument("--review-authority", type=Path)
    args = parser.parse_args()
    if args.command == "draft":
        result = draft_one(args.input.resolve(), args.plan_id, args.output_dir.resolve())
    elif args.command == "draft-batch":
        result = draft_batch(args.delivery_manifest.resolve(), args.output_dir.resolve())
    elif args.command == "render":
        result = render(args.config.resolve(), args.output_dir.resolve(), args.manifest.resolve(),
                        args.delivery_manifest.resolve() if args.delivery_manifest else None,
                        args.controller_validation.resolve() if args.controller_validation else None)
    elif args.command == "reburn":
        result = reburn(args.previous_manifest.resolve(), args.plan_id, args.subtitle_txt.resolve(),
                        args.output_dir.resolve(), args.manifest.resolve())
    else:
        result = validate(args.manifest.resolve(), args.report.resolve(),
                          args.review.resolve() if args.review else None,
                          args.review_authority.resolve() if args.review_authority else None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("decision") != "reject" else 2


if __name__ == "__main__":
    raise SystemExit(main())
