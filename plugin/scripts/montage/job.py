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
}


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


def init(order: dict, job_dir: Path | None = None) -> tuple[Path, dict]:
    count = order.get("requested_outputs")
    if type(count) is not int or count < 1:
        raise ValueError("requested_outputs must be a positive integer")
    paths = [Path(row.get("path", "") if isinstance(row, dict) else str(row)).resolve() for row in order.get("sources") or []]
    if not paths:
        raise ValueError("sources must list the original video files")
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            raise ValueError(f"not a source video (.mp4/.mov/.mkv): {path}")
    with pool(len(paths)) as executor:
        hashes = list(executor.map(sha, paths))
    if len(set(hashes)) != len(hashes):
        raise ValueError("two sources have identical bytes")
    asset_root = Path(order.get("asset_root", "")).resolve()
    if not asset_root.is_dir():
        raise ValueError(f"asset_root is not a folder: {asset_root}")
    celebrities = []
    for row in order.get("celebrities", []):
        row = row if isinstance(row, dict) else {"person_id": row}
        nameplate = row.get("nameplate")
        plate = Path(nameplate if isinstance(nameplate, str) else nameplate.get("path", "") if isinstance(nameplate, dict) else "")
        if not rules.person_id(row.get("person_id")) or not str(row.get("name", "")).strip() or not plate.is_file():
            raise ValueError(f"celebrity needs person_id (person:xxx), name and an existing nameplate PNG: {row}")
        if problem := overlay_problem(plate):
            raise ValueError(f"nameplate {plate} must be a transparent 9:16 PNG ({problem})")
        celebrities.append({"person_id": row["person_id"], "name": row["name"],
                            "nameplate": {"path": str(plate.resolve()), "sha256": sha(plate)}})
    if not celebrities or len({c["person_id"] for c in celebrities}) != len(celebrities):
        raise ValueError("celebrities must list each selected celebrity once")
    decisions = {key: check_decision(key, value) for key, value in (order.get("decisions") or {}).items()}
    cast = decisions.get("min_celebrities_per_output", {}).get("value", 1)
    if cast > len(celebrities):
        raise ValueError(f"decisions.min_celebrities_per_output is {cast} but only {len(celebrities)} celebrities are registered")
    minimum = decisions.get("min_sources_per_output", {}).get("value", 3)
    if len(paths) < minimum:
        raise ValueError(f"{len(paths)} source files cannot give each video {minimum} different sources; ask the user now "
                         "for more footage or record decisions.min_sources_per_output")
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
    if delivery.exists() and any(delivery.iterdir()):
        raise FileExistsError(f"refusing a non-empty delivery folder: {delivery}")
    job.mkdir(parents=True)
    write(job / "config/text_library.json", library)
    (job / "subtitles").mkdir()
    order = {**order, "sources": [str(p) for p in paths], "asset_root": str(asset_root), "celebrities": celebrities}
    order.pop("decisions", None)
    state = {"delivery_dir": str(delivery), "job_dir": str(job), "order": order, "celebrities": celebrities,
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
    save(job, state)
    return state["decisions"]
