"""Optional, hash-bound packaging of an already completed montage video."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / "dependencies/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "dependencies/ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = ROOT / "dependencies/models"
SCHEMA = "video-montage-packaging/v1"
PLAN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


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
        text = "\n".join(lines[1:]).strip()
        if not text or len(text.splitlines()) > 2 or any(display_width(line) > 54 for line in text.splitlines()) or not 0 <= start < end <= total_ms + 20:
            raise ValueError("subtitle cue is empty, over two lines, or outside video")
        if cues and start < cues[-1]["end_ms"]:
            raise ValueError("subtitle cues overlap or are out of order")
        cues.append({"start_ms": start, "end_ms": end, "text": text})
    return cues


def display_width(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def wrap_text(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    if display_width(text) <= 28:
        return text
    midpoint = len(text) // 2
    boundary = min(range(1, len(text)), key=lambda index: abs(display_width(text[:index]) - display_width(text[index:])) + (0 if abs(index-midpoint) <= 3 else 4))
    return text[:boundary] + "\n" + text[boundary:]


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
            next_text = "".join(item["word"] for item in current) + word["word"]
            if current and (display_width(next_text) > 54 or float(word["end"]) - float(current[0]["start"]) > 3.5):
                rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": wrap_text("".join(item["word"] for item in current))})
                current = []
            current.append(word)
            if re.search(r"[。！？!?]$", str(word["word"])) and float(word["end"]) - float(current[0]["start"]) >= 0.7:
                rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": wrap_text("".join(item["word"] for item in current))})
                current = []
        if current:
            rows.append({"start": float(current[0]["start"]), "end": float(current[-1]["end"]), "text": wrap_text("".join(item["word"] for item in current))})
    if not rows:
        raise RuntimeError("Whisper produced no subtitle cues")
    duration_ms = round(video_spec(input_path)["duration"] * 1000)
    blocks = []
    previous_end = 0
    for index, row in enumerate(rows, 1):
        start = max(previous_end, round(row["start"] * 1000))
        end = min(duration_ms, max(start + 100, round(row["end"] * 1000)))
        if start >= end:
            continue
        blocks.append(f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{row['text']}")
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


def write_ass(path: Path, cues: list[dict]) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1440
PlayResY: 2560
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,90,90,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for cue in cues:
        clean = cue["text"].replace("{", "（").replace("}", "）").replace("\\", "／").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{ass_time(cue['start_ms'])},{ass_time(cue['end_ms'])},Default,,0,0,0,,{clean}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")


def prepared_rows(config_path: Path, expected_inputs: dict[str, str] | None = None) -> list[dict]:
    config = read(config_path)
    if config.get("schema") != SCHEMA or not isinstance(config.get("outputs"), list) or not config["outputs"]:
        raise ValueError(f"{SCHEMA} with nonempty outputs required")
    base = config_path.resolve().parent
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
    write_ass(ass, row["cues"])
    command = [str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", row["input"]["path"]]
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
    filters.append(f"{current}ass=filename={ass.name},format=yuv420p[vout]")
    if row["bgm"]:
        command += ["-stream_loop", "-1", "-i", row["bgm"]["path"]]
        filters.append(f"[{index}:a]volume={row['bgm']['gain_db']}dB[bgm]")
        filters.append("[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
        audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        filters.append("[0:a]anull[aout]")
        audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    frames = row["spec"]["frames"]
    command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-frames:v", str(frames),
                "-r", "60", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *audio_codec,
                "-t", f"{frames / 60:.6f}", "-movflags", "+faststart", str(partial)]
    try:
        run(command, cwd=output_dir)
        spec = video_spec(partial)
        if (spec["frames"] != frames or (spec["width"], spec["height"]) != (1440, 2560)
                or spec["video_codec"] != "h264" or spec["audio_codec"] != "aac" or not packaged_streams_ok(spec)):
            raise RuntimeError(f"packaged video specification mismatch: {spec}")
        os.replace(partial, output)
        ass.unlink()
    except Exception:
        partial.unlink(missing_ok=True)
        ass.unlink(missing_ok=True)
        raise
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
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "reburn_config.json"
    atomic(config_path, {"schema": SCHEMA, "outputs": rows})
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
        reviews = {row.get("plan_id"): row for row in review.get("results", []) if isinstance(row, dict)}
    if manifest.get("schema") != "video-montage-packaging-delivery/v1":
        failures.append("schema")
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
    if review is not None and set(reviews) != {row.get("plan_id") for row in results}:
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
