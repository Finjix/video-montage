"""Optional, hash-bound packaging of an already completed montage video."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery_files import publish_files
import flower_effects
import subtitle_fonts
import packaging_design
import design_renderer
FFMPEG = ROOT / "assets/dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "assets/dependencies/ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = ROOT / "assets/dependencies/models"
SCHEMA = "video-montage-packaging/v1"
FINAL_SPEED = 1.2


def final_frames(input_frames: int) -> int:
    # Round up to a complete output frame so the last source frame is retained.
    return (input_frames * 5 + 5) // 6


def delivery_speed(row: dict) -> float:
    speed = row.get("final_speed", 1.0)  # Existing manifests remain readable.
    if speed not in (1.0, FINAL_SPEED):
        raise ValueError("invalid final delivery speed")
    return speed


def delivery_cues(row: dict) -> list[dict]:
    cues = parse_srt(Path(row["subtitles"]["path"]), round(row["input_frames"] / 60 * 1000))
    speed = delivery_speed(row)
    return [{**cue, "start_ms": round(cue["start_ms"] / speed),
             "end_ms": round(cue["end_ms"] / speed)} for cue in cues]


def speed_clean(input_path: Path, output: Path) -> dict:
    """Create a separate accelerated delivery; never overwrite the clean input."""
    if input_path.resolve() == output.resolve():
        raise ValueError("speed output must be separate from clean input")
    source_hash = sha(input_path)
    source_spec = video_spec(input_path)
    frames = final_frames(source_spec["frames"])
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".partial.mp4")
    try:
        run([str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(input_path),
             "-map", "0:v:0", "-map", "0:a:0", "-vf", "setpts=(PTS-STARTPTS)/1.2,fps=60:round=up",
             "-af", "asetpts=PTS-STARTPTS,atempo=1.2,apad",
             "-frames:v", str(frames), "-r", "60", "-fps_mode", "cfr",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-t", f"{frames / 60:.9f}", "-movflags", "+faststart", str(partial)])
        spec = video_spec(partial)
        if (spec["frames"] != frames or not packaged_streams_ok(spec)
                or (spec["width"], spec["height"]) != (1440, 2560)
                or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac"):
            raise RuntimeError("accelerated clean specification mismatch")
        if sha(input_path) != source_hash:
            raise ValueError("clean input changed during speed encoding")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return {"input": {"path": str(input_path.resolve()), "sha256": source_hash},
            "input_frames": source_spec["frames"], "final_speed": FINAL_SPEED,
            "output_frames": frames, "output_spec": spec,
            "output_path": str(output.resolve()), "output_sha256": sha(output)}
PLAN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
DEFAULT_SUBTITLE_FONT_PATH = ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf"
SUBTITLE_FONT_SHA256 = "20b03dfe8dc982a19946726fe4acf156f9bb8b45adae8aac22a4a3590bb9a6bf"
SUBTITLE_FONT_FAMILY = "WenYue XinQingNianTi J W8"
SUBTITLE_FONT_POSTSCRIPT = "WenYue_XinQingNianTi_J-W8"
SUBTITLE_REFERENCE = {"canvas_width": 1920, "canvas_height": 3414, "font_size": 8, "scale_percent": 164,
                      "color": "#FFDE00", "outline_color": "#000000", "outline_width": subtitle_fonts.EDITOR_OUTLINE_WIDTH, "y": -1300}
# Preserve the calibrated CapCut font/stroke conversion, applying its text scale.
# CapCut Y is upward-positive and uses twice the reference canvas pixel offset.
SUBTITLE_ASS = {"play_res_x": 1920, "play_res_y": 3414,
                "font_size": round(187 / 12 * SUBTITLE_REFERENCE["font_size"] * SUBTITLE_REFERENCE["scale_percent"] / 100),
                "outline": SUBTITLE_REFERENCE["outline_width"] / 4 * SUBTITLE_REFERENCE["scale_percent"] / 100,
                "alignment": 5, "position_x": 960,
                "position_y": (SUBTITLE_REFERENCE["canvas_height"] - SUBTITLE_REFERENCE["y"]) // 2}
SUBTITLE_EMPHASIS = {
    "text": "无尽冬日", "capcut_font_size": 9,
    "ass_font_size": round(187 / 12 * 9 * SUBTITLE_REFERENCE["scale_percent"] / 100),
    "effect": "dynamic_font_shader_v2", "selection": "random_ice",
    "gap_font_size": round(100 * SUBTITLE_REFERENCE["scale_percent"] / 100), "gap_scale_x": 280,
}


OUTPUT_NAME = re.compile(r"自动化混剪_\d{8}_\d{6}(?:_\d{6})?\Z")


def runtime_directory(root: Path) -> Path:
    """Keep each delivery's audit/state files in its temporary directory."""
    root = root.resolve()
    return root / "临时文件"


def delivery_category(root: Path, kind: str) -> Path:
    """Keep existing jobs readable while using the required layout for new jobs."""
    if OUTPUT_NAME.fullmatch(root.name):
        if kind in {"manifests", "reports"}:
            return runtime_directory(root) / {"manifests": "manifests", "reports": "reports"}[kind]
        return root / {"video": "成片", "clean": "混剪（无包装）",
                       "subtitles": "字幕", "config": "临时文件/config"}[kind]
    return root if kind == "video" else root / kind


def ensure_delivery_layout(root: Path) -> None:
    if not OUTPUT_NAME.fullmatch(root.name):
        return
    for kind in ("video", "clean", "subtitles", "config", "manifests"):
        delivery_category(root, kind).mkdir(parents=True, exist_ok=True)
    marker = delivery_category(root, "subtitles") / "修改字幕后让AI重新烧录"
    generated_prefix = "修改本目录 subtitle-*.txt 的文字或时间码后，把本输出目录交给 AI，要求重新烧录。"
    if not marker.exists() or marker.read_text(encoding="utf-8").startswith(generated_prefix):
        marker.write_text(generated_prefix + "\n"
                          "保留混剪（无包装）和临时文件/config，AI 使用配置重新烧录并校验。\n"
                          "重新烧录在本目录覆盖成片、字幕和配置，不新建交付文件夹；通过校验后清理运行记录和证据，只保留二次修改必要文件。\n", encoding="utf-8")


def invalidate_delivery_receipts(root: Path) -> None:
    """Remove only known completion receipts that overwrite makes obsolete."""
    paths = [delivery_category(root, "reports") / "packaging_validation.json",
             root / "临时文件" / "最终审核.json",
             root / "临时文件" / "video_montage_autonomous_completion.json",
             root / "临时文件" / "reports" / "packaging_technical.json",
             delivery_category(root, "reports") / "packaging_technical.json",
             runtime_directory(root) / "video_montage_autonomous_completion.json",
             runtime_directory(root) / "reports" / "packaging_technical.json",
             root / "日志" / "最终审核.json",
             root / "日志" / "任务记录" / "video_montage_autonomous_completion.json",
             root / "日志" / "任务记录" / "reports" / "packaging_technical.json"]
    for path in paths:
        path.unlink(missing_ok=True)


def subtitle_filename(plan_id: str) -> str:
    return f"subtitle-{plan_id}.txt"


def subtitle_output(output_dir: Path, plan_id: str) -> Path:
    ensure_delivery_layout(output_dir)
    return delivery_category(output_dir, "subtitles") / subtitle_filename(plan_id)


def relocated_config(config_path: Path, rows: list[dict], snapshots: list[Path], target: Path) -> dict:
    """Keep a copied config usable after moving it into the output directory."""
    config = read(config_path)
    for original, prepared, subtitle in zip(config["outputs"], rows, snapshots):
        original["input_path"] = prepared["input"]["path"]
        original["subtitle_flower_seed"] = prepared["subtitle_flower_seed"]
        original["subtitle_design"] = {key: value for key, value in prepared["subtitle_design"].items() if key != "graphic_layers"}
        original["graphic_layers"] = []
        original["subtitle_txt"] = os.path.relpath(subtitle, target.parent)
        for key in ("nameplate", "disclaimer", "bgm"):
            if prepared[key]:
                original[key]["path"] = prepared[key]["path"]
        for item, resolved in zip(original.get("text_pins", []), prepared["text_pins"]):
            item["path"] = resolved["path"]
    return config


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
    selection = config.get("subtitle_font", "random")
    if config.get("subtitle_font_path"):
        font = resolve_file(base, config["subtitle_font_path"])
        font_id, spec = subtitle_fonts.by_hash(font["sha256"])
        if selection != "random" and selection != font_id:
            raise ValueError("subtitle_font and subtitle_font_path identify different fonts")
    else:
        font_id, spec = subtitle_fonts.select(selection)
        font = resolve_file(base, spec["path"], spec["sha256"])
    if Path(font["path"]).suffix.lower() != ".otf":
        raise ValueError("subtitle font must be a supplied OTF file")
    mode = config.get("subtitle_flower", "random_ice")
    if mode not in flower_effects.MODES:
        raise ValueError(f"subtitle_flower must be one of {flower_effects.MODES}")
    scope = config.get("subtitle_flower_scope", "keywords")
    if scope not in ("keywords", "all"):
        raise ValueError("subtitle_flower_scope must be keywords or all")
    texts = config.get("subtitle_flower_texts", [SUBTITLE_EMPHASIS["text"]])
    if (not isinstance(texts, list) or not texts and scope == "keywords"
            or any(not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text for text in texts)
            or len(texts) != len(set(texts))):
        raise ValueError("subtitle_flower_texts must contain distinct nonempty single-line strings")
    emphasis = {**SUBTITLE_EMPHASIS, "selection": mode, "scope": scope, "texts": texts,
                "effects": flower_effects.effect_styles()["styles"]}
    ass = dict(SUBTITLE_ASS)
    # Historical ASS measurements use libass metrics, distinct from the current
    # reference-calibrated PIL design. Preserve legacy layout/read compatibility.
    ass_scale = {"w8": 1.0, "smiley": .83, "fangtang": .98}[font_id]
    ass["font_size"] = round(ass["font_size"] * ass_scale)
    emphasis["ass_font_size"] = round(emphasis["ass_font_size"] * ass_scale)
    return {"font": font, "font_id": font_id, "font_label": spec["label"],
            "font_family": spec["family"], "font_postscript": spec["postscript"],
            "capcut_reference": dict(SUBTITLE_REFERENCE), "ass": ass,
            "emphasis": emphasis}


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


def verified_caption_rows(asr: dict) -> list[dict]:
    """Balance Chinese shot captions without replaying recognition or tiny tails."""
    protected = ('无尽冬日', '无尽动日', '无尽冬至', '幸存者', '熔炉', '发展建设',
                 '三分钟', '中年人', '解压神器', '庇护所', '冰雪末日', '沉浸感',
                 '明星玩家', '吴岳', '吴樾', '升级', '温度', '砍树', '打猎', '挖矿',
                 '资源', '厨房', '三十', '快乐', '聚爽', '聚解压', '然后', '不断',
                 '获取', '建造', '建设', '收集', '收留', '几个', '更多', '现在玩',
                 '三分钟一局', '现在玩无尽冬日', '更是快乐')
    observations = asr.get('shot_observations')
    if not observations:
        observations = [{'output_in_frame': 0, 'asr': asr}]
    rows = []
    for shot in observations:
        offset = shot['output_in_frame'] / 60
        letters, times = [], []
        for segment in shot['asr']['segments']:
            words = segment.get('words') or [{'word': segment['text'], 'start': segment['start'], 'end': segment['end']}]
            for word in words:
                chars = re.findall(r'[0-9A-Za-z\u4e00-\u9fff]', word['word'])
                for index, char in enumerate(chars):
                    letters.append(char)
                    start = word['start'] + (word['end'] - word['start']) * index / len(chars)
                    end = word['start'] + (word['end'] - word['start']) * (index + 1) / len(chars)
                    times.append((start + offset, end + offset))
        text = ''.join(letters)
        forbidden = set()
        for word in protected:
            for match in re.finditer(re.escape(word), text):
                forbidden.update(range(match.start() + 1, match.end()))
        for match in re.finditer(r'[0-9A-Za-z]+', text):
            if len(match[0]) <= 10:forbidden.update(range(match.start() + 1, match.end()))
        cursor = 0
        while cursor < len(text):
            remaining = len(text) - cursor
            parts = (remaining + 9) // 10
            if parts == 1:end = len(text)
            else:
                choices = []
                while not choices and parts <= remaining:
                    ideal = cursor + round(remaining / parts)
                    choices = [end for end in range(max(cursor + 1, len(text) - (parts - 1) * 10), min(cursor + 10, len(text) - 1) + 1) if end not in forbidden]
                    if not choices:parts += 1
                if not choices:raise ValueError('cannot fit protected caption phrase')
                end = min(choices, key=lambda candidate: (abs(candidate - ideal), candidate))
            rows.append({'start': times[cursor][0], 'end': times[end - 1][1], 'text': text[cursor:end]})
            cursor = end
    return rows


def draft_one(input_path: Path, plan_id: str, output_dir: Path, *, overwrite: bool = False,
              verified_asr: dict | None = None) -> dict:
    if not PLAN_ID.fullmatch(plan_id):
        raise ValueError(f"unsafe plan ID: {plan_id}")
    video_spec(input_path)
    output = subtitle_output(output_dir, plan_id)
    if output.exists() and not overwrite:
        raise FileExistsError(output)
    source_script = ROOT / "scripts/semantic/scripts/v9_source_asr.py"
    spec = importlib.util.spec_from_file_location("packaging_source_asr", source_script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    if verified_asr is not None:
        if (verified_asr.get("timing_basis") != "verified_rendered_shot_asr"
                or verified_asr.get("output") != {"path": str(input_path.resolve()), "sha256": sha(input_path)}):
            raise ValueError("subtitle timing ASR must bind the actual clean input")
        asr, device = verified_asr, "verified_rendered_shot_asr"
    else:
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
    if verified_asr is not None:
        rows = verified_caption_rows(verified_asr)
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
            if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", chunk):
                continue
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
    atomic(delivery_category(output_dir, "reports") / "subtitle_draft.json", report)
    return report


def ass_time(ms: int) -> str:
    centis = round(ms / 10)
    hour, rem = divmod(centis, 360_000)
    minute, rem = divmod(rem, 6000)
    second, centi = divmod(rem, 100)
    return f"{hour}:{minute:02}:{second:02}.{centi:02}"


def clean_ass_text(text: str) -> str:
    return text.replace("{", "（").replace("}", "）").replace("\\", "／")


def flower_fragments(clean: str, emphasis: dict) -> list[tuple[str, bool]]:
    if emphasis["scope"] == "all":
        return [(clean, True)]
    pattern = "|".join(re.escape(clean_ass_text(text)) for text in sorted(emphasis["texts"], key=len, reverse=True))
    fragments, cursor = [], 0
    for match in re.finditer(pattern, clean):
        if match.start() > cursor:
            fragments.append((clean[cursor:match.start()], False))
        fragments.append((match.group(), True))
        cursor = match.end()
    if cursor < len(clean):
        fragments.append((clean[cursor:], False))
    return fragments


def write_ass(path: Path, cues: list[dict], style: dict, *, render_width: int = 1440,
              flower_seed: int = 0) -> list[dict]:
    """Write ordinary subtitles and reserve title space; return sprite placements.

    The returned occurrences must be composited with flower_effects.prepare_track.
    Each occurrence selects a style once, never once per video frame.
    """
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
    fragments_by_cue = [flower_fragments(clean_ass_text(cue["text"]), emphasis) for cue in cues]
    selections = iter(flower_effects.choose(emphasis["selection"], sum(flag for parts in fragments_by_cue for _, flag in parts), flower_seed))
    gap = f"{{\\fs{emphasis['gap_font_size']}\\fscx{emphasis['gap_scale_x']}}}\\h{{\\fs{ass['font_size']}\\fscx100}}"
    occurrences = []
    for cue_index, cue in enumerate(cues):
        fragments = fragments_by_cue[cue_index]
        timing = f"{ass_time(cue['start_ms'])},{ass_time(cue['end_ms'])},Default,,0,0,0,,"
        prefix = timing + position
        if not any(flag for _, flag in fragments):
            lines.append(f"Dialogue: 0,{prefix}{clean_ass_text(cue['text'])}")
            continue
        padded = []
        for index, (text, flag) in enumerate(fragments):
            if index and (flag or fragments[index - 1][1]):
                padded.append((gap, False))
            padded.append((text, flag))
        enlarged = lambda text: f"{{\\fs{emphasis['ass_font_size']}\\alpha&HFF&}}{text}{{\\fs{ass['font_size']}\\alpha&H00&}}"
        lines.append(f"Dialogue: 0,{prefix}" + "".join(enlarged(text) if flag else text for text, flag in padded))
        targets = [(index, text) for index, (text, flag) in enumerate(padded) if flag]
        for occurrence_index, (target_index, target_text) in enumerate(targets):
            marker = ""
            for index, (text, flag) in enumerate(padded):
                if flag:
                    alpha = "00" if index == target_index else "FF"
                    marker += (f"{{\\fs{emphasis['ass_font_size']}\\alpha&H{alpha}&}}{text}"
                               f"{{\\fs{ass['font_size']}\\alpha&HFF&}}")
                else:
                    marker += text
            occurrences.append({"cue_index": cue_index, "occurrence_index": occurrence_index,
                                "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                                "style_id": next(selections), "text": target_text,
                                "marker_text": position + r"{\alpha&HFF&}" + marker})
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return occurrences


def require_subtitle_font(log: str, style: dict | None = None) -> None:
    style = style or {"font_family": SUBTITLE_FONT_FAMILY, "font_postscript": SUBTITLE_FONT_POSTSCRIPT}
    subtitle_fonts.require_selected(log, style["font_family"], style["font_postscript"])


def require_design_config(config: dict) -> None:
    if config.get("schema") != SCHEMA or not isinstance(config.get("outputs"), list) or not config["outputs"]:
        raise ValueError(f"{SCHEMA} with nonempty outputs required")
    retired = {"subtitle_font", "subtitle_font_path", "subtitle_flower", "subtitle_flower_scope", "subtitle_flower_texts"}
    for item in [config, *config["outputs"]]:
        if not isinstance(item, dict):
            raise ValueError("packaging output must be an object")
        if retired.intersection(item):
            raise ValueError("legacy font/flower configuration fields are unsupported; use subtitle_design")
    for row in config["outputs"]:
        if "subtitle_srt" in row:
            raise ValueError("subtitle_srt is no longer supported; use subtitle_txt and subtitle_design")
        if not isinstance(row.get("subtitle_design"), dict):
            raise ValueError("every packaging output requires subtitle_design; legacy configurations are unsupported")
        if not isinstance(row.get("subtitle_txt"), str) or not row["subtitle_txt"]:
            raise ValueError("every packaging output requires subtitle_txt")


def prepared_rows(config_path: Path, expected_inputs: dict[str, str] | None = None) -> list[dict]:
    config = read(config_path)
    require_design_config(config)
    base = config_path.resolve().parent
    rows = []
    for row in config["outputs"]:
        plan_id = row.get("plan_id")
        if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id) or any(item["plan_id"].casefold() == plan_id.casefold() for item in rows):
            raise ValueError(f"unsafe or duplicate plan ID: {plan_id}")
        source = resolve_file(base, row.get("input_path"), row.get("input_sha256"))
        if expected_inputs is not None and expected_inputs.get(plan_id) != source["sha256"]:
            raise ValueError(f"clean delivery input mismatch: {plan_id}")
        spec = video_spec(Path(source["path"]))
        if (spec["width"], spec["height"]) != (1440, 2560):
            raise ValueError("clean video must be 1440x2560")
        subtitles = resolve_file(base, row.get("subtitle_txt"), row.get("subtitle_sha256"))
        cues = parse_srt(Path(subtitles["path"]), round(spec["duration"] * 1000))
        design = packaging_design.prepare(row["subtitle_design"], cues, subtitles["sha256"],
                                           row.get("graphic_layers", []), base, spec["frames"])
        style = subtitle_style({"subtitle_font": design["font"]}, base)
        seed = row.get("subtitle_flower_seed", 0)
        if type(seed) is not int or not 0 <= seed < 2 ** 64:
            raise ValueError("subtitle_flower_seed must be an unsigned 64-bit integer")
        prepared = {"plan_id": plan_id, "input": source, "spec": spec, "subtitles": {**subtitles, "cue_count": len(cues)}, "cues": cues,
                    "subtitle_flower_seed": seed,
                    "subtitle_style": style, "subtitle_design": design,
                    "graphic_layers": design["graphic_layers"],
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
    packaging_design.batch_effects([row["subtitle_design"] for row in rows])
    return rows


def render_one(row: dict, output_dir: Path) -> dict:
    plan_id = row["plan_id"]
    temporary_dir = output_dir / "临时文件" if OUTPUT_NAME.fullmatch(output_dir.name) else output_dir
    output_dir = delivery_category(output_dir, "video")
    output = output_dir / f"{plan_id}.mp4"
    partial = temporary_dir / f"{plan_id}.partial.mp4"
    if partial.exists() or (output.exists() and not row.get("_overwrite", False)):
        raise FileExistsError(f"refusing overwrite: {output}")
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=f".{plan_id}.subtitle-font-", dir=temporary_dir) as font_directory:
            local_font = Path(font_directory) / "subtitle.otf"
            shutil.copyfile(row["subtitle_style"]["font"]["path"], local_font)
            if sha(local_font) != row["subtitle_style"]["font"]["sha256"]:
                raise ValueError("subtitle font changed before rendering")
            track = design_renderer.prepare_track(Path(font_directory), row["cues"], row["subtitle_design"],
                                                  row["subtitle_style"]["font"]["path"], row["spec"]["frames"])
            styles = [span["flower"] for cue in row["subtitle_design"]["cues"] for span in cue["spans"] if span.get("flower")]
            encoding = flower_effects.video_encoding(styles)
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
            if track:
                command += ["-f", "concat", "-safe", "0", "-i", track["path"]]
                filters.append(f"[{index}:v]fps=60:round=up,format=rgba[flowers]")
                filters.append(f"{current}[flowers]overlay=0:{track['y']}:format={encoding['overlay_format']}:eof_action=pass[flowered]")
                current = "[flowered]"
                index += 1
            filters.append(f"{current}format={encoding['pixel_format']},setpts=(PTS-STARTPTS)/1.2,fps=60:round=up[vout]")
            if row["bgm"]:
                command += ["-stream_loop", "-1", "-i", row["bgm"]["path"]]
                filters.append(f"[{index}:a]volume={row['bgm']['gain_db']}dB[bgm]")
                filters.append("[0:a][bgm]amix=inputs=2:duration=first:normalize=0,asetpts=PTS-STARTPTS,atempo=1.2,apad[aout]")
            else:
                filters.append("[0:a]asetpts=PTS-STARTPTS,atempo=1.2,apad[aout]")
            frames = final_frames(row["spec"]["frames"])
            command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-frames:v", str(frames),
                        "-r", "60", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(encoding["crf"]),
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                        "-t", f"{frames / 60:.6f}", "-movflags", "+faststart", str(partial)]
            completed = run(command, cwd=temporary_dir)
            spec = video_spec(partial)
            if (spec["frames"] != frames or (spec["width"], spec["height"]) != (1440, 2560)
                    or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac" or not packaged_streams_ok(spec)):
                raise RuntimeError(f"packaged video specification mismatch: {spec}")
            os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {"plan_id": plan_id, "input": row["input"], "input_frames": row["spec"]["frames"],
            "final_speed": FINAL_SPEED, "output_frames": frames, "output_path": str(output.resolve()),
            "output_sha256": sha(output), "output_spec": spec, "subtitles": row["subtitles"],
            "nameplate": row["nameplate"], "text_pins": row["text_pins"], "disclaimer": row["disclaimer"], "bgm": row["bgm"],
            "subtitle_flower_seed": row["subtitle_flower_seed"], "flower_choices": track.get("choices", []) if track else [],
            "subtitle_style": row["subtitle_style"],
            "design": track["record"],
            "video_encoding": encoding}


def render(config_path: Path, output_dir: Path, manifest_path: Path, delivery_manifest: Path | None = None,
           controller_validation: Path | None = None, autonomous_clean: Path | None = None,
            autonomous_clean_qc: Path | None = None, *, overwrite: bool = False, resume: bool = False) -> dict:
    if (OUTPUT_NAME.fullmatch(output_dir.name) and manifest_path.resolve().is_relative_to(output_dir.resolve())
            and not manifest_path.resolve().is_relative_to(runtime_directory(output_dir))):
        raise ValueError("packaging records inside the delivery must be in 临时文件")
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(manifest_path)
    if bool(delivery_manifest) != bool(controller_validation):
        raise ValueError("complete montage packaging requires both clean delivery and controller validation")
    if bool(autonomous_clean) != bool(autonomous_clean_qc) or (autonomous_clean and delivery_manifest):
        raise ValueError("choose one complete montage delivery contract")
    expected = None
    if delivery_manifest:
        delivery = read(delivery_manifest)
        if delivery.get("schema") != "ffmpeg-controller-delivery/v260928":
            raise ValueError("controller delivery manifest required")
        if any(item.get("final_speed", 1.0) != 1.0 for item in delivery.get("results", [])):
            raise ValueError("packaging requires unaccelerated clean inputs; final speed would be applied twice")
        validation = read(controller_validation)
        if (validation.get("schema") != "ffmpeg-controller-validation/v260928"
                or validation.get("decision") != "pass"
                or Path(str(validation.get("manifest_path") or "")).resolve() != delivery_manifest.resolve()
                or validation.get("manifest_sha256") != sha(delivery_manifest)):
            raise ValueError("passing controller validation bound to clean delivery required")
        expected = {item["plan_id"]: item["output_sha256"] for item in delivery["results"]}
    if autonomous_clean:
        delivery = read(autonomous_clean)
        validation = read(autonomous_clean_qc)
        if (delivery.get("schema") != "video-montage-autonomous-clean/v260929"
                or validation.get("schema") != "video-montage-autonomous-clean-qc/v260929"
                or validation.get("decision") != "pass"
                or validation.get("clean_delivery", {}).get("sha256") != sha(autonomous_clean)
                or Path(str(validation.get("clean_delivery", {}).get("path") or "")).resolve() != autonomous_clean.resolve()):
            raise ValueError("passing autonomous clean QC bound to delivery required")
        expected = {item["plan_id"]: item["output"]["sha256"] for item in delivery["results"]}
    rows = prepared_rows(config_path, expected)
    snapshots = [subtitle_output(output_dir, row["plan_id"]) for row in rows]
    config_snapshot = delivery_category(output_dir, "config") / config_path.name
    copies = [(config_path, config_snapshot)] + [
        (Path(row["subtitles"]["path"]), snapshot) for row, snapshot in zip(rows, snapshots)
    ]
    # Publication can finish before Windows releases a temporary file held by a
    # concurrent preview. Recover only an exact, fully hash-bound publication.
    if resume and overwrite and manifest_path.is_file():
        previous = read(manifest_path)
        relocated_rows = json.loads(json.dumps(rows))
        for row in relocated_rows:
            if OUTPUT_NAME.fullmatch(output_dir.name):
                row["input"]["path"] = str((delivery_category(output_dir, "clean") / f"{row['plan_id']}.mp4").resolve())
        expected_config = relocated_config(config_path, relocated_rows, snapshots, config_snapshot)
        source_manifest = delivery_manifest or autonomous_clean
        source_validation = controller_validation or autonomous_clean_qc
        mode = "complete_montage" if delivery_manifest else "complete_autonomous" if autonomous_clean else "standalone_test"
        valid = (previous.get("schema") == "video-montage-packaging-delivery/v1"
                 and previous.get("mode") == mode and previous.get("output_count") == len(rows)
                 and config_snapshot.is_file() and read(config_snapshot) == expected_config
                 and previous.get("config_snapshot_path") == str(config_snapshot.resolve())
                 and previous.get("config_snapshot_sha256") == sha(config_snapshot)
                 and previous.get("clean_delivery_sha256") == (sha(source_manifest) if source_manifest else None)
                 and previous.get("controller_validation_sha256") == (sha(source_validation) if source_validation else None))
        prior_rows = previous.get("results", [])
        valid = valid and [r.get("plan_id") for r in prior_rows] == [r["plan_id"] for r in rows]
        if valid:
            for row, old, snapshot in zip(relocated_rows, prior_rows, snapshots):
                output = delivery_category(output_dir, "video") / f"{row['plan_id']}.mp4"
                valid = (old.get("input") == row["input"]
                         and old.get("output_path") == str(output.resolve()) and output.is_file()
                         and old.get("output_sha256") == sha(output)
                         and snapshot.is_file() and sha(snapshot) == row["subtitles"]["sha256"]
                         and Path(row["input"]["path"]).is_file() and sha(Path(row["input"]["path"])) == row["input"]["sha256"]
                         and old.get("design", {}).get("cues") == row["subtitle_design"]["cues"]
                         and all(Path(r["path"]).is_file() and sha(Path(r["path"])) == r["sha256"]
                                 for r in old.get("design", {}).get("resources", [])))
                if not valid:
                    break
        if valid:
            invalidate_delivery_receipts(output_dir)
            return previous
    reserved = {}
    for path, label in [(manifest_path, "manifest"), *[(target, "snapshot") for _, target in copies],
                        *[(delivery_category(output_dir, "video") / f"{row['plan_id']}{suffix}", "render")
                          for row in rows for suffix in (".mp4", ".partial.mp4", ".ass")]]:
        resolved = path.resolve()
        if resolved in reserved:
            raise ValueError(f"packaging paths collide: {reserved[resolved]} and {label}: {resolved}")
        reserved[resolved] = label
    for source, target in copies:
        if source.resolve() != target.resolve() and target.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite packaging file: {target}")
    for row in rows:
        target = delivery_category(output_dir, "video") / f"{row['plan_id']}.mp4"
        if target.exists() and not overwrite:
            raise FileExistsError(f"refusing overwrite: {target}")
    temporary_root = runtime_directory(output_dir) if OUTPUT_NAME.fullmatch(output_dir.name) else output_dir
    temporary_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="encode-batch-", dir=temporary_root, ignore_cleanup_errors=True) as directory:
        staging = Path(directory)
        (staging / "videos").mkdir()
        # No delivered file is touched until every encoder has succeeded.
        results = [render_one(row, staging / "videos") for row in rows]
        publication = []
        for index, (row, result, snapshot) in enumerate(zip(rows, results, snapshots)):
            for item in (row["input"], row["subtitles"]):
                if sha(Path(item["path"])) != item["sha256"]:
                    raise ValueError("packaging input changed during encoding")
            if result.get("output_path"):
                encoded = Path(result["output_path"])
                target = delivery_category(output_dir, "video") / f"{row['plan_id']}.mp4"
                publication.append((encoded, target))
                result["output_path"] = str(target.resolve())
            if OUTPUT_NAME.fullmatch(output_dir.name):
                clean_source = Path(row["input"]["path"])
                clean_target = delivery_category(output_dir, "clean") / f"{row['plan_id']}.mp4"
                if clean_source.resolve() != clean_target.resolve():
                    if clean_target.exists() and not overwrite:
                        raise FileExistsError(f"refusing overwrite: {clean_target}")
                    publication.append((clean_source, clean_target))
                row["input"] = {**row["input"], "path": str(clean_target.resolve())}
                result["input"] = row["input"]
            subtitle = staging / f"subtitle-{index}.txt"
            shutil.copyfile(row["subtitles"]["path"], subtitle)
            publication.append((subtitle, snapshot))
            result["subtitles"] = {**row["subtitles"], "path": str(snapshot.resolve())}
            result["subtitle_snapshot"] = {"path": str(snapshot.resolve()), "sha256": sha(subtitle)}
        staged_config = staging / "config.json"
        atomic(staged_config, relocated_config(config_path, rows, snapshots, config_snapshot))
        publication.append((staged_config, config_snapshot))
        mode = "complete_montage" if delivery_manifest else "complete_autonomous" if autonomous_clean else "standalone_test"
        manifest = {"schema": "video-montage-packaging-delivery/v1", "mode": mode,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "subtitle_style": rows[0]["subtitle_style"],
                    "config_path": str(config_snapshot.resolve()), "config_sha256": sha(staged_config),
                    "config_snapshot_path": str(config_snapshot.resolve()), "config_snapshot_sha256": sha(staged_config),
                    "clean_delivery_path": str((delivery_manifest or autonomous_clean).resolve()) if delivery_manifest or autonomous_clean else None,
                    "clean_delivery_sha256": sha(delivery_manifest or autonomous_clean) if delivery_manifest or autonomous_clean else None,
                    "controller_validation_path": str((controller_validation or autonomous_clean_qc).resolve()) if controller_validation or autonomous_clean_qc else None,
                    "controller_validation_sha256": sha(controller_validation or autonomous_clean_qc) if controller_validation or autonomous_clean_qc else None,
                    "output_count": len(results), "results": results}
        staged_manifest = staging / "manifest.json"
        atomic(staged_manifest, manifest)
        publication.append((staged_manifest, manifest_path))
        publish_files(publication, temporary_root)
    if overwrite:
        invalidate_delivery_receipts(output_dir)
    return manifest


def reburn(previous_manifest_path: Path, plan_id: str, subtitle_txt: Path, output_dir: Path,
           manifest_path: Path) -> dict:
    """Reburn in the requested directory, atomically replacing finished videos."""
    previous = read(previous_manifest_path)
    if previous.get("schema") != "video-montage-packaging-delivery/v1":
        raise ValueError("previous packaging manifest required")
    results = previous.get("results", [])
    if not results or plan_id not in {row.get("plan_id") for row in results}:
        raise ValueError(f"unknown plan ID in previous packaging: {plan_id}")
    previous_sha256 = sha(previous_manifest_path)
    edited = resolve_file(Path.cwd(), str(subtitle_txt))
    snapshot = Path(str(previous.get("config_snapshot_path") or ""))
    if not snapshot.is_file() or sha(snapshot) != previous.get("config_snapshot_sha256"):
        raise ValueError("hash-bound design configuration snapshot required for reburn")
    old_config = read(snapshot)
    require_design_config(old_config)
    rows = []
    for old in results:
        selected = old["plan_id"] == plan_id
        subtitle = edited if selected else old["subtitle_snapshot"]
        row = {"plan_id": old["plan_id"], "input_path": old["input"]["path"],
               "input_sha256": old["input"]["sha256"], "subtitle_txt": subtitle["path"],
               "subtitle_sha256": subtitle["sha256"]}
        if "subtitle_flower_seed" in old:
            row["subtitle_flower_seed"] = old["subtitle_flower_seed"]
        old_row = next((r for r in old_config.get("outputs", []) if r["plan_id"] == old["plan_id"]), {})
        if not isinstance(old_row.get("subtitle_design"), dict):
            raise ValueError("reburn requires subtitle_design for every output")
        source_cues = parse_srt(Path(subtitle["path"]), round(old["input_frames"] / 60 * 1000))
        row["subtitle_design"] = packaging_design.refresh(old_row["subtitle_design"], source_cues, subtitle["sha256"])
        row["graphic_layers"] = []
        if row["subtitle_design"].get("needs_design_review"):
            raise ValueError("subtitle text changed; revise retained subtitle_design before rendering through render/package")
        for key in ("nameplate", "disclaimer", "bgm"):
            if old.get(key):
                row[key] = old[key]
        if old.get("text_pins"):
            row["text_pins"] = old["text_pins"]
        rows.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = delivery_category(output_dir, "config") / "reburn_config.json"
    config = {"schema": SCHEMA, "outputs": rows}
    atomic(config_path, config)
    delivery = Path(previous["clean_delivery_path"]) if previous.get("clean_delivery_path") else None
    controller = Path(previous["controller_validation_path"]) if previous.get("controller_validation_path") else None
    if previous.get("mode") == "complete_autonomous":
        result = render(config_path, output_dir, manifest_path, autonomous_clean=delivery,
                        autonomous_clean_qc=controller, overwrite=True)
    else:
        result = render(config_path, output_dir, manifest_path, delivery, controller, overwrite=True)
    result["reburn_source"] = {"manifest_path": str(previous_manifest_path.resolve()),
                               "manifest_sha256": previous_sha256, "edited_plan_id": plan_id,
                               "overwritten_in_place": previous_manifest_path.resolve() == manifest_path.resolve()}
    atomic(manifest_path, result)
    return result


def pcm_stats(path: Path) -> dict:
    import numpy as np
    command = [str(FFMPEG), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "2", "-ar", "48000", "-f", "s16le", "-"]
    clipped = count = 0
    peak = 0
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
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


def verify_autonomous_speech(measured: dict, item: dict, manifest: dict) -> bool:
    if measured.get("asr_match") is True:
        return True
    if measured.get("speech_preserved") is not True:
        return False
    # Recompute the signal proof, rather than trusting a declared pass flag.
    spec = importlib.util.spec_from_file_location("packaging_mix_verifier", ROOT / "scripts/autonomous/scripts/autonomous_montage.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    qc_path = Path(str(manifest.get("controller_validation_path") or ""))
    if not qc_path.is_file() or sha(qc_path) != manifest.get("controller_validation_sha256"):
        return False
    qc = read(qc_path)
    clean_row = next((r for r in qc.get("results", []) if r.get("plan_id") == item["plan_id"]), {})
    if qc.get("decision") != "pass" or clean_row.get("text_match") is not True or clean_row.get("output", {}).get("sha256") != item["input"]["sha256"]:
        return False
    output = verifier.pcm(Path(item["output_path"]))
    proof = verifier.delivery_mix_evidence(output, item)
    return proof.get("decision") == "pass" and proof == measured.get("mix_evidence")


def validate(manifest_path: Path, report_path: Path, review_path: Path | None = None,
             authority_path: Path | None = None, autonomous_evidence: Path | None = None,
             autonomous_review: Path | None = None) -> dict:
    manifest = read(manifest_path)
    for row in manifest.get("results", []):
        output = Path(row.get("output_path", "")).resolve()
        delivery = output.parent.parent if output.parent.name == "成片" else output.parent
        if (OUTPUT_NAME.fullmatch(delivery.name) and report_path.resolve().is_relative_to(delivery)
                and not report_path.resolve().is_relative_to(runtime_directory(delivery))):
            raise ValueError("validation reports inside the delivery must be in 临时文件")
    failures = []
    if bool(review_path) != bool(authority_path):
        raise ValueError("review and independent review authority must be provided together")
    if bool(autonomous_evidence) != bool(autonomous_review) or (autonomous_evidence and review_path):
        raise ValueError("choose one packaging review contract")
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
    automatic = read(autonomous_evidence) if autonomous_evidence else None
    codex = read(autonomous_review) if autonomous_review else None
    auto_findings = {}
    if automatic is not None:
        if (manifest.get("mode") != "complete_autonomous"
                or automatic.get("schema") != "video-montage-autonomous-final-evidence/v260929"
                or automatic.get("decision") != "pass"
                or automatic.get("manifest", {}).get("sha256") != sha(manifest_path)
                or Path(str(automatic.get("manifest", {}).get("path") or "")).resolve() != manifest_path.resolve()
                or codex.get("schema") != "video-montage-codex-review/v260929"
                or codex.get("stage") != "final" or codex.get("reviewer_role") != "codex"
                or codex.get("evidence_sha256") != sha(autonomous_evidence)):
            failures.append("autonomous_review_binding")
        auto_rows = automatic.get("results", [])
        codex_rows = codex.get("outputs", [])
        if (not isinstance(auto_rows, list) or not isinstance(codex_rows, list)
                or len(auto_rows) != len(manifest.get("results", []))
                or len(codex_rows) != len(auto_rows)):
            failures.append("autonomous_review_scope")
        else:
            auto_by_id = {row.get("plan_id"): row for row in auto_rows}
            codex_by_id = {row.get("plan_id"): row for row in codex_rows}
            ids = {row.get("plan_id") for row in manifest.get("results", [])}
            if len(auto_by_id) != len(auto_rows) or len(codex_by_id) != len(codex_rows) or set(auto_by_id) != ids or set(codex_by_id) != ids:
                failures.append("autonomous_review_scope")
            else:
                auto_findings = codex_by_id
                for item in manifest["results"]:
                    pid = item["plan_id"]
                    measured, finding = auto_by_id[pid], codex_by_id[pid]
                    if (measured.get("decision") != "pass" or not verify_autonomous_speech(measured, item, manifest)
                            or measured.get("subtitle_timing_pass") is not True
                            or not isinstance(measured.get("cut_pcm"), list)
                            or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])
                            or measured.get("output", {}).get("sha256") != item.get("output_sha256")
                            or Path(str(measured.get("output", {}).get("path") or "")).resolve() != Path(item["output_path"]).resolve()
                            or measured.get("metrics", {}).get("clipped_samples") != 0
                            or measured.get("subtitle_cue_count") != item.get("subtitles", {}).get("cue_count")
                            or (measured.get("voice_over_bgm_db") is not None and measured["voice_over_bgm_db"] < 6)
                            or finding.get("output_sha256") != item.get("output_sha256")
                            or any(finding.get(name) is not True for name in ("visual_pass", "subtitle_pass", "overlay_pass"))
                            or not finding.get("reason")):
                        failures.append(f"autonomous_review:{pid}")
                    for frame in measured.get("frames", []):
                        path = Path(str(frame.get("path") or ""))
                        if not path.is_file() or sha(path) != frame.get("sha256"):
                            failures.append(f"autonomous_frame:{pid}")
                            break
                    if item.get("design"):
                        indices = {frame.get("frame") for frame in measured.get("frames", [])}
                        required = packaging_design.evidence_frames(item["design"], delivery_speed(item), item["output_frames"])
                        if (measured.get("design") != item["design"] or measured.get("design_required") is not True
                                or not set(required).issubset(indices)):
                            failures.append(f"autonomous_design_evidence:{pid}")
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
        artwork = style.get("emphasis", {}).get("effects", style.get("emphasis", {}).get("artwork", {})) if isinstance(style, dict) else {}
        if not isinstance(artwork, dict):
            failures.append("subtitle_flower_record")
        else:
            for spec in artwork.values():
                if not isinstance(spec, dict) or not spec.get("path") or not spec.get("sha256"):
                    failures.append("subtitle_flower_record")
                    continue
                path = Path(spec["path"])
                if not path.is_file() or sha(path) != spec["sha256"]:
                    failures.append("subtitle_flower_changed")
    source = manifest.get("reburn_source")
    if source:
        previous_path = Path(str(source.get("manifest_path") or ""))
        in_place = source.get("overwritten_in_place") is True
        if in_place and (previous_path.resolve() != manifest_path.resolve() or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("manifest_sha256", "")))):
            failures.append("reburn_source_changed")
        elif not in_place and (not previous_path.is_file() or sha(previous_path) != source.get("manifest_sha256")):
            failures.append("reburn_source_changed")
    config_path = Path(str(manifest.get("config_path") or ""))
    if not config_path.is_file() or sha(config_path) != manifest.get("config_sha256"):
        failures.append("config_changed")
    config_snapshot = Path(str(manifest.get("config_snapshot_path") or ""))
    if not config_snapshot.is_file() or sha(config_snapshot) != manifest.get("config_snapshot_sha256"):
        failures.append("config_snapshot_changed")
    clean_path = manifest.get("clean_delivery_path")
    if clean_path:
        clean = Path(clean_path)
        clean_valid = clean.is_file() and sha(clean) == manifest.get("clean_delivery_sha256")
        if not clean_valid:
            failures.append("clean_delivery_changed")
        controller = Path(str(manifest.get("controller_validation_path") or ""))
        if not controller.is_file() or sha(controller) != manifest.get("controller_validation_sha256"):
            failures.append("controller_validation_changed")
        elif clean_valid:
            clean_report = read(controller)
            if manifest.get("mode") == "complete_autonomous":
                if (read(clean).get("schema") != "video-montage-autonomous-clean/v260929"
                        or clean_report.get("schema") != "video-montage-autonomous-clean-qc/v260929"
                        or clean_report.get("decision") != "pass"
                        or clean_report.get("clean_delivery", {}).get("sha256") != manifest.get("clean_delivery_sha256")):
                    failures.append("autonomous_clean_validation_binding")
            elif (clean_report.get("decision") != "pass"
                  or clean_report.get("manifest_sha256") != manifest.get("clean_delivery_sha256")):
                failures.append("controller_validation_binding")
    checks = []
    results = manifest.get("results", [])
    ids = [row.get("plan_id") for row in results]
    if (manifest.get("output_count") != len(results) or not results
            or any(not isinstance(pid, str) or not PLAN_ID.fullmatch(pid) for pid in ids)
            or len({str(pid).casefold() for pid in ids}) != len(ids)):
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
                expected_frames = final_frames(row["input_frames"]) if delivery_speed(row) == FINAL_SPEED else row["input_frames"]
                if (spec["frames"] != expected_frames or (spec["width"], spec["height"]) != (1440, 2560)
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
        finding = reviews.get(plan_id) or auto_findings.get(plan_id)
        if row.get("subtitle_style"):
            font = row["subtitle_style"]["font"]
            if not Path(font["path"]).is_file() or sha(Path(font["path"])) != font["sha256"]:
                changes.append("subtitle_font_changed")
        if row.get("design"):
            for resource in row["design"].get("resources", []):
                path = Path(resource["path"])
                if not path.is_file() or sha(path) != resource["sha256"]:
                    changes.append("design_resource_changed")
            if (review is not None or automatic is not None) and not packaging_design.review_ok(row["design"], finding):
                changes.append("design_review")
        if review is not None and (not finding or finding.get("output_sha256") != row.get("output_sha256")
                                   or finding.get("visual_pass") is not True or finding.get("audio_pass") is not True
                                   or finding.get("subtitle_pass") is not True or finding.get("overlay_pass") is not True):
            changes.append("independent_review")
        checks.append({"plan_id": plan_id, "decision": "pass" if not changes else "reject", "failures": changes,
                       "audio": audio if output.is_file() and "output_changed" not in changes else None,
                       "visual_review": {"subtitle_cues": row["subtitles"]["cue_count"], "nameplate": bool(row.get("nameplate")),
                                         "text_pins": len(row.get("text_pins", [])), "disclaimer": bool(row.get("disclaimer")),
                                         "status": "pass" if finding and finding.get("visual_pass") is True else "pending_review"},
                       "sound_review": {"bgm": bool(row.get("bgm")), "status": "pass" if automatic is not None and not any(f.startswith(f"autonomous_review:{plan_id}") for f in failures) else "pass" if finding and finding.get("audio_pass") is True else "pending_review"}})
        failures += [f"{plan_id}:{change}" for change in changes]
    if review is not None and set(reviews) != {row.get("plan_id") for row in results} and "review_scope" not in failures:
        failures.append("review_scope")
    decision = "reject" if failures else "pass" if review is not None or automatic is not None else "technical_pass_pending_review"
    report = {"schema": "video-montage-packaging-validation/v1", "decision": decision,
              "manifest_path": str(manifest_path.resolve()), "manifest_sha256": sha(manifest_path),
              "review_path": str(review_path.resolve()) if review_path else None,
              "review_sha256": sha(review_path) if review_path else None,
              "review_authority_path": str(authority_path.resolve()) if authority_path else None,
              "review_authority_sha256": sha(authority_path) if authority_path else None,
              "autonomous_evidence_sha256": sha(autonomous_evidence) if autonomous_evidence else None,
              "autonomous_review_sha256": sha(autonomous_review) if autonomous_review else None,
              "independent_review": "not_applicable" if automatic is not None else "pass" if review is not None and not any("review" in failure for failure in failures) else "pending_or_rejected",
              "autonomous_review": "pass" if automatic is not None and not failures else "pending_or_rejected" if automatic is not None else "not_applicable",
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
    render_cmd.add_argument("--autonomous-clean", type=Path)
    render_cmd.add_argument("--autonomous-clean-qc", type=Path)
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
    check.add_argument("--autonomous-evidence", type=Path)
    check.add_argument("--autonomous-review", type=Path)
    args = parser.parse_args()
    if hasattr(args, "output_dir"):
        if not OUTPUT_NAME.fullmatch(args.output_dir.name):
            parser.error("--output-dir 必须命名为 自动化混剪_YYYYMMDD_HHMMSS")
        if args.output_dir.resolve().parent.name != "work":
            parser.error("--output-dir 必须位于 work 目录下")
        if hasattr(args, "manifest"):
            expected_parent = delivery_category(args.output_dir.resolve(), "manifests")
            if args.manifest.resolve().parent != expected_parent:
                parser.error("--manifest 必须位于 work/<交付目录名>/临时文件/manifests/ 下")
    if args.command == "draft":
        result = draft_one(args.input.resolve(), args.plan_id, args.output_dir.resolve())
    elif args.command == "draft-batch":
        result = draft_batch(args.delivery_manifest.resolve(), args.output_dir.resolve())
    elif args.command == "render":
        result = render(args.config.resolve(), args.output_dir.resolve(), args.manifest.resolve(),
                        args.delivery_manifest.resolve() if args.delivery_manifest else None,
                        args.controller_validation.resolve() if args.controller_validation else None,
                        args.autonomous_clean.resolve() if args.autonomous_clean else None,
                        args.autonomous_clean_qc.resolve() if args.autonomous_clean_qc else None)
    elif args.command == "reburn":
        result = reburn(args.previous_manifest.resolve(), args.plan_id, args.subtitle_txt.resolve(),
                        args.output_dir.resolve(), args.manifest.resolve())
    else:
        result = validate(args.manifest.resolve(), args.report.resolve(),
                          args.review.resolve() if args.review else None,
                          args.review_authority.resolve() if args.review_authority else None,
                          args.autonomous_evidence.resolve() if args.autonomous_evidence else None,
                          args.autonomous_review.resolve() if args.autonomous_review else None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("decision") != "reject" else 2


if __name__ == "__main__":
    raise SystemExit(main())
