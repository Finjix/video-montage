"""Job creation, state and the user's recorded decisions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from montage import rules, text_library
from montage.common import LIBRARY, OUTPUT_ROOT, pool, read, sha, write

STATE = "job.json"
SOURCE_SUFFIXES = {".mp4", ".mov", ".mkv"}
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
DELIVERY_NAME = re.compile(r"自动化混剪_\d{8}_\d{6}")
DECISIONS = {
    "min_sources_per_output": lambda v: type(v) is int and v in (1, 2),
    "bgm_repetition": lambda v: v is True,
    "min_celebrities_per_output": lambda v: type(v) is int and 2 <= v <= 8,
    "max_celebrities_per_output": lambda v: type(v) is int and 1 <= v <= 8,
}


def cast_range(decisions: dict) -> tuple[int, int | None]:
    """Fewest and most registered celebrities a video may feature (the user's decisions; no maximum by default)."""
    return (decisions.get("min_celebrities_per_output", {}).get("value", 1),
            decisions.get("max_celebrities_per_output", {}).get("value"))


def check_decision(key: str, row) -> dict:
    if key not in DECISIONS:
        raise ValueError(f"unknown decision {key}; known: {sorted(DECISIONS)}")
    if (not isinstance(row, dict) or not DECISIONS[key](row.get("value"))
            or not isinstance(row.get("instruction"), str) or not row["instruction"].strip()):
        raise ValueError(f"decision {key} needs a valid value and an instruction (the user's own words, or 'fallback: <why>')")
    return {"value": row["value"], "instruction": row["instruction"].strip()}


def png_size(path: Path) -> tuple[int, int] | None:
    """Width and height from a PNG header (no image library needed)."""
    with Path(path).open("rb") as stream:
        head = stream.read(24)
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def overlay_problem(path: Path) -> str | None:
    try:
        size = png_size(path)
    except OSError as error:
        return f"unreadable ({error})"
    if size is None:
        return "not a PNG"
    if size[0] * 16 != size[1] * 9:
        return f"{size[0]}x{size[1]} is not 9:16"
    return None


def asset_inventory(asset_root: Path) -> dict:
    """What `package` will find in the asset pack by default (text pins, disclaimer, audio)."""
    pngs = [p for p in asset_root.rglob("*.png") if not overlay_problem(p)]

    def under(*words):
        return sorted(str(p) for p in pngs if any(w in str(p.relative_to(asset_root)).lower() for w in words))
    audio = sorted(str(p) for p in asset_root.rglob("*") if p.is_file()
                   and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"})
    return {"text_pins": under("文字钉", "pin"), "disclaimers": under("免责", "disclaimer"), "audio": audio}


INIT_FILES = {"temp/" + STATE, "temp/config/text_library.json"}


def only_init(delivery: Path) -> bool:
    """True when the delivery folder holds nothing but what init wrote, so a changed order may replace it."""
    files = {p.relative_to(delivery).as_posix() for p in delivery.rglob("*") if p.is_file()}
    return files <= INIT_FILES


def init(order: dict, job_dir: Path | None = None) -> tuple[Path, dict]:
    """Validate the order and create the job; every gap the user must fill is reported in one error."""
    problems = []
    count = order.get("requested_outputs")
    if type(count) is not int or count < 1:
        problems.append("requested_outputs must be a positive integer")
    paths = [Path(row.get("path", "") if isinstance(row, dict) else str(row)).resolve() for row in order.get("sources") or []]
    if not paths:
        problems.append("sources must list the original video files")
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            problems.append(f"not a source video (.mp4/.mov/.mkv): {path}")
    paths = [path for path in paths if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES]
    with pool(max(1, len(paths))) as executor:
        hashes = list(executor.map(sha, paths))
    if len(set(hashes)) != len(hashes):
        problems.append("two sources have identical bytes")
    asset_root = Path(order.get("asset_root", "")).resolve()
    if not asset_root.is_dir():
        problems.append(f"asset_root is not a folder: {asset_root}")
    else:
        assets = asset_inventory(asset_root)
        found = (f"it holds {len(assets['text_pins'])} text pins, {len(assets['disclaimers'])} disclaimer PNGs and "
                 f"{len(assets['audio'])} audio files")
        if not assets["text_pins"]:
            problems.append(f"asset pack has no text pin PNGs (folder 文字钉/pin; {found})")
        if len(assets["disclaimers"]) != 1:
            problems.append(f"asset pack needs exactly one disclaimer PNG (folder 免责/disclaimer; {found})")
    celebrities = []
    for row in order.get("celebrities", []):
        row = row if isinstance(row, dict) else {"person_id": row}
        nameplate = row.get("nameplate")
        plate = Path(nameplate if isinstance(nameplate, str) else nameplate.get("path", "") if isinstance(nameplate, dict) else "")
        if not rules.person_id(row.get("person_id")) or not str(row.get("name", "")).strip() or not plate.is_file():
            problems.append(f"celebrity needs person_id (person:xxx), name and an existing nameplate PNG: {row}")
        elif problem := overlay_problem(plate):
            problems.append(f"nameplate {plate} must be a transparent 9:16 PNG ({problem})")
        else:
            celebrities.append({"person_id": row["person_id"], "name": row["name"],
                                "nameplate": {"path": str(plate.resolve()), "sha256": sha(plate)}})
    people = [row.get("person_id") if isinstance(row, dict) else row for row in order.get("celebrities", [])]
    if not people or len(set(map(str, people))) != len(people):
        problems.append("celebrities must list each selected celebrity once")
    decisions = {}
    for key, value in (order.get("decisions") or {}).items():
        try:
            decisions[key] = check_decision(key, value)
        except ValueError as error:
            problems.append(str(error))
    cast, most = cast_range(decisions)
    if cast > len(people):
        problems.append(f"decisions.min_celebrities_per_output is {cast} but only {len(people)} celebrities are registered")
    if most is not None and most < cast:
        problems.append(f"decisions.max_celebrities_per_output {most} is below min_celebrities_per_output {cast}")
    minimum = decisions.get("min_sources_per_output", {}).get("value", 3)
    if len(order.get("sources") or []) < minimum:
        problems.append(f"{len(order.get('sources') or [])} source files cannot give each video {minimum} different "
                        "sources: more footage, or record decisions.min_sources_per_output")
    if problems:
        raise ValueError("the work order cannot start; ask the user in one message to fill every gap, then run init "
                         "again:\n- " + "\n- ".join(problems))
    library = text_library.checked(read(LIBRARY))
    parent = Path(order.get("output_root") or OUTPUT_ROOT).resolve()
    if job_dir is not None:
        job = job_dir.resolve()
        delivery = job.parent
        if job.name != "temp" or not DELIVERY_NAME.fullmatch(delivery.name):
            raise ValueError("job-dir must be <output_root>/自动化混剪_YYYYMMDD_HHMMSS/temp")
    else:
        delivery = parent / ("自动化混剪_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S"))
        job = delivery / "temp"
    if delivery.exists() and any(delivery.iterdir()) and not (job_dir is not None and only_init(delivery)):
        raise FileExistsError(f"refusing a non-empty delivery folder: {delivery} (init --job-dir replaces only a job "
                              "that has not been prepared yet)")
    job.mkdir(parents=True, exist_ok=True)
    write(job / "config/text_library.json", library)
    (job / "subtitles").mkdir(exist_ok=True)
    order = {**order, "sources": [str(p) for p in paths], "asset_root": str(asset_root), "celebrities": celebrities}
    order.pop("decisions", None)
    state = {"delivery_dir": str(delivery), "packaged_dir": str(job / "packaged"), "job_dir": str(job),
             "order": order, "celebrities": celebrities,
             "decisions": decisions, "created": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
             "sources": [{"id": f"S{i:02d}", "path": str(p), "name": p.name, "sha256": h, "size": p.stat().st_size,
                          "mtime_ns": p.stat().st_mtime_ns} for i, (p, h) in enumerate(zip(paths, hashes), 1)]}
    write(job / STATE, state)
    return job, state


def load(job: Path) -> dict:
    path = job / STATE
    if not path.is_file():
        raise ValueError(f"no job at {job} (run init first)")
    state = read(path)
    for source in state["sources"]:
        file = Path(source["path"])
        if not file.is_file() or file.stat().st_size != source["size"] or (
                file.stat().st_mtime_ns != source["mtime_ns"] and sha(file) != source["sha256"]):
            raise ValueError(f"source changed or missing: {file}")
    return state


def save(job: Path, state: dict) -> None:
    write(job / STATE, state)


def decide(job: Path, key: str, value, instruction: str) -> dict:
    state = load(job)
    state.setdefault("decisions", {})[key] = check_decision(key, {"value": value, "instruction": instruction})
    least, most = cast_range(state["decisions"])
    if most is not None and most < least:
        raise ValueError(f"max_celebrities_per_output {most} is below min_celebrities_per_output {least}")
    save(job, state)
    return state["decisions"]
