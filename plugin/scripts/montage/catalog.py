"""Source catalog: frame clock, camera cuts, speech units with clean cut frames, and one contact sheet per source.

`prepare` analyses every source once. `annotate` applies the agent's people, speaker and transcript
corrections (corrected text is re-timed by the forced aligner) and rewrites the catalog.
"""
from __future__ import annotations

import math
import re
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from montage import asr as ASR
from montage import rules
from montage.common import FFMPEG, FFPROBE, FONT, REJECTED, digest, log, pool, probe, read, run, write

THUMB_W, THUMB_H = 64, 114
CUT_DIFFERENCE = 0.07       # adjacent-frame mean difference that marks a camera cut / unstable edge
SOFT_CUT, ISOLATION = 0.03, 3.0  # a same-background cut (zoom-in, jump cut) stays under CUT_DIFFERENCE but towers over every neighbour
ENDPOINT_DIFFERENCE = 0.12  # 13-frame endpoint distance at a clip edge (dissolves, fast zooms)
EDGE_FRAMES = 30            # frames at each clip edge that must stay free of dissolves, flashes and fast motion
HOP = 0.01                  # energy envelope resolution (seconds)
CLAUSE_PAUSE = 0.35         # a pause this long always ends a unit
SENTENCE_PAUSE = 0.6
LEAD, TAIL = 0.02, 0.03     # kept beyond the energy island around the words
GROW_LEFT, GROW_RIGHT = 0.12, 0.25  # an island grows through audible onset/decay at most this far
SILENT_EDGE = 0.045         # muted audio at every clip edge (the cut check reads 40 ms)
MAX_HEAD, MAX_TAIL = 0.45, 0.55  # longest pause kept before / after the speech of a clip
STRONG = "。！？!?…"
WEAK = "，、；：,;:"
SHEET_TILE = (135, 240)
BEAT_KINDS = ("talk", "scene")                 # talk: a message that stands alone for the viewer; scene: acting that needs the other lines
BEAT_ROLES = ("hook", "body", "payoff", "cta")  # position in a video: opener, middle, result, call to action
UNIT_RANGE = re.compile(r"U\d+(?:\.\d+)?(?:-U\d+(?:\.\d+)?)?")


# ----------------------------------------------------------------------------------- media analysis

def video_clock(path: Path) -> dict:
    """Constant frame rate from timestamp 0, read from packet timestamps (no decode)."""
    info = probe(path)
    stream = next((row for row in info["streams"] if row["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"{path.name}: no video stream")
    fps = Fraction(stream["avg_frame_rate"])
    tick = Fraction(stream["time_base"])
    output = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts",
                  "-of", "csv=p=0", path])
    stamps = sorted(int(value) for value in output.split() if value.strip().lstrip("-").isdigit())
    if fps <= 0 or not stamps:
        raise ValueError(f"{path.name}: unreadable frame timestamps")
    tolerance = max(tick / 2, Fraction(1, 1_000_000))
    if any(abs(stamp * tick - Fraction(index) / fps) > tolerance for index, stamp in enumerate(stamps)):
        raise ValueError(f"{path.name}: variable frame rate or nonzero start; make a constant-frame-rate copy first")
    audio = any(row["codec_type"] == "audio" for row in info["streams"])
    return {"fps_num": fps.numerator, "fps_den": fps.denominator, "frames": len(stamps),
            "duration": round(float(len(stamps) / fps), 6), "width": int(stream["width"]),
            "height": int(stream["height"]), "audio": audio}


def tiny(frame: np.ndarray) -> np.ndarray:
    """16x19 grey fingerprint of a 64x114 RGB thumbnail (render lineage checks)."""
    return (frame.reshape(19, 6, 16, 4, 3).mean(axis=(1, 3, 4)) * 255).astype(np.uint8)


def visual_series(path: Path, frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame adjacent difference, 13-frame endpoint distance and fingerprints on 64x114 thumbnails."""
    size = THUMB_W * THUMB_H * 3
    command = [str(FFMPEG), "-v", "error", "-nostdin", "-threads", "0", "-i", str(path), "-an",
               "-vf", f"scale={THUMB_W}:{THUMB_H}:flags=area,format=rgb24", "-fps_mode", "passthrough",
               "-f", "rawvideo", "-"]
    adjacent = np.zeros(frames, np.float32)
    endpoint = np.full(frames, np.nan, np.float32)
    prints = np.zeros((frames, 19, 16), np.uint8)
    window: deque = deque(maxlen=13)
    count = 0
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        while (block := process.stdout.read(size)) and len(block) == size:
            frame = np.frombuffer(block, np.uint8).astype(np.float32) / 255
            if count >= frames:
                count += 1
                continue
            prints[count] = tiny(frame.reshape(THUMB_H, THUMB_W, 3))
            if window:
                adjacent[count] = float(np.mean(np.abs(frame - window[-1])))
            window.append(frame)
            if len(window) == 13:
                endpoint[count - 12] = float(np.mean(np.abs(window[-1] - window[0])))
            count += 1
    if count != frames:
        raise ValueError(f"{path.name}: decoded {count} frames, timestamps list {frames}")
    return adjacent, endpoint, prints


def camera_cuts(adjacent: np.ndarray) -> list[int]:
    """Frames that start a new shot: a spike well above its neighbourhood, so steady motion and duplicated-frame
    flicker are not cuts. A softer single-frame spike ISOLATION times above every neighbour is a cut too: a zoom-in on
    the same set or a jump cut differs little per pixel, and a few frames of it at a clip edge flash on screen.
    The onset of a motion burst can still read as a cut; annotations can merge it."""
    cuts = []
    for i in range(1, len(adjacent)):
        value = adjacent[i]
        if value <= SOFT_CUT:
            continue
        around = np.concatenate([adjacent[max(1, i - 8):i], adjacent[i + 1:i + 9]])
        hard = value > CUT_DIFFERENCE and not (around.size and value < 3 * float(np.percentile(around, 75)))
        soft = not around.size or value >= ISOLATION * float(np.max(around))
        if not (hard or soft):
            continue
        if cuts and i - cuts[-1] < 6:
            if value > adjacent[cuts[-1]]:
                cuts[-1] = i
            continue
        cuts.append(i)
    return cuts


def mask_cuts(adjacent: np.ndarray, endpoint: np.ndarray, cuts: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """A plain camera cut near a clip edge is not instability: it only makes the edge shot short, and the plan
    solver already keeps every shot of a video at least MIN_SHOT frames long."""
    adjacent, endpoint = adjacent.copy(), endpoint.copy()
    for cut in cuts:
        adjacent[cut] = 0.0
        endpoint[max(0, cut - 12):cut] = 0.0    # endpoint[i] compares frames i and i + 12
    return adjacent, endpoint


def head_stable(adjacent: np.ndarray, endpoint: np.ndarray, frame: int) -> bool:
    """A clip may start at `frame`: no transition inside its first frames."""
    end = min(len(adjacent), frame + EDGE_FRAMES)
    if end - frame < 13:
        return False
    return float(np.max(adjacent[frame + 1:end])) <= CUT_DIFFERENCE and not endpoint[frame] > ENDPOINT_DIFFERENCE


def tail_stable(adjacent: np.ndarray, endpoint: np.ndarray, frame: int) -> bool:
    """A clip may end before `frame` (exclusive): no transition inside its last frames."""
    start = max(0, frame - EDGE_FRAMES)
    if frame - start < 13 or frame > len(adjacent):
        return False
    return float(np.max(adjacent[start + 1:frame])) <= CUT_DIFFERENCE and not endpoint[frame - 13] > ENDPOINT_DIFFERENCE


def audio_samples(path: Path) -> np.ndarray:
    result = subprocess.run([str(FFMPEG), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn",
                             "-ac", "1", "-ar", str(ASR.SAMPLE_RATE), "-f", "f32le", "-"], capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{path.name}: cannot decode audio")
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def energy(samples: np.ndarray) -> np.ndarray:
    """dBFS per 10 ms hop over a 20 ms window."""
    hop = round(ASR.SAMPLE_RATE * HOP)
    count = len(samples) // hop
    if not count:
        return np.zeros(0, np.float32)
    power = (samples[:count * hop].reshape(count, hop) ** 2).mean(axis=1)
    smoothed = np.convolve(power, [0.5, 0.5], mode="same")
    return (10 * np.log10(np.maximum(smoothed, 1e-12))).astype(np.float32)


# ----------------------------------------------------------------------------------- speech units

def split_units(words: list[dict]) -> list[dict]:
    """Clauses: end at punctuation or a pause; `sentence_end` marks complete sentences."""
    units, current = [], []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        tail = word["word"].rstrip()
        gap = following["start"] - word["end"] if following else math.inf
        strong = tail.endswith(tuple(STRONG))
        if following is None or strong or tail.endswith(tuple(WEAK)) or gap >= CLAUSE_PAUSE:
            units.append({"words": current, "sentence_end": strong or gap >= SENTENCE_PAUSE})
            current = []
    return units


def threshold(env: np.ndarray) -> float:
    floor = float(np.percentile(env, 10)) if env.size else -90.0
    return min(-35.0, max(floor + 10.0, -50.0))


def island(env: np.ndarray, limit: float, start: float, end: float, lower: float, upper: float) -> tuple[float, float]:
    """Grow a word span through adjacent audible energy (breath, decay), never into the neighbouring words."""
    left, right = int(round(start / HOP)), int(round(end / HOP))
    floor, ceiling = max(int(math.ceil(lower / HOP)), left - round(GROW_LEFT / HOP)), min(int(upper / HOP), right + round(GROW_RIGHT / HOP))
    while left > floor and left - 1 < len(env) and env[left - 1] > limit:
        left -= 1
    while right < ceiling and right < len(env) and env[right] > limit:
        right += 1
    return min(start, left * HOP), max(end, right * HOP)


def unit_text(words: list[dict]) -> str:
    return "".join(word["word"] for word in words).strip()


def finish_units(units: list[dict], env: np.ndarray, clock: dict, series: tuple[np.ndarray, np.ndarray]) -> list[dict]:
    """Protected speech span and clean in/out frames of every unit (neighbours bound the cut window)."""
    fps = clock["fps_num"] / clock["fps_den"]
    limit = threshold(env)
    for index, unit in enumerate(units):
        words = unit["words"]
        lower = units[index - 1]["words"][-1]["end"] if index else 0.0
        upper = units[index + 1]["words"][0]["start"] if index + 1 < len(units) else clock["duration"]
        lo, hi = island(env, limit, words[0]["start"], words[-1]["end"], lower, upper)
        unit["protect"] = [round(max(lower, lo - LEAD), 3), round(min(upper, hi + TAIL), 3)]
    adjacent, endpoint = mask_cuts(*series, clock["cuts"])
    for index, unit in enumerate(units):
        before = units[index - 1]["protect"][1] if index else 0.0
        after = units[index + 1]["protect"][0] if index + 1 < len(units) else clock["duration"]
        first = math.ceil(max(before, unit["protect"][0] - MAX_HEAD) * fps - 1e-9)
        last = math.floor((unit["protect"][0] - SILENT_EDGE) * fps + 1e-9)
        unit["in_frames"] = ranges(f for f in range(max(0, first), last + 1) if head_stable(adjacent, endpoint, f))
        first = math.ceil((unit["protect"][1] + SILENT_EDGE) * fps - 1e-9)
        last = math.floor(min(after, unit["protect"][1] + MAX_TAIL) * fps + 1e-9)
        unit["out_frames"] = ranges(f for f in range(first, min(clock["frames"], last) + 1)
                                    if tail_stable(adjacent, endpoint, f))
        unit["text"] = unit_text(unit["words"])
        unit["start"], unit["end"] = unit["words"][0]["start"], unit["words"][-1]["end"]
    return units


def ranges(frames) -> list[list[int]]:
    result = []
    for frame in frames:
        if result and frame == result[-1][1] + 1:
            result[-1][1] = frame
        else:
            result.append([frame, frame])
    return result


from montage.timeline import expand, rejected_entries, rejected_hits


# ----------------------------------------------------------------------------------- sheets

def fingerprints(path: Path) -> np.ndarray:
    """Fingerprints of every frame of a rendered video (same scaling path as the source analysis)."""
    data = run([FFMPEG, "-v", "error", "-nostdin", "-threads", "0", "-i", path, "-an", "-vf",
                f"scale={THUMB_W}:{THUMB_H}:flags=area,format=rgb24", "-fps_mode", "passthrough", "-f", "rawvideo", "-"], binary=True)
    frames = np.frombuffer(data, np.uint8).reshape(-1, THUMB_H, THUMB_W, 3).astype(np.float32) / 255
    return np.stack([tiny(frame) for frame in frames]) if len(frames) else np.zeros((0, 19, 16), np.uint8)


def grab(path: Path, numbers: list[int], size=SHEET_TILE) -> dict[int, Image.Image]:
    """Decode selected frames at tile size in one pass."""
    wanted = sorted(set(numbers))
    if not wanted:
        return {}
    expression = "+".join(f"eq(n\\,{n})" for n in wanted)
    width, height = size
    data = run([FFMPEG, "-v", "error", "-nostdin", "-i", path, "-an", "-vf",
                f"select='{expression}',scale={width}:{height}:flags=area,format=rgb24", "-fps_mode", "passthrough",
                "-f", "rawvideo", "-"], binary=True)
    frame = width * height * 3
    if len(data) != frame * len(wanted):
        raise RuntimeError(f"{path.name}: decoded {len(data) // frame} of {len(wanted)} sheet frames")
    return {n: Image.frombytes("RGB", (width, height), data[i * frame:(i + 1) * frame]) for i, n in enumerate(wanted)}


def label_font(size: int = 16):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def tile_sheet(tiles: list[tuple[Image.Image, str]], target: Path, columns: int = 8, title: str = "") -> Path:
    width, height = SHEET_TILE
    rows = max(1, math.ceil(len(tiles) / columns))
    top = 34 if title else 0
    sheet = Image.new("RGB", (columns * width, top + rows * (height + 22)), "#151515")
    draw = ImageDraw.Draw(sheet)
    font = label_font()
    if title:
        draw.text((6, 6), title, fill="white", font=label_font(20))
    for index, (image, label) in enumerate(tiles):
        x, y = (index % columns) * width, top + (index // columns) * (height + 22)
        sheet.paste(image, (x, y + 20))
        draw.text((x + 3, y + 1), label, fill="#ffd400", font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=85)
    return target


def asset_sheet(asset_root: Path, target: Path) -> Path | None:
    """Every 9:16 overlay PNG of the asset pack, cropped to its visible content (text pins, nameplates, disclaimer)."""
    width, gap = 540, 26
    crops = []
    for path in sorted(asset_root.rglob("*.png")):
        with Image.open(path) as image:
            if image.width * 16 != image.height * 9:
                continue
            rgba = image.convert("RGBA")
        box = rgba.getchannel("A").getbbox()
        if not box:
            continue
        crop = rgba.crop(box)
        scale = min(width / crop.width, 220 / crop.height)
        crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))))
        crops.append((crop, path.name))
    if not crops:
        return None
    columns = 2
    rows = [crops[i:i + columns] for i in range(0, len(crops), columns)]
    heights = [max(c.height for c, _ in row) + gap for row in rows]
    sheet = Image.new("RGB", (columns * width, sum(heights) + 34), "#5a5a5a")
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), f"assets {asset_root}", fill="white", font=label_font(20))
    y = 34
    for row, height in zip(rows, heights):
        for column, (crop, name) in enumerate(row):
            draw.text((column * width + 4, y + 2), name, fill="#ffd400", font=label_font())
            sheet.paste(crop, (column * width, y + gap - 4), crop)
        y += height
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=88)
    return target


def source_sheet(path: Path, sid: str, shots: list[list[int]], target: Path) -> Path:
    middles = [(start + end) // 2 for start, end in shots]
    images = grab(path, middles)
    tiles = [(images[m], f"{sid} #{i} f{s}-{e - 1}") for i, ((s, e), m) in enumerate(zip(shots, middles), 1)]
    return tile_sheet(tiles, target, title=f"{sid} {path.name}")


# ----------------------------------------------------------------------------------- prepare

def shots_of(cuts: list[int], frames: int) -> list[list[int]]:
    edges = [0, *cuts, frames]
    return [[a, b] for a, b in zip(edges, edges[1:]) if b > a]


def analyse(source: dict, clock: dict, directory: Path) -> dict:
    """Visual pass of one source (CPU): differences, fingerprints, camera cuts and the contact sheet."""
    started = time.monotonic()
    path = Path(source["path"])
    adjacent, endpoint, prints = visual_series(path, clock["frames"])
    np.savez_compressed(directory / f"{source['id']}.npz", adjacent=adjacent, endpoint=endpoint, prints=prints)
    cuts = camera_cuts(adjacent)
    shots = shots_of(cuts, clock["frames"])
    sheet = source_sheet(path, source["id"], shots, directory / f"{source['id']}.jpg")
    log(f"prepare: {source['id']} pictures analysed ({clock['frames']} frames, {len(shots)} shots, {time.monotonic() - started:.1f} s)")
    return {**source, **clock, "cuts": cuts, "sheet": str(sheet)}


def recognition_context(order: dict, library: dict) -> str:
    names = [entry["text"] for entry in library["entries"]] + [row["name"] for row in order["celebrities"]]
    return " ".join(dict.fromkeys(names))


def prepare(job: Path, state: dict, *, fresh: bool = False) -> dict:
    """Pictures are analysed on the CPU workers while speech is recognized on the GPU.

    Speech results are reused on a re-run (unit IDs, and the annotations keyed by them, stay valid) unless `fresh`.
    """
    directory = job / "catalog"
    directory.mkdir(parents=True, exist_ok=True)
    sources = state["sources"]
    previous = read(directory / "asr.json") if (directory / "asr.json").is_file() and not fresh else {}
    reuse = previous.get("_sources") == {s["id"]: s["sha256"] for s in sources}
    model = None
    with pool(len(sources)) as executor:
        clocks = list(executor.map(lambda source: video_clock(Path(source["path"])), sources))
        futures = [executor.submit(analyse, source, clock, directory) for source, clock in zip(sources, clocks)]
        with ThreadPoolExecutor(4) as decoder:
            samples = list(decoder.map(lambda pair: audio_samples(Path(pair[0]["path"])) if pair[1]["audio"]
                                       else np.zeros(0, np.float32), zip(sources, clocks)))
        for source, audio in zip(sources, samples):
            np.save(directory / f"{source['id']}-energy.npy", energy(audio))
        if reuse:
            log("prepare: reusing the recognized speech of this job")
            words = [previous[source["id"]]["words"] for source in sources]
        else:
            log("prepare: loading speech recognizer")
            model = ASR.build()
            library = read(job / "config/text_library.json")
            started = time.monotonic()
            words = ASR.recognize(model, samples, recognition_context(state["order"], library))
            log(f"prepare: speech recognized for {len(sources)} sources ({time.monotonic() - started:.1f} s)")
        analysed = [future.result() for future in futures]
    raw = {row["id"]: {"words": w} for row, w in zip(analysed, words)}
    write(directory / "asr.json", {**raw, "_sources": {s["id"]: s["sha256"] for s in sources}})
    write(directory / "sources.json", analysed)
    asset_sheet(Path(state["order"]["asset_root"]), directory / "assets.jpg")
    return build(job, state, model=model)


# ----------------------------------------------------------------------------------- annotations

def load_annotations(job: Path) -> dict:
    path = job / "config/annotations.json"
    return read(path) if path.is_file() else {"sources": {}}


def check_annotations(value: dict, state: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("sources", {}), dict):
        raise ValueError("annotations need {\"sources\": {\"S01\": {...}}}")
    known = {row["id"] for row in state["sources"]}
    allowed = {"people", "speaker", "shots", "cameras", "text", "speakers", "exclude", "notes", "beats"}
    for sid, row in value["sources"].items():
        if sid not in known:
            raise ValueError(f"unknown source {sid}")
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError(f"{sid}: allowed fields are {sorted(allowed)}")
        beats = row.get("beats", [])
        if not isinstance(beats, list):
            raise ValueError(f"{sid}: beats is a list of {{units, kind, role, gist}}")
        for number, beat in enumerate(beats, 1):
            where = f"{sid} beat {number}"
            if not isinstance(beat, dict) or set(beat) != {"units", "kind", "role", "gist"}:
                raise ValueError(f"{where}: a beat has exactly units, kind, role and gist")
            if not isinstance(beat["units"], str) or not UNIT_RANGE.fullmatch(beat["units"]):
                raise ValueError(f"{where}: units look like \"U06-U10\" or \"U06\", got {beat['units']!r}")
            if beat["kind"] not in BEAT_KINDS or beat["role"] not in BEAT_ROLES:
                raise ValueError(f"{where}: kind is one of {list(BEAT_KINDS)}, role one of {list(BEAT_ROLES)}")
            if not isinstance(beat["gist"], str) or len(rules.normalize(beat["gist"])) < 12:
                raise ValueError(f"{where}: gist must say in a sentence what the beat says and does for the video")
        for person in [*row.get("people", []), *[p for shot in row.get("shots", {}).values() for p in shot.get("people", [])],
                       *([row["speaker"]] if row.get("speaker") else []), *row.get("speakers", {}).values()]:
            if not rules.person_id(person):
                raise ValueError(f"{sid}: person IDs look like person:wu-yue, got {person!r}")
        for key, text in row.get("text", {}).items():
            if not (isinstance(text, str) or (isinstance(text, list) and text and all(isinstance(t, str) and t.strip() for t in text))):
                raise ValueError(f"{sid} {key}: corrected text is a string ('' deletes the unit) or a list of strings (splits it)")
    return value


def raw_units(words: list[dict]) -> list[dict]:
    units = split_units(words)
    for number, unit in enumerate(units, 1):
        unit["id"] = f"U{number:02d}"
    return units


def text_window(units: list[dict], index: int) -> tuple[float, float]:
    """Audio slice the aligner re-times a corrected unit in (bounded by the neighbouring words)."""
    unit = units[index]
    before = units[index - 1]["words"][-1]["end"] if index else 0.0
    after = units[index + 1]["words"][0]["start"] if index + 1 < len(units) else unit["words"][-1]["end"] + 0.5
    return max(before, unit["words"][0]["start"] - 0.25), min(after, unit["words"][-1]["end"] + 0.25)


def text_requests(sid: str, units: list[dict], edits: dict) -> list[tuple[str, float, float, str]]:
    """(cache key, origin, finish, text) of every corrected unit."""
    unknown = set(edits) - {unit["id"] for unit in units}
    if unknown:
        raise ValueError(f"{sid}: unknown units {sorted(unknown)}")
    requests = []
    for index, unit in enumerate(units):
        edit = edits.get(unit["id"])
        pieces = edit if isinstance(edit, list) else [edit]
        if edit is None or not "".join(pieces).strip():
            continue
        origin, finish = text_window(units, index)
        requests.append((digest([sid, unit["id"], pieces, origin, finish]), origin, finish, "".join(pieces)))
    return requests


def apply_text(sid: str, units: list[dict], edits: dict, cache: dict) -> list[dict]:
    """Corrected text replaces a unit's words with the aligner's timing (already in `cache`)."""
    result = []
    for index, unit in enumerate(units):
        edit = edits.get(unit["id"])
        if edit is None:
            result.append(unit)
            continue
        pieces = edit if isinstance(edit, list) else [edit]
        if not "".join(pieces).strip():
            continue
        origin, finish = text_window(units, index)
        words = [dict(word) for word in cache.get(digest([sid, unit["id"], pieces, origin, finish]), [])]
        if not words:
            raise ValueError(f"{sid} {unit['id']}: aligner returned no words")
        if len(pieces) == 1:
            result.append({"id": unit["id"], "words": words, "sentence_end": unit["sentence_end"], "edited": True})
            continue
        cursor = 0
        for number, piece in enumerate(pieces, 1):
            length = len(rules.normalize(piece))
            taken, count = [], 0
            while cursor < len(words) and count < length:
                taken.append(words[cursor])
                count += len(rules.normalize(words[cursor]["word"]))
                cursor += 1
            if not taken:
                raise ValueError(f"{sid} {unit['id']}: split piece {number} has no aligned words")
            result.append({"id": f"{unit['id']}.{number}", "words": taken, "edited": True,
                           "sentence_end": unit["sentence_end"] if number == len(pieces) else piece.rstrip()[-1:] in STRONG})
    return result


def align_corrections(job_sources: list[dict], raw: dict, annotations: dict, cache: dict, model=None) -> None:
    """Re-time every new correction in one batched aligner pass (each source's audio decoded once)."""
    pending = []
    for source in job_sources:
        edits = annotations["sources"].get(source["id"], {}).get("text", {})
        if edits:
            units = raw_units(raw[source["id"]]["words"])
            pending += [(source, *request) for request in text_requests(source["id"], units, edits) if request[0] not in cache]
    if not pending:
        return
    started = time.monotonic()
    paths = {source["id"]: Path(source["path"]) for source, *_ in pending}
    with ThreadPoolExecutor(4) as decoder:
        audio = dict(zip(paths, decoder.map(audio_samples, paths.values())))
    if model is None:
        log("annotate: loading aligner")
        model = ASR.build_aligner()
    requests = [(audio[source["id"]][round(origin * ASR.SAMPLE_RATE):round(finish * ASR.SAMPLE_RATE)], text, origin)
                for source, _, origin, finish, text in pending]
    for (_, key, *_), words in zip(pending, ASR.align_many(model, requests)):
        if words:
            cache[key] = words
    log(f"annotate: {len(pending)} corrected units re-timed ({time.monotonic() - started:.1f} s)")


def resolve_beats(sid: str, units: list[dict], specs: list[dict]) -> list[dict]:
    """The agent's beats as ordered, non-overlapping runs of unit IDs (IDs B01... follow the order in the source)."""
    ids = [unit["id"] for unit in units]
    found = []
    for number, spec in enumerate(specs, 1):
        first, _, last = spec["units"].partition("-")
        last = last or first
        missing = [value for value in (first, last) if value not in ids]
        if missing:
            raise ValueError(f"{sid} beat {number} ({spec['units']}): no unit {', '.join(missing)} (a corrected or split unit "
                             "has its own ID; check the catalog)")
        a, b = ids.index(first), ids.index(last)
        if b < a:
            raise ValueError(f"{sid} beat {number} ({spec['units']}): units out of order")
        found.append((a, b, spec))
    found.sort(key=lambda row: row[0])
    for (a, b, spec), (c, _, other) in zip(found, found[1:]):
        if c <= b:
            raise ValueError(f"{sid}: beats {spec['units']} and {other['units']} overlap")
    return [{"id": f"B{number:02d}", "units": ids[a:b + 1], "kind": spec["kind"], "role": spec["role"], "gist": spec["gist"].strip()}
            for number, (a, b, spec) in enumerate(found, 1)]


def build(job: Path, state: dict, *, model=None) -> dict:
    """Recompute units from raw ASR plus annotations and write catalog.json / catalog.md."""
    directory = job / "catalog"
    sources = read(directory / "sources.json")
    raw = read(directory / "asr.json")
    annotations = check_annotations(load_annotations(job), state)
    cache_path = directory / "aligned.json"
    cache = read(cache_path) if cache_path.is_file() else {}
    align_corrections(sources, raw, annotations, cache, model)
    write(cache_path, cache)
    registered = {row["person_id"] for row in state["celebrities"]}
    catalog = []
    for source in sources:
        sid = source["id"]
        note = annotations["sources"].get(sid, {})
        env = np.load(directory / f"{sid}-energy.npy")
        series = np.load(directory / f"{sid}.npz")
        units = apply_text(sid, raw_units(raw[sid]["words"]), note.get("text", {}), cache)
        units = finish_units(units, env, source, (series["adjacent"], series["endpoint"]))
        cameras = {shot: f"{sid}:cam{index}" for index, group in enumerate(note.get("cameras", []), 1) for shot in group}
        merged = {int(number) for number, row in note.get("shots", {}).items() if row.get("merge")}
        cuts = [cut for number, cut in enumerate(source["cuts"], 2) if number not in merged]
        people_default = note.get("people", [])
        shots = []
        for number, (start, end) in enumerate(shots_of(source["cuts"], source["frames"]), 1):
            override = note.get("shots", {}).get(str(number), {})
            if number in merged and shots:
                shots[-1]["end"] = end
                shots[-1]["numbers"].append(number)
                continue
            shots.append({"number": number, "numbers": [number], "start": start, "end": end,
                          "people": override.get("people", people_default),
                          "family": cameras.get(number, f"{sid}:shot{number}")})
        speaker_default = note.get("speaker") or (people_default[0] if len([p for p in people_default if p in registered]) == 1 else None)
        excluded = set(note.get("exclude", []))
        beats = resolve_beats(sid, units, note.get("beats", []))
        beat_of = {unit_id: beat["id"] for beat in beats for unit_id in beat["units"]}
        for unit in units:
            unit["speaker"] = note.get("speakers", {}).get(unit["id"], speaker_default)
            unit["excluded"] = unit["id"] in excluded
            unit["beat"] = beat_of.get(unit["id"])
            fps = source["fps_num"] / source["fps_den"]
            unit["rejected"] = rejected_hits(source, math.floor(unit["protect"][0] * fps), math.ceil(unit["protect"][1] * fps))
        catalog.append({key: source[key] for key in ("id", "path", "name", "sha256", "fps_num", "fps_den", "frames",
                                                      "duration", "width", "height", "sheet")}
                       | {"cuts": cuts, "shots": shots, "units": units, "beats": beats,
                          "annotated": sid in annotations["sources"]})
    write(cache_path, cache)
    value = {"sources": catalog, "annotations_digest": digest(annotations)}
    write(directory / "catalog.json", value)
    (directory / "catalog.md").write_text(markdown(value, registered), encoding="utf-8")
    return value


def markdown(catalog: dict, registered: set[str]) -> str:
    lines = ["# Source catalog", "",
             "Units are clauses; `|` marks a sentence end. `in`/`out`: the clip may start before / end after this unit",
             "(clean silence and a stable picture). `x` excluded, `R` user-rejected interval, `*` corrected text.",
             "`>> Bnn` opens a beat the agent defined (kind/role: gist); units outside every beat cannot be planned.", ""]
    for source in catalog["sources"]:
        fps = source["fps_num"] / source["fps_den"]
        lines.append(f"## {source['id']} {source['name']}")
        lines.append(f"{source['duration']:.1f} s, {source['frames']} frames @ {fps:.3f} fps, "
                     f"{source['width']}x{source['height']}. Sheet: {source['sheet']}")
        shots = " · ".join(f"{shot['number']} f{shot['start']}-{shot['end'] - 1} ({(shot['end'] - shot['start']) / fps:.1f}s"
                           + (f" {','.join(p.split(':', 1)[1] for p in shot['people'])}" if shot["people"] else "") + ")"
                           for shot in source["shots"])
        lines.append(f"Shots: {shots}")
        if not source["annotated"]:
            lines.append("People: not annotated yet")
        if not source.get("beats"):
            lines.append("Beats: none yet (the source cannot be planned until its beats are annotated)")
        lines.append("")
        beats = {beat["units"][0]: beat for beat in source.get("beats", [])}
        by_id = {unit["id"]: unit for unit in source["units"]}
        for unit in source["units"]:
            if unit["id"] in beats:
                beat = beats[unit["id"]]
                limits = [text for text, ok in (("cannot start a clip", by_id[beat["units"][0]]["in_frames"]),
                                                ("cannot end a clip", by_id[beat["units"][-1]]["out_frames"])) if not ok]
                lines.append(f">> {beat['id']} {beat['units'][0]}-{beat['units'][-1]} {beat['kind']}/{beat['role']}"
                             + (f" ({', '.join(limits)}; join a neighbour)" if limits else "") + f": {beat['gist']}")
            first = math.floor(unit["start"] * fps)
            last = math.ceil(unit["end"] * fps)
            shot_numbers = [shot["number"] for shot in source["shots"] if shot["start"] < last and shot["end"] > first]
            flags = ("in " if unit["in_frames"] else "-- ") + ("out" if unit["out_frames"] else "---")
            marks = ("x" if unit["excluded"] else "") + ("R" if unit["rejected"] else "") + ("*" if unit.get("edited") else "")
            speaker = f" [{unit['speaker'].split(':', 1)[1]}]" if unit.get("speaker") and unit["speaker"] not in registered else ""
            lines.append(f"{unit['id']:<6} {unit['start']:6.2f}-{unit['end']:6.2f}  {flags}  sh{','.join(map(str, shot_numbers)):<6}"
                         f"{marks:<3} {unit['text']}{' |' if unit['sentence_end'] else ''}{speaker}")
        lines.append("")
    return "\n".join(lines)
