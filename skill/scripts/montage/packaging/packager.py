"""Hash-bound subtitle and overlay packaging of validated clean montage videos."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from montage import cast as CAST
from montage.common import FFMPEG, probe, read, require_ref, run, sha, workers, write as atomic
from montage.media.mix import FINAL_SPEED, MIN_VOICE_OVER_MUSIC_DB, auto_bgm_gain_db, delivery_mix_evidence, delivery_speed, final_frames, pcm
from montage.packaging import design as packaging_design
from montage.packaging import design_renderer, flower_effects
from montage.packaging import fonts as subtitle_fonts
from montage.packaging import segmentation as subtitle_segmentation
from montage.packaging.delivery_files import publish_files

SCHEMA = "video-montage-packaging/v1"
TEXT_PIN_POLICY = "required-full-coverage/v1"


def prepare_nameplates(row: dict, base: Path, frames: int) -> list[dict]:
    items = row.get("nameplates", [])
    if not isinstance(items, list):
        raise ValueError("nameplates must be a list")
    result, seen, previous_end = [], set(), 0
    for item in items:
        if not isinstance(item, dict) or not CAST.person_id(item.get("person_id")) or item["person_id"] in seen:
            raise ValueError("one nameplate per unique canonical celebrity required")
        if any(key not in item for key in ("start_frame", "end_frame_exclusive", "sha256")):
            raise ValueError("nameplate identity, hash and integer frame interval required")
        start, end = frame_window(item, frames)
        if start < previous_end:
            raise ValueError("first-appearance nameplates must be chronological and must not overlap")
        if final_frames(end) - final_frames(start) < CAST.MIN_FINAL_NAMEPLATE_FRAMES:
            raise ValueError("nameplate display must last at least 0.8 final seconds")
        asset = resolve_overlay(base, item.get("path"), item.get("sha256"))
        result.append({**asset, "person_id": item["person_id"], "start_frame": start, "end_frame_exclusive": end})
        seen.add(item["person_id"])
        previous_end = end
    return result


def text_pin_coverage(layers: list[dict], frames: int) -> dict:
    """Required pins cover every clean frame, with readable, hash-bound assets."""
    if not isinstance(layers, list) or not layers:
        raise ValueError("every packaged video requires text pins")
    cursor = 0
    for layer in layers:
        if (not isinstance(layer, dict) or type(layer.get("start_frame")) is not int
                or type(layer.get("end_frame_exclusive")) is not int
                or not isinstance(layer.get("path"), str) or not layer["path"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(layer.get("sha256", "")))):
            raise ValueError("text pins require an asset hash and integer frame interval")
        start, end = layer["start_frame"], layer["end_frame_exclusive"]
        if start != cursor or not start < end <= frames:
            raise ValueError("text pins must cover the full video without gaps or overlap")
        if final_frames(end) - final_frames(start) < 48:
            raise ValueError("each text pin must remain readable for at least 0.8 final seconds")
        cursor = end
    if cursor != frames:
        raise ValueError("text pins must cover the video through its final frame")
    return {"policy": TEXT_PIN_POLICY, "decision": "pass", "input_frames": frames,
            "final_frames": final_frames(frames), "layers": layers}


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
DRAFT_PROTECTED_WORD = "无尽冬日"  # never split across draft caption lines


OUTPUT_NAME = re.compile(r"自动化混剪_\d{8}_\d{6}(?:_\d{6})?\Z")


def runtime_directory(root: Path) -> Path:
    """Keep each delivery's audit/state files in its temporary directory."""
    root = root.resolve()
    return root / "临时文件"


def delivery_category(root: Path, kind: str) -> Path:
    """Delivery folders inside <output_root>/自动化混剪_*; a plain directory otherwise."""
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
             delivery_category(root, "reports") / "packaging_technical.json",
             runtime_directory(root) / "video_montage_autonomous_completion.json"]
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
        original["subtitle_design"] = {key: value for key, value in prepared["subtitle_design"].items() if key != "graphic_layers"}
        original["graphic_layers"] = []
        original["subtitle_txt"] = os.path.relpath(subtitle, target.parent)
        for key in ("disclaimer", "bgm"):
            if prepared[key]:
                original[key]["path"] = prepared[key]["path"]
        for item, resolved in zip(original.get("nameplates", []), prepared["nameplates"]):
            item["path"] = resolved["path"]
        for item, resolved in zip(original.get("text_pins", []), prepared["text_pins"]):
            item["path"] = resolved["path"]
    return config


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


def subtitle_style(font_id: str, base: Path) -> dict:
    spec = subtitle_fonts.FONTS[font_id]
    font = resolve_file(base, spec["path"], spec["sha256"])
    return {"font": font, "font_id": font_id, "font_label": spec["label"],
            "font_family": spec["family"], "font_postscript": spec["postscript"]}


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
        word = DRAFT_PROTECTED_WORD
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
    """Propose measured, speech-bound phrases; the agent still reviews semantics."""
    return subtitle_segmentation.caption_rows(asr)


def draft_one(input_path: Path, plan_id: str, output_dir: Path, verified_asr: dict, *,
              overwrite: bool = False) -> dict:
    if not PLAN_ID.fullmatch(plan_id):
        raise ValueError(f"unsafe plan ID: {plan_id}")
    video_spec(input_path)
    output = subtitle_output(output_dir, plan_id)
    if output.exists() and not overwrite:
        raise FileExistsError(output)
    if (verified_asr.get("timing_basis") != "verified_rendered_shot_asr"
            or verified_asr.get("output") != {"path": str(input_path.resolve()), "sha256": sha(input_path)}):
        raise ValueError("subtitle timing ASR must bind the actual clean input")
    asr, device = verified_asr, "verified_rendered_shot_asr"
    # Reviewed shot words keep each caption inside its rendered shot.
    rows = verified_caption_rows(asr)
    if not rows:
        raise RuntimeError("verified ASR produced no subtitle cues")
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


def require_design_config(config: dict) -> None:
    if config.get("schema") != SCHEMA or not isinstance(config.get("outputs"), list) or not config["outputs"]:
        raise ValueError(f"{SCHEMA} with nonempty outputs required")
    for row in config["outputs"]:
        if not isinstance(row, dict) or not isinstance(row.get("subtitle_design"), dict):
            raise ValueError("every packaging output requires subtitle_design")
        if not isinstance(row.get("subtitle_txt"), str) or not row["subtitle_txt"]:
            raise ValueError("every packaging output requires subtitle_txt")


def prepared_rows(config_path: Path, expected_inputs: dict[str, str] | None = None) -> list[dict]:
    config = read(config_path)
    require_design_config(config)
    pin_policy = config.get("text_pin_policy")
    if pin_policy not in {None, TEXT_PIN_POLICY}:
        raise ValueError("unknown text pin policy")
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
        if "nameplate" in row:
            raise ValueError("use nameplates: one first-appearance window per selected celebrity")
        prepared = {"plan_id": plan_id, "input": source, "spec": spec, "subtitles": {**subtitles, "cue_count": len(cues)}, "cues": cues,
                    "subtitle_style": subtitle_style(design["font"], base), "subtitle_design": design,
                    "graphic_layers": design["graphic_layers"],
                    "nameplates": prepare_nameplates(row, base, spec["frames"]),
                    "text_pins": [], "disclaimer": None, "bgm": None}
        for item in row.get("text_pins", []):
            if "start_frame" not in item or "end_frame_exclusive" not in item:
                raise ValueError("text pin interval is required")
            start, end = frame_window(item, spec["frames"])
            prepared["text_pins"].append({**resolve_overlay(base, item.get("path"), item.get("sha256")), "start_frame": start, "end_frame_exclusive": end})
        if pin_policy:
            text_pin_coverage(row.get("text_pins", []), spec["frames"])
            text_pin_coverage(prepared["text_pins"], spec["frames"])
            prepared["text_pin_policy"] = pin_policy
        if row.get("disclaimer"):
            item = row["disclaimer"]
            prepared["disclaimer"] = resolve_overlay(base, item.get("path"), item.get("sha256"))
        if row.get("bgm"):
            item = row["bgm"]
            music = resolve_file(base, item.get("path"), item.get("sha256"))
            gain = item.get("gain_db")
            if gain is None:
                gain = auto_bgm_gain_db(pcm(Path(source["path"])), pcm(Path(music["path"])))
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not -60 <= gain <= 0:
                raise ValueError("BGM gain must be between -60 and 0 dB")
            prepared["bgm"] = {**music, "gain_db": float(gain)}
        rows.append(prepared)
    if expected_inputs is not None and set(expected_inputs) != {row["plan_id"] for row in rows}:
        raise ValueError("packaging configuration must cover the complete delivery")
    packaging_design.batch_effects([row["subtitle_design"] for row in rows])
    return rows


def prescaled_overlay(source: Path, target: Path) -> Path:
    """Scale a still overlay once; the encoder's identical per-frame scale is then a no-op."""
    result = subprocess.run([str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(source), "-vf",
                             "scale=1440:2560:flags=lanczos,format=rgba", "-frames:v", "1", str(target)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"overlay scaling failed ({result.returncode}): {result.stderr[-3000:]}")
    return target


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
            layers = row["nameplates"] + row["text_pins"] + ([row["disclaimer"]] if row["disclaimer"] else [])
            for layer in layers:
                scaled = prescaled_overlay(Path(layer["path"]), Path(font_directory) / f"layer-{index}.png")
                command += ["-loop", "1", "-framerate", "60", "-i", str(scaled)]
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
            run(command, cwd=temporary_dir)
            spec = video_spec(partial)
            if (spec["frames"] != frames or (spec["width"], spec["height"]) != (1440, 2560)
                    or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac" or not packaged_streams_ok(spec)):
                raise RuntimeError(f"packaged video specification mismatch: {spec}")
            os.replace(partial, output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {"plan_id": plan_id, "input": row["input"], "input_frames": row["spec"]["frames"],
            **({"text_pin_policy": row["text_pin_policy"]} if row.get("text_pin_policy") else {}),
            "final_speed": FINAL_SPEED, "output_frames": frames, "output_path": str(output.resolve()),
            "output_sha256": sha(output), "output_spec": spec, "subtitles": row["subtitles"],
            "nameplates": row["nameplates"],
            "text_pins": row["text_pins"], "disclaimer": row["disclaimer"], "bgm": row["bgm"],
            "subtitle_style": row["subtitle_style"],
            "design": track["record"],
            "video_encoding": encoding}


def render(config_path: Path, output_dir: Path, manifest_path: Path, clean_delivery: Path | None = None,
           clean_qc: Path | None = None, *, overwrite: bool = False, resume: bool = False) -> dict:
    if (OUTPUT_NAME.fullmatch(output_dir.name) and manifest_path.resolve().is_relative_to(output_dir.resolve())
            and not manifest_path.resolve().is_relative_to(runtime_directory(output_dir))):
        raise ValueError("packaging records inside the delivery must be in 临时文件")
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(manifest_path)
    if bool(clean_delivery) != bool(clean_qc):
        raise ValueError("montage packaging requires both the clean delivery and its clean QC")
    expected = None
    if clean_delivery:
        delivery = read(clean_delivery)
        validation = read(clean_qc)
        if (delivery.get("schema") != "video-montage-autonomous-clean/v260929"
                or validation.get("schema") != "video-montage-autonomous-clean-qc/v260929"
                or validation.get("decision") != "pass"
                or validation.get("clean_delivery", {}).get("sha256") != sha(clean_delivery)
                or Path(str(validation.get("clean_delivery", {}).get("path") or "")).resolve() != clean_delivery.resolve()):
            raise ValueError("passing clean QC bound to the clean delivery required")
        expected = {item["plan_id"]: item["output"]["sha256"] for item in delivery["results"]}
    rows = prepared_rows(config_path, expected)
    snapshots = [subtitle_output(output_dir, row["plan_id"]) for row in rows]
    config_snapshot = delivery_category(output_dir, "config") / config_path.name
    copies = [(config_path, config_snapshot)] + [
        (Path(row["subtitles"]["path"]), snapshot) for row, snapshot in zip(rows, snapshots)
    ]
    reserved = {}
    for path, label in [(manifest_path, "manifest"), *[(target, "snapshot") for _, target in copies],
                        *[(delivery_category(output_dir, "video") / f"{row['plan_id']}{suffix}", "render")
                          for row in rows for suffix in (".mp4", ".partial.mp4", ".ass")]]:
        resolved = path.resolve()
        if resolved in reserved:
            raise ValueError(f"packaging paths collide: {reserved[resolved]} and {label}: {resolved}")
        reserved[resolved] = label

    def dependency_paths(item):
        if isinstance(item, dict):
            if isinstance(item.get("path"), str):
                yield Path(item["path"]).resolve()
            for child in item.values():
                yield from dependency_paths(child)
        elif isinstance(item, list):
            for child in item:
                yield from dependency_paths(child)

    immutable = {path for row in rows for key, item in row.items() if key != "subtitles"
                 for path in dependency_paths(item)}
    immutable.update(path.resolve() for path in
                     (clean_delivery, clean_qc) if path)
    editable = {config_path.resolve(), *(Path(row["subtitles"]["path"]).resolve() for row in rows)}
    in_place_snapshots = {target.resolve() for source, target in copies if source.resolve() == target.resolve()}
    for path, label in reserved.items():
        if path in immutable or (path in editable and (label != "snapshot" or path not in in_place_snapshots)):
            raise ValueError(f"packaging output paths collide with input: {path}")

    # Publication can finish before Windows releases a temporary file held by a
    # concurrent preview. Recover only an exact, fully hash-bound publication.
    if resume and overwrite and manifest_path.is_file():
        previous = read(manifest_path)
        relocated_rows = json.loads(json.dumps(rows))
        for row in relocated_rows:
            if OUTPUT_NAME.fullmatch(output_dir.name):
                row["input"]["path"] = str((delivery_category(output_dir, "clean") / f"{row['plan_id']}.mp4").resolve())
        expected_config = relocated_config(config_path, relocated_rows, snapshots, config_snapshot)
        mode = "complete_autonomous" if clean_delivery else "standalone_test"
        valid = (previous.get("schema") == "video-montage-packaging-delivery/v1"
                 and previous.get("mode") == mode and previous.get("output_count") == len(rows)
                 and config_snapshot.is_file() and read(config_snapshot) == expected_config
                 and previous.get("config_snapshot_path") == str(config_snapshot.resolve())
                 and previous.get("config_snapshot_sha256") == sha(config_snapshot)
                 and previous.get("clean_delivery_sha256") == (sha(clean_delivery) if clean_delivery else None)
                 and previous.get("clean_qc_sha256") == (sha(clean_qc) if clean_qc else None))
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
        with ThreadPoolExecutor(workers(len(rows))) as pool:
            results = list(pool.map(lambda row: render_one(row, staging / "videos"), rows))
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
        mode = "complete_autonomous" if clean_delivery else "standalone_test"
        manifest = {"schema": "video-montage-packaging-delivery/v1", "mode": mode,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "subtitle_style": rows[0]["subtitle_style"],
                    "config_path": str(config_snapshot.resolve()), "config_sha256": sha(staged_config),
                    "config_snapshot_path": str(config_snapshot.resolve()), "config_snapshot_sha256": sha(staged_config),
                    "clean_delivery_path": str(clean_delivery.resolve()) if clean_delivery else None,
                    "clean_delivery_sha256": sha(clean_delivery) if clean_delivery else None,
                    "clean_qc_path": str(clean_qc.resolve()) if clean_qc else None,
                    "clean_qc_sha256": sha(clean_qc) if clean_qc else None,
                    "output_count": len(results), "results": results}
        staged_manifest = staging / "manifest.json"
        atomic(staged_manifest, manifest)
        publication.append((staged_manifest, manifest_path))
        publish_files(publication, temporary_root)
    if overwrite:
        invalidate_delivery_receipts(output_dir)
    return manifest


def pcm_stats(path: Path) -> dict:
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
    qc_path = Path(str(manifest.get("clean_qc_path") or ""))
    if not qc_path.is_file() or sha(qc_path) != manifest.get("clean_qc_sha256"):
        return False
    qc = read(qc_path)
    clean_row = next((r for r in qc.get("results", []) if r.get("plan_id") == item["plan_id"]), {})
    if qc.get("decision") != "pass" or clean_row.get("text_match") is not True or clean_row.get("output", {}).get("sha256") != item["input"]["sha256"]:
        return False
    # Rebuild the authorized cleanup proof from the bound clean render.
    clean_path = Path(str(manifest.get("clean_delivery_path") or ""))
    if not clean_path.is_file() or sha(clean_path) != manifest.get("clean_delivery_sha256"):
        return False
    clean_item = next((r for r in read(clean_path).get("results", []) if r.get("plan_id") == item["plan_id"]), {})
    if clean_item.get("output") != clean_row.get("output"):
        return False
    cleanup_render = read(require_ref(clean_item.get("render_evidence", {}), "clean cleanup render"))
    proof = delivery_mix_evidence(pcm(Path(item["output_path"])), item, cleanup_render)
    return proof.get("decision") == "pass" and proof == measured.get("mix_evidence")


def validate(manifest_path: Path, report_path: Path, autonomous_evidence: Path | None = None,
             autonomous_review: Path | None = None) -> dict:
    manifest = read(manifest_path)
    for row in manifest.get("results", []):
        output = Path(row.get("output_path", "")).resolve()
        delivery = output.parent.parent if output.parent.name == "成片" else output.parent
        if (OUTPUT_NAME.fullmatch(delivery.name) and report_path.resolve().is_relative_to(delivery)
                and not report_path.resolve().is_relative_to(runtime_directory(delivery))):
            raise ValueError("validation reports inside the delivery must be in 临时文件")
    failures = []
    if bool(autonomous_evidence) != bool(autonomous_review):
        raise ValueError("final evidence and its agent review must be provided together")
    automatic = read(autonomous_evidence) if autonomous_evidence else None
    agent = read(autonomous_review) if autonomous_review else None
    auto_findings = {}
    if automatic is not None:
        if (manifest.get("mode") != "complete_autonomous"
                or automatic.get("schema") != "video-montage-autonomous-final-evidence/v260929"
                or automatic.get("decision") != "pass"
                or automatic.get("manifest", {}).get("sha256") != sha(manifest_path)
                or Path(str(automatic.get("manifest", {}).get("path") or "")).resolve() != manifest_path.resolve()
                or agent.get("schema") != "video-montage-agent-review/v260929"
                or agent.get("stage") != "final" or agent.get("reviewer_role") != "agent"
                or agent.get("evidence_sha256") != sha(autonomous_evidence)):
            failures.append("autonomous_review_binding")
        auto_rows = automatic.get("results", [])
        agent_rows = agent.get("outputs", [])
        if (not isinstance(auto_rows, list) or not isinstance(agent_rows, list)
                or len(auto_rows) != len(manifest.get("results", []))
                or len(agent_rows) != len(auto_rows)):
            failures.append("autonomous_review_scope")
        else:
            auto_by_id = {row.get("plan_id"): row for row in auto_rows}
            agent_by_id = {row.get("plan_id"): row for row in agent_rows}
            ids = {row.get("plan_id") for row in manifest.get("results", [])}
            if len(auto_by_id) != len(auto_rows) or len(agent_by_id) != len(agent_rows) or set(auto_by_id) != ids or set(agent_by_id) != ids:
                failures.append("autonomous_review_scope")
            else:
                auto_findings = agent_by_id
                for item in manifest["results"]:
                    pid = item["plan_id"]
                    measured, finding = auto_by_id[pid], agent_by_id[pid]
                    if (measured.get("decision") != "pass" or not verify_autonomous_speech(measured, item, manifest)
                            or measured.get("subtitle_timing_pass") is not True
                            or not isinstance(measured.get("cut_pcm"), list)
                            or any(cut.get("decision") != "pass" for cut in measured["cut_pcm"])
                            or measured.get("output", {}).get("sha256") != item.get("output_sha256")
                            or Path(str(measured.get("output", {}).get("path") or "")).resolve() != Path(item["output_path"]).resolve()
                            or measured.get("metrics", {}).get("clipped_samples") != 0
                            or measured.get("subtitle_cue_count") != item.get("subtitles", {}).get("cue_count")
                            or (measured.get("voice_over_bgm_db") is not None and measured["voice_over_bgm_db"] < MIN_VOICE_OVER_MUSIC_DB)
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
                        if measured.get("design") != item["design"] or not set(required).issubset(indices):
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
        qc_path = Path(str(manifest.get("clean_qc_path") or ""))
        if not qc_path.is_file() or sha(qc_path) != manifest.get("clean_qc_sha256"):
            failures.append("clean_qc_changed")
        elif clean_valid:
            clean_report = read(qc_path)
            if (manifest.get("mode") != "complete_autonomous"
                    or read(clean).get("schema") != "video-montage-autonomous-clean/v260929"
                    or clean_report.get("schema") != "video-montage-autonomous-clean-qc/v260929"
                    or clean_report.get("decision") != "pass"
                    or clean_report.get("clean_delivery", {}).get("sha256") != manifest.get("clean_delivery_sha256")):
                failures.append("clean_qc_binding")
    checks = []
    snapshot_rows = {}
    snapshot_pin_policy = None
    if config_snapshot.is_file() and sha(config_snapshot) == manifest.get("config_snapshot_sha256"):
        snapshot = read(config_snapshot)
        snapshot_rows = {item.get("plan_id"): item for item in snapshot.get("outputs", [])}
        snapshot_pin_policy = snapshot.get("text_pin_policy")
    results = manifest.get("results", [])
    ids = [row.get("plan_id") for row in results]
    if (manifest.get("output_count") != len(results) or not results
            or any(not isinstance(pid, str) or not PLAN_ID.fullmatch(pid) for pid in ids)
            or len({str(pid).casefold() for pid in ids}) != len(ids)):
        failures.append("result_scope")
    for row in results:
        plan_id = row.get("plan_id")
        changes = []
        for key in ("input", "subtitles", "subtitle_snapshot", "disclaimer", "bgm"):
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
        if snapshot_pin_policy or row.get("text_pin_policy"):
            try:
                if snapshot_pin_policy != TEXT_PIN_POLICY or row.get("text_pin_policy") != snapshot_pin_policy:
                    raise ValueError("text pin policy binding changed")
                raw = snapshot_rows[plan_id].get("text_pins", [])
                text_pin_coverage(raw, row["input_frames"])
                expected = [{**resolve_overlay(config_snapshot.parent, item['path'], item['sha256']),
                             'start_frame': item['start_frame'], 'end_frame_exclusive': item['end_frame_exclusive']}
                            for item in raw]
                if row.get("text_pins") != expected:
                    raise ValueError("text pin timeline changed")
                text_pin_coverage(row["text_pins"], row["input_frames"])
            except (ValueError, TypeError, KeyError, OSError):
                changes.append("required_text_pin_coverage_changed")
        if row.get("nameplates") or snapshot_rows.get(plan_id, {}).get("nameplates"):
            try:
                expected = prepare_nameplates(snapshot_rows.get(plan_id, {}), config_snapshot.parent, row["input_frames"])
                if row.get("nameplates") != expected:
                    changes.append("nameplate_timeline_changed")
                for item in row.get("nameplates", []):
                    path = Path(item["path"])
                    if not path.is_file() or sha(path) != item["sha256"]:
                        changes.append("nameplate_changed")
            except (KeyError, TypeError, ValueError, OSError):
                changes.append("nameplate_timeline_changed")
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
        finding = auto_findings.get(plan_id)
        if row.get("subtitle_style"):
            font = row["subtitle_style"]["font"]
            if not Path(font["path"]).is_file() or sha(Path(font["path"])) != font["sha256"]:
                changes.append("subtitle_font_changed")
        if row.get("design"):
            for resource in row["design"].get("resources", []):
                path = Path(resource["path"])
                if not path.is_file() or sha(path) != resource["sha256"]:
                    changes.append("design_resource_changed")
            if automatic is not None and not packaging_design.review_ok(row["design"], finding):
                changes.append("design_review")
        checks.append({"plan_id": plan_id, "decision": "pass" if not changes else "reject", "failures": changes,
                       "audio": audio if output.is_file() and "output_changed" not in changes else None,
                       "visual_review": {"subtitle_cues": row["subtitles"]["cue_count"],
                                         "nameplate_count": len(row.get("nameplates", [])),
                                         "text_pins": len(row.get("text_pins", [])), "disclaimer": bool(row.get("disclaimer")),
                                         "status": "pass" if finding and finding.get("visual_pass") is True else "pending_review"},
                       "sound_review": {"bgm": bool(row.get("bgm")), "status": "pass" if automatic is not None and not any(f.startswith(f"autonomous_review:{plan_id}") for f in failures) else "pending_review"}})
        failures += [f"{plan_id}:{change}" for change in changes]
    decision = "reject" if failures else "pass" if automatic is not None else "technical_pass_pending_review"
    report = {"schema": "video-montage-packaging-validation/v1", "decision": decision,
              "manifest_path": str(manifest_path.resolve()), "manifest_sha256": sha(manifest_path),
              "autonomous_evidence_sha256": sha(autonomous_evidence) if autonomous_evidence else None,
              "autonomous_review_sha256": sha(autonomous_review) if autonomous_review else None,
              "autonomous_review": "pass" if automatic is not None and not failures else "pending_or_rejected" if automatic is not None else "not_applicable",
              "failures": failures, "results": checks}
    atomic(report_path, report)
    return report

