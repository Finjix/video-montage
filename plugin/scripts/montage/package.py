"""Packaged delivery: designed subtitles, nameplates, one text pin, disclaimer and BGM over each clean video.

Each output is encoded only when its inputs changed, checked (frames, loudness, peak, clipping, voice over
music) and then published straight into the delivery folder.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from montage import audio as AUDIO
from montage import bgm as BGM
from montage import job as JOB
from montage import catalog as CATALOG
from montage import rules, runtime, text_library
from montage.common import FFMPEG, digest, log, pool, probe, read, run, sha, write
from montage.packaging import design, design_renderer, flower_effects, nameplates, segmentation
from montage.packaging import fonts as subtitle_fonts

VERSION = 2
SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
ENTRANCE_CYCLE = ("bounce_up", "ice_drift", "shout_wave")
SHOUT_MAX_CHARS = 5


# ----------------------------------------------------------------------------------- subtitles

def parse_time(value: str) -> int:
    match = SRT_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid SRT time {value!r} (HH:MM:SS,mmm)")
    hour, minute, second, milli = map(int, match.groups())
    return ((hour * 60 + minute) * 60 + second) * 1000 + milli


def parse_srt(path: Path, duration_ms: int) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    cues = []
    for block in re.split(r"\n\s*\n", raw) if raw else []:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].isdigit():
            lines = lines[1:]
        if len(lines) != 2 or " --> " not in lines[0]:
            raise ValueError(f"{path.name}: each cue is an index, one 'start --> end' line and one text line: {block[:60]!r}")
        start, end = (parse_time(part) for part in lines[0].split(" --> ", 1))
        segmentation.require_line_limit(lines[1])
        if not 0 <= start < end <= duration_ms or (cues and start < cues[-1]["end_ms"]):
            raise ValueError(f"{path.name}: cue {lines[1]!r} is outside the video, empty or overlaps the previous cue")
        cues.append({"start_ms": start, "end_ms": end, "text": lines[1]})
    if not cues:
        raise ValueError(f"{path.name}: no subtitle cues")
    return cues


def check_cues(cues: list[dict], output: dict, terms: list[str]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    spans = [(round(clip["out_in"] * 1000 / 60), round(clip["out_out"] * 1000 / 60)) for clip in output["clips"]]
    for cue in cues:
        if not any(start <= cue["start_ms"] and cue["end_ms"] <= end for start, end in spans):
            errors.append(f"subtitle {cue['text']!r} crosses a cut; keep each cue inside one clip")
    text = "".join(rules.normalize(cue["text"]) for cue in cues)
    speech = "".join(rules.normalize(clip["text"]) for clip in output["clips"])
    if text != speech:
        warnings.append("subtitle text differs from the transcript (kept as written)")
    edges, cursor = set(), 0
    for cue in cues[:-1]:
        cursor += len(rules.normalize(cue["text"]))
        edges.add(cursor)
    for term in terms:
        for match in re.finditer(re.escape(rules.normalize(term)), text):
            if any(match.start() < edge < match.end() for edge in edges):
                errors.append(f"{term} is split across subtitle cues")
    for issue in segmentation.reading_issues(cues):
        if issue["code"] == "short_reading":
            warnings.append(f"cue {issue['index']} shows {issue['final_duration_ms']} ms (< 800)")
        elif issue["code"] == "dependent_head":
            warnings.append(f"cue {issue['index']} starts with a dependent particle: {issue['text']}")
    return errors, warnings


# ----------------------------------------------------------------------------------- design

def entrance(effect: str, cue: dict, longest: int = 350) -> dict:
    if effect == "none":
        return {"effect": "none"}
    duration = min(longest, (cue["end_ms"] - cue["start_ms"]) // 2)
    return {"effect": effect, "duration_ms": duration} if duration >= 50 else {"effect": "none"}


def default_design(cues: list[dict], games: list[str], ordinal: int, font: str, flower: str) -> dict:
    """Yellow static narration; the opening cue and a standalone game name get an entrance."""
    rows = []
    for index, cue in enumerate(cues, 1):
        row = {"index": index, "text": cue["text"], "color": "yellow", "font_size": 8}
        standalone = rules.normalize(cue["text"]) in {rules.normalize(g) for g in games}
        if standalone:
            row["entrance"] = entrance("ice_drift", cue, min(200, cue["end_ms"] - cue["start_ms"] - 600))
        elif index == 1:
            effect = ENTRANCE_CYCLE[ordinal % 3]
            if effect == "shout_wave" and segmentation.character_count(cue["text"]) > SHOUT_MAX_CHARS:
                effect = "bounce_up"
            row["entrance"] = entrance(effect, cue)
        else:
            row["entrance"] = {"effect": "none"}
        rows.append(row)
    return {"font": font, "game_names": games, "game_flower": flower, "cues": rows}


def apply_overrides(value: dict, overrides: list[dict], cues: list[dict]) -> list[str]:
    warnings = []
    allowed = {"index", "text", "color", "spans", "font_size", "entrance", "x", "y", "max_width"}
    for row in overrides:
        if not isinstance(row, dict) or type(row.get("index")) is not int or not 1 <= row["index"] <= len(cues):
            raise ValueError(f"cue override needs a valid 1-based index: {row}")
        if set(row) - allowed:
            raise ValueError(f"cue override fields: {sorted(allowed)}")
        target = value["cues"][row["index"] - 1]
        if row.get("text") is not None and row["text"] != target["text"]:
            warnings.append(f"cue {row['index']} override skipped: text is now {target['text']!r}")
            continue
        for key in ("color", "spans", "font_size"):
            if key in row:
                target[key] = row[key]
        if "entrance" in row:
            chosen = row["entrance"]
            target["entrance"] = entrance(chosen, cues[row["index"] - 1]) if isinstance(chosen, str) else chosen
        layout = {key: row[key] for key in ("x", "y", "max_width") if key in row}
        if layout:
            target["layout"] = layout
    return warnings


def balance_entrances(values: list[dict], cues_by_output: list[list[dict]]) -> None:
    """Batches of 2+ videos use min(3, n) different entrances: add a missing one on a fitting quiet cue."""
    required = min(3, len(values)) if len(values) > 1 else 0
    used = {row["entrance"]["effect"] for value in values for row in value["cues"]} - {"none"}
    for effect in ENTRANCE_CYCLE:
        if len(used) >= required or effect in used:
            continue
        for value, cues in zip(values, cues_by_output):
            candidates = [row for row in value["cues"][1:] if row["entrance"]["effect"] == "none"
                          and (effect != "shout_wave" or segmentation.character_count(row["text"]) <= SHOUT_MAX_CHARS)]
            if candidates:
                row = candidates[0]
                row["entrance"] = entrance(effect, cues[row["index"] - 1])
                used.add(effect)
                break


# ----------------------------------------------------------------------------------- overlays and BGM

def overlay(path: str) -> dict:
    file = Path(path)
    if not file.is_file() or file.suffix.lower() != ".png":
        raise ValueError(f"overlay must be an existing PNG: {path}")
    with Image.open(file) as image:
        if image.width * 16 != image.height * 9:
            raise ValueError(f"overlay must be 9:16: {path}")
        low, high = image.convert("RGBA").getchannel("A").getextrema()
        if low == 255 or high == 0:
            raise ValueError(f"overlay needs visible content and transparency: {path}")
    return {"path": str(file.resolve()), "sha256": sha(file)}


def pick(options, index: int):
    if isinstance(options, list):
        return options[index % len(options)] if options else None
    return options


def find_assets(asset_root: Path, words: tuple[str, ...]) -> list[str]:
    """Usable 9:16 PNGs under folders named by `words` (the same discovery `init` reports)."""
    return sorted(str(p) for p in asset_root.rglob("*.png") if any(w in str(p.relative_to(asset_root)).lower() for w in words)
                  and not JOB.overlay_problem(p))


# ----------------------------------------------------------------------------------- encode

def encode(item: dict, work: Path, target: Path) -> dict:
    """Burn the design track and overlays over the clean video and mix the BGM; returns the design record."""
    with TemporaryDirectory(prefix=f"{item['plan_id']}-", dir=work) as folder:
        folder = Path(folder)
        track = design_renderer.prepare_track(folder, item["cues"], item["design"], item["font_path"], item["frames"])
        styles = [span["flower"] for cue in item["design"]["cues"] for span in cue["spans"] if span.get("flower")]
        encoding = flower_effects.video_encoding(styles)
        command = [str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", item["clean"]]
        filters, current, index = [], "[0:v]", 1
        for layer in item["layers"]:
            if "x" in layer:
                # A nameplate is already cut to its visible size at frame scale.
                command += ["-loop", "1", "-framerate", "60", "-i", layer["path"]]
            else:
                scaled = folder / f"layer-{index}.png"
                run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", layer["path"], "-vf",
                     "scale=1440:2560:flags=lanczos,format=rgba", "-frames:v", "1", scaled])
                command += ["-loop", "1", "-framerate", "60", "-i", str(scaled)]
            enable = (f":enable='gte(n,{layer['start_frame']})*lt(n,{layer['end_frame_exclusive']})'"
                      if "start_frame" in layer else "")
            filters.append(f"{current}[{index}:v]overlay={layer.get('x', 0)}:{layer.get('y', 0)}:format=auto:shortest=0"
                           f"{enable}[v{index}]")
            current, index = f"[v{index}]", index + 1
        command += ["-f", "concat", "-safe", "0", "-i", track["path"]]
        filters.append(f"[{index}:v]fps=60:round=up,format=rgba[track]")
        filters.append(f"{current}[track]overlay=0:{track['y']}:format={encoding['overlay_format']}:eof_action=pass,"
                       f"format={encoding['pixel_format']}[vout]")
        index += 1
        command += ["-stream_loop", "-1", "-i", item["bgm"]["path"]]
        filters.append(f"[0:a]volume={item['voice_gain_db']}dB[voice];[{index}:a]volume={item['bgm']['gain_db']}dB[music];"
                       "[voice][music]amix=inputs=2:duration=first:normalize=0[aout]")
        command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-frames:v", str(item["frames"]),
                    "-r", "60", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(encoding["crf"]),
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{item['frames'] / 60:.6f}",
                    "-movflags", "+faststart", str(target)]
        run(command, cwd=folder)
        return {"design": track["record"], "encoding": encoding}


def remix(item: dict, packaged: Path, target: Path, limit: bool = False) -> None:
    """Rebuild only the audio (picture copied) with new gains; `limit` holds peaks under -2 dBFS."""
    limiter = ",alimiter=limit=0.794:level=0:attack=5:release=50" if limit else ""
    run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", packaged, "-i", item["clean"], "-stream_loop", "-1", "-i", item["bgm"]["path"],
         "-filter_complex", f"[1:a]volume={item['voice_gain_db']}dB[v];[2:a]volume={item['bgm']['gain_db']}dB[m];"
         f"[v][m]amix=inputs=2:duration=first:normalize=0{limiter}[a]", "-map", "0:v:0", "-map", "[a]", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{item['frames'] / 60:.6f}", "-movflags", "+faststart", target])


def correct_loudness(item: dict, staged: Path, work: Path) -> dict:
    """Shift voice and music together until the mix lands on the target; a limiter only when peaks forbid it."""
    loudness, limit = AUDIO.measure(staged), False
    for _ in range(3):
        if loudness["lufs"] is None:
            break
        offset = AUDIO.TARGET_LUFS - loudness["lufs"]
        room = AUDIO.DELIVERY_PEAK_DB - loudness["true_peak"] if loudness["true_peak"] is not None else offset
        if abs(offset) <= 0.5 and room >= 0:
            break
        # Each remix starts again from the clean audio, so once peaks needed the limiter it stays on.
        limit = limit or offset > room + 0.5
        shift = offset if limit else min(offset, room)
        item["voice_gain_db"] = round(item["voice_gain_db"] + shift, 2)
        item["bgm"]["gain_db"] = round(item["bgm"]["gain_db"] + shift, 2)
        corrected = work / f"{item['plan_id']}-mix.mp4"
        remix(item, staged, corrected, limit=limit)
        os.replace(corrected, staged)
        loudness = AUDIO.measure(staged)
    return loudness


def clipped_stereo(path: Path) -> int:
    command = [str(FFMPEG), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "2", "-ar", "48000", "-f", "s16le", "-"]
    count = 0
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        while block := process.stdout.read(2 * 48000 * 4):
            count += int(np.count_nonzero(np.abs(np.frombuffer(block, dtype="<i2").astype(np.int32)) >= 32760))
    return count


def finish(item: dict, work: Path) -> dict:
    """Encode (or reuse), correct the loudness once, check and return the result row."""
    started = time.monotonic()
    staged = work / f"{item['plan_id']}.mp4"
    reused = item.get("previous") is not None
    if reused:
        result = item["previous"]
        if Path(result["path"]).is_file() and sha(Path(result["path"])) == result["sha256"]:
            log(f"package: {item['plan_id']} unchanged")
            return {**result, "bgm_auto": item["bgm_auto"], "nameplates": item["nameplates"], "reused": True}
    result = encode(item, work, staged)
    loudness = correct_loudness(item, staged, work)
    errors = []
    info = probe(staged)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    streams = [s["codec_type"] for s in info["streams"]]
    if int(video.get("nb_frames") or 0) != item["frames"] or streams.count("video") != 1 or streams.count("audio") != 1 or "subtitle" in streams:
        errors.append(f"packaged video has {video.get('nb_frames')} frames / streams {streams}; expected {item['frames']} frames, 1 video + 1 audio")
    if loudness["lufs"] is None or abs(loudness["lufs"] - AUDIO.TARGET_LUFS) > AUDIO.TOLERANCE_LU:
        errors.append(f"loudness {loudness['lufs']} LUFS (target {AUDIO.TARGET_LUFS} +/- {AUDIO.TOLERANCE_LU})")
    if loudness["true_peak"] is not None and loudness["true_peak"] > AUDIO.DELIVERY_PEAK_DB + 0.5:
        errors.append(f"true peak {loudness['true_peak']} dBTP (max {AUDIO.DELIVERY_PEAK_DB})")
    clipping = clipped_stereo(staged)
    if clipping > 3:
        errors.append(f"{clipping} clipped samples")
    if item["voice_over_music_db"] < AUDIO.MIN_VOICE_OVER_MUSIC_DB:
        errors.append(f"voice only {item['voice_over_music_db']} dB over the BGM (needs {AUDIO.MIN_VOICE_OVER_MUSIC_DB})")
    log(f"package: {item['plan_id']} encoded in {time.monotonic() - started:.1f} s ({loudness['lufs']} LUFS)"
        + (f", {len(errors)} problems" if errors else ""))
    return {"plan_id": item["plan_id"], "key": item["key"], "staged": str(staged), "errors": errors, "frames": item["frames"],
            "clean": {"path": item["clean"], "sha256": item["clean_sha256"]}, "subtitle": item["subtitle"],
            "lufs": loudness["lufs"], "true_peak": loudness["true_peak"], "voice_gain_db": item["voice_gain_db"],
            "bgm": item["bgm"], "voice_over_music_db": item["voice_over_music_db"], "bgm_auto": item["bgm_auto"],
            "nameplates": item["nameplates"], **result}


def final_sheet(row: dict, video: Path, target: Path) -> None:
    """Each nameplate in the middle of its window (labelled with its spot), then up to 16 settled cues."""
    plates = {(p["start_frame"] + p["end_frame_exclusive"]) // 2: p["position"] for p in row["nameplates"]["rows"]}
    events = row["design"]["events"]
    numbers = sorted({min(row["frames"] - 1, (e["start_frame"] + e["entrance_frames"] + e["end_frame_exclusive"]) // 2)
                      for e in events} - set(plates))[:16]
    images = CATALOG.grab(video, [*plates, *numbers])
    tiles = [(images[n], f"f{n} {spot}") for n, spot in plates.items()] + [(images[n], f"f{n}") for n in numbers]
    CATALOG.tile_sheet(tiles, target, title=f"{row['plan_id']} packaged (nameplates with their spot, then settled cues)")


# ----------------------------------------------------------------------------------- batch

def prepare_items(job: Path, state: dict, config: dict) -> tuple[list[dict], dict[str, list[str]], dict[str, list[str]]]:
    asset_root = Path(state["order"]["asset_root"])
    library = text_library.load(job)
    games_all = [e["text"] for e in library["entries"] if e["category"] == "game"]
    terms = [e["text"] for e in library["entries"]]
    sources = {row["person_id"]: row["nameplate"] for row in state["celebrities"]}
    plates = {}
    records = [read(path) for path in sorted((job / "clean").glob("*.json"))]
    plan_ids = [row["plan_id"] for row in read(job / "config/plan.json")["outputs"]]
    by_id = {record["plan_id"]: record for record in records}
    missing = [pid for pid in plan_ids if pid not in by_id]
    if len(missing) == len(plan_ids):
        raise ValueError("render first: no clean videos")
    defaults = config.get("defaults", {})
    overrides = config.get("outputs", {})
    unknown = set(overrides) - set(plan_ids)
    if unknown:
        raise ValueError(f"config names unknown outputs {sorted(unknown)}")
    pins = defaults.get("text_pins") or find_assets(asset_root, ("文字钉", "pin"))
    disclaimer = defaults.get("disclaimer") or (lambda found: found[0] if len(found) == 1 else None)(find_assets(asset_root, ("免责", "disclaimer")))
    tracks = defaults.get("bgm") or [row["path"] for row in BGM.available(job, asset_root)]
    manifest_path = job / "delivery.json"
    previous = {row["plan_id"]: row for row in read(manifest_path)["outputs"]} if manifest_path.is_file() else {}
    items, errors, warnings = [], {}, {}
    cues_by_output, values = [], []
    for ordinal, pid in enumerate(plan_ids):
        if pid not in by_id:
            errors[pid] = ["no clean video: run render for this plan first"]
            continue
        record, own = by_id[pid], overrides.get(pid, {})
        output = record["output"]
        problems, notes = list(record["errors"]), []
        subtitle = job / "subtitles" / f"subtitle-{pid}.txt"
        try:
            cues = parse_srt(subtitle, round(record["frames"] * 1000 / 60))
        except ValueError as error:
            errors[pid] = [str(error)]
            continue
        cue_errors, cue_warnings = check_cues(cues, output, terms)
        problems += cue_errors
        notes += cue_warnings
        if record.get("draft_sha256") and sha(subtitle) != record["draft_sha256"]:
            notes.append("subtitle file edited after the draft")
        speech = "".join(cue["text"] for cue in cues)
        games = [g for g in games_all if g in speech]
        value = default_design(cues, games, ordinal, own.get("font", defaults.get("font", "w8")),
                               own.get("game_flower", defaults.get("game_flower", "ice2")))
        try:
            notes += apply_overrides(value, own.get("cues", []), cues)
        except ValueError as error:
            problems.append(str(error))
        layers = []
        spots = own.get("nameplates", {})
        try:
            people = [window["person_id"] for window in output["nameplates"]]
            if not isinstance(spots, dict) or any(spot not in nameplates.SPOTS for spot in spots.values()):
                raise ValueError(f"outputs.{pid}.nameplates maps person IDs to one of {', '.join(nameplates.SPOTS)}")
            if set(spots) - set(people):
                raise ValueError(f"outputs.{pid}.nameplates names {', '.join(sorted(set(spots) - set(people)))}, who has no "
                                 f"nameplate in {pid} (nameplates: {', '.join(people)})")
            for window in output["nameplates"]:
                person = window["person_id"]
                if person not in plates:
                    plates[person] = nameplates.trim(sources[person], person, job / "nameplates")
                layers.append({"path": plates[person]["path"], "sha256": plates[person]["sha256"], **window,
                               "position": spots.get(person)})
            pin = own.get("text_pin") or pick(pins, ordinal)
            if not pin:
                raise ValueError("no text pin: set defaults.text_pins or outputs.<id>.text_pin")
            layers.append({**overlay(pin), "start_frame": 0, "end_frame_exclusive": record["frames"]})
            chosen = own.get("disclaimer") or disclaimer
            if not chosen:
                raise ValueError("no disclaimer: set defaults.disclaimer")
            layers.append(overlay(chosen))
        except (ValueError, OSError) as error:
            problems.append(str(error))
        track = own.get("bgm") or pick(tracks, ordinal)
        if not track:
            problems.append("no BGM: the asset pack has no audio and nothing is registered; see `bgm-assets`")
        items.append({"plan_id": pid, "clean": record["path"], "clean_sha256": record["sha256"], "frames": record["frames"],
                      "cues": cues, "design": value, "layers": layers, "bgm_path": track, "subtitle": str(subtitle),
                      "previous": previous.get(pid)})
        values.append(value)
        cues_by_output.append(cues)
        if problems:
            errors[pid] = problems
        if notes:
            warnings[pid] = notes
    balance_entrances(values, cues_by_output)
    prepared = []
    for item, cues in zip(items, cues_by_output):
        try:
            item["design"] = design.prepare(item["design"], cues, item["frames"])
        except ValueError as error:
            errors.setdefault(item["plan_id"], []).append(f"design: {error}")
    try:
        design.batch_effects([item["design"] for item in items])
    except (ValueError, KeyError) as error:
        errors.setdefault("batch", []).append(f"design: {error}")
    shas = []
    for item in items:
        if not item["bgm_path"]:
            continue
        music = Path(item["bgm_path"])
        try:
            origin = BGM.require_authorized(job, asset_root, {"path": str(music), "sha256": sha(music)})
        except (ValueError, OSError) as error:
            errors.setdefault(item["plan_id"], []).append(str(error))
            continue
        shas.append(origin["sha256"])
        item["bgm"] = {"path": origin["path"], "sha256": origin["sha256"], "origin": origin["origin"]}
    try:
        BGM.require_distribution(shas, state.get("decisions", {}).get("bgm_repetition"))
    except ValueError as error:
        errors.setdefault("batch", []).append(str(error))
    ready = [item for item in items if item["plan_id"] not in errors and "bgm" in item]
    log(f"package: {len(ready)} of {len(plan_ids)} outputs ready; placing nameplates and setting BGM levels")
    place_nameplates(ready, plates, warnings)
    gains = bgm_gains(ready)
    for item in ready:
        item["font_path"] = subtitle_fonts.FONTS[item["design"]["font"]]["path"]
        item["voice_gain_db"] = 0.0
        item["bgm_auto"] = gains[item["plan_id"]]
        item["bgm"]["gain_db"] = item["bgm_auto"]["gain_db"]
        item["voice_over_music_db"] = item["bgm_auto"]["voice_over_music_db"]
        item["key"] = digest([VERSION, AUDIO.TARGET_LUFS, item["clean_sha256"], sha(Path(item["subtitle"])), item["design"]["cues"],
                              item["design"]["font"], item["layers"], item["bgm"]])
        if not (item["previous"] and item["previous"].get("key") == item["key"]):
            item["previous"] = None
        prepared.append(item)
    return prepared, errors, warnings


def place_nameplates(items: list[dict], plates: dict[str, dict], warnings: dict[str, list[str]]) -> None:
    """Give every nameplate layer its spot and position beside the speaker: the config's spot, or the nearest that
    leaves the sampled faces clear. The face check is reused from the last run while the clean video, the window and
    the plate are unchanged."""
    def window(layer):
        return layer["person_id"], layer["start_frame"], layer["end_frame_exclusive"], layer["sha256"]
    todo = []
    for item in items:
        item["plates"] = [layer for layer in item["layers"] if "person_id" in layer]
        known = (item["previous"] or {}).get("nameplates") or {}
        rows = ({(r["person_id"], r["start_frame"], r["end_frame_exclusive"], r["plate"]): r for r in known["rows"]}
                if known.get("version") == nameplates.VERSION and known.get("clean") == item["clean_sha256"] else {})
        item["plate_rows"] = [rows.get(window(layer)) for layer in item["plates"]]
        if None in item["plate_rows"]:
            todo.append(item)
    masks = {person: nameplates.mask(plate) for person, plate in plates.items()} if todo else {}

    def detect(item):
        found = nameplates.faces(Path(item["clean"]), sorted({n for layer in item["plates"] for n in nameplates.samples(layer)}))
        rows = []
        for layer in item["plates"]:
            spot, where = nameplates.place(plates[layer["person_id"]], masks[layer["person_id"]], found, nameplates.samples(layer))
            rows.append({"person_id": layer["person_id"], "start_frame": layer["start_frame"],
                         "end_frame_exclusive": layer["end_frame_exclusive"], "plate": layer["sha256"], "auto": spot, "spots": where})
        return rows
    if todo:
        log(f"package: finding faces under {sum(len(item['plates']) for item in todo)} nameplates")
        with pool(len(todo)) as executor:
            for item, rows in zip(todo, executor.map(detect, todo)):
                item["plate_rows"] = rows
    for item in items:
        rows = []
        for layer, row in zip(item["plates"], item["plate_rows"]):
            configured = layer["position"] is not None
            chosen = layer["position"] or row["auto"]
            cover = {spot: value["cover"] for spot, value in row["spots"].items()}
            layer.update(position=chosen, x=row["spots"][chosen]["x"], y=row["spots"][chosen]["y"])
            rows.append({**row, "position": chosen, "configured": configured})
            clear = [spot for spot in nameplates.preference(plates[layer["person_id"]]) if cover[spot] <= nameplates.CLEAR]
            where = f"nameplate {layer['person_id']} (f{layer['start_frame']}-{layer['end_frame_exclusive'] - 1})"
            # A configured spot is a decision already taken; it is only questioned when another spot is clear.
            if cover[chosen] <= nameplates.CLEAR or (configured and not clear):
                continue
            if not configured:
                note = (f"{where} touches a face at every spot (least: {chosen} {cover[chosen]:.0%}); check its tile "
                        f"in sheets/{item['plan_id']}-final.jpg and set outputs.{item['plan_id']}.nameplates if another spot reads better")
            else:
                note = f"{where} at {chosen} covers {cover[chosen]:.0%} of a face" + (f"; {clear[0]} is clear" if clear else "")
            warnings.setdefault(item["plan_id"], []).append(note)
        item["nameplates"] = {"version": nameplates.VERSION, "clean": item["clean_sha256"], "rows": rows}
        del item["plates"], item["plate_rows"]


def bgm_gains(items: list[dict]) -> dict[str, dict]:
    """Automatic BGM gain per output; reused from the last run while the clean video and track are unchanged,
    otherwise each track is decoded once and the voices in parallel."""
    result, todo = {}, []
    for item in items:
        known = (item["previous"] or {}).get("bgm_auto") or {}
        if (known.get("version") == VERSION and known.get("clean") == item["clean_sha256"]
                and known.get("bgm") == item["bgm"]["sha256"]):
            result[item["plan_id"]] = known
        else:
            todo.append(item)
    if not todo:
        return result
    music = {}
    for item in todo:
        if item["bgm"]["sha256"] not in music:
            music[item["bgm"]["sha256"]] = AUDIO.pcm(Path(item["bgm"]["path"]))

    def measure(item):
        voice, track = AUDIO.pcm(Path(item["clean"])), music[item["bgm"]["sha256"]]
        gain = AUDIO.auto_bgm_gain_db(voice, track)
        return {"version": VERSION, "clean": item["clean_sha256"], "bgm": item["bgm"]["sha256"], "gain_db": gain,
                "voice_over_music_db": AUDIO.voice_over_music_db(voice, track, gain)}
    with pool(len(todo)) as executor:
        for item, row in zip(todo, executor.map(measure, todo)):
            result[item["plan_id"]] = row
    return result


def package(job: Path, state: dict, config: dict) -> dict:
    items, errors, warnings = prepare_items(job, state, config)
    if errors.get("batch"):
        return {"published": [], "errors": errors, "warnings": warnings, "complete": False}
    # Outputs without problems are packaged now; the others wait for their fix (a re-run redoes only those).
    # A private work folder per run: a leftover process of an interrupted run cannot block this one.
    shutil.rmtree(job / "work", ignore_errors=True)
    work = job / "work" / f"run-{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    fresh = [item for item in items if not item["previous"]]
    runtime.prefetch_animations({cue["entrance"]["effect"] for item in fresh for cue in item["design"]["cues"]} - {"none"})
    log(f"package: encoding {len(fresh)} outputs ({len(items) - len(fresh)} unchanged)")

    def attempt(item):
        try:
            return finish(item, work)
        except (RuntimeError, ValueError, OSError) as error:
            return {"plan_id": item["plan_id"], "errors": [f"encode failed: {str(error)[-600:]}"]}
    with pool(len(items)) as executor:
        results = list(executor.map(attempt, items))
    for row in results:
        if row["errors"]:
            errors[row["plan_id"]] = row["errors"]
    delivery = Path(state["delivery_dir"])
    manifest_path = job / "delivery.json"
    manifest = read(manifest_path) if manifest_path.is_file() else {"outputs": []}
    rows = {row["plan_id"]: row for row in manifest["outputs"]}
    published = []
    for row in results:
        if row["plan_id"] in errors:
            continue
        target = delivery / f"{row['plan_id']}.mp4"
        if not row.get("reused"):
            staged = Path(row.pop("staged"))
            partial = target.with_name(f".{target.name}.partial")
            shutil.copyfile(staged, partial)
            os.replace(partial, target)
            staged.unlink(missing_ok=True)
            final_sheet(row, target, job / "sheets" / f"{row['plan_id']}-final.jpg")
        row.update(path=str(target), sha256=sha(target))
        row.pop("reused", None)
        rows[row["plan_id"]] = row
        published.append(row["plan_id"])
    plan_ids = [row["plan_id"] for row in read(job / "config/plan.json")["outputs"]]
    for pid in list(rows):
        if pid not in plan_ids:
            Path(rows.pop(pid)["path"]).unlink(missing_ok=True)
    write(manifest_path, {"outputs": [rows[pid] for pid in plan_ids if pid in rows], "config": config})
    shutil.rmtree(job / "work", ignore_errors=True)
    return {"published": published, "errors": errors, "warnings": warnings,
            "complete": all(pid in rows for pid in plan_ids) and not errors}


from montage.delivery import deliver_clean
