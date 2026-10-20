"""Job creation, state and the user's recorded decisions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from montage import rules, text_library
from montage.common import LIBRARY, OUTPUT_ROOT, pool, read, sha, write

STATE = "job.json"
SOURCE_SUFFIXES = {".mp4", ".mov", ".mkv"}
DELIVERY_NAME = re.compile(r"自动化混剪_\d{8}_\d{6}")
REBURN_NOTE = ("修改本目录 subtitle-*.txt 的文字或时间码后，把整个输出文件夹交给 AI，要求重新烧录。\n"
               "保留 temp 文件夹，AI 会用它重新打包并覆盖成片。\n")
DECISIONS = {
    "min_sources_per_output": lambda v: type(v) is int and v in (1, 2),
    "reuse": lambda v: v in ("reasonable", "extended"),
    "bgm_downloads": lambda v: v is True,
    "bgm_repetition": lambda v: v is True,
}


def check_decision(key: str, row) -> dict:
    if key not in DECISIONS:
        raise ValueError(f"unknown decision {key}; known: {sorted(DECISIONS)}")
    if (not isinstance(row, dict) or not DECISIONS[key](row.get("value"))
            or not isinstance(row.get("instruction"), str) or not row["instruction"].strip()):
        raise ValueError(f"decision {key} needs a valid value and the user's own words as instruction")
    return {"value": row["value"], "instruction": row["instruction"].strip()}


def init(order: dict, job_dir: Path | None = None) -> tuple[Path, dict]:
    count = order.get("requested_outputs")
    if type(count) is not int or count < 1:
        raise ValueError("requested_outputs must be a positive integer")
    paths = [Path(row["path"] if isinstance(row, dict) else row).resolve() for row in order.get("sources", [])]
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
        plate = Path(row.get("nameplate", "") if isinstance(row.get("nameplate"), str) else row.get("nameplate", {}).get("path", ""))
        if not rules.person_id(row.get("person_id")) or not str(row.get("name", "")).strip() or not plate.is_file():
            raise ValueError(f"celebrity needs person_id (person:xxx), name and an existing nameplate PNG: {row}")
        celebrities.append({"person_id": row["person_id"], "name": row["name"],
                            "nameplate": {"path": str(plate.resolve()), "sha256": sha(plate)}})
    if not celebrities or len({c["person_id"] for c in celebrities}) != len(celebrities):
        raise ValueError("celebrities must list each selected celebrity once")
    decisions = {key: check_decision(key, value) for key, value in (order.get("decisions") or {}).items()}
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
    (job / "subtitles" / "修改字幕后让AI重新烧录").write_text(REBURN_NOTE, encoding="utf-8")
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
