"""Resolve an editorial A/B/end-card assignment and publish the verified, named MP4s."""
from __future__ import annotations

import math
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from montage import runtime
from montage.common import digest, log, read, sha, write
from montage.delivery_names import delivery_filename


def resolve_path(base: Path, value, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} needs a path")
    return (base / value).resolve()


def assignments(job: Path, config: dict, base: Path) -> tuple[Path, float, list[dict]]:
    if not isinstance(config, dict):
        raise ValueError("delivery config must be an object")
    folder = resolve_path(base, config.get("output_dir"), "output_dir")
    if folder.exists() and not folder.is_dir():
        raise ValueError(f"output_dir must be a directory: {folder}")
    outputs = config.get("outputs")
    count = config.get("expected_outputs")
    if type(count) is not int or count < 1 or not isinstance(outputs, list) or len(outputs) != count:
        raise ValueError("expected_outputs must be positive and match the number of final output rows")
    target = config.get("target_lufs", -12.0)
    if type(target) not in (float, int) or not math.isfinite(target) or not -70 <= target <= -5:
        raise ValueError("target_lufs must be a number between -70 and -5")
    packaged = {}
    if (job / "job.json").is_file():
        report = job / "reports/package.json"
        if not report.is_file() or not read(report).get("complete"):
            raise ValueError("finish package for all A sides before deliver")
        packaged = {r["plan_id"]: r for r in read(job / "delivery.json")["outputs"]}
    rows, names, plans = [], set(), set()
    for index, row in enumerate(outputs, 1):
        if not isinstance(row, dict):
            raise ValueError(f"output {index} needs an assignment")
        name = delivery_filename(row.get("material_id"), row.get("name_fields"), row.get("keyword"), row.get("series"))
        if name.casefold() in names:
            raise ValueError(f"duplicate delivery filename: {name}")
        names.add(name.casefold())
        pid = row.get("plan_id")
        if packaged:
            if not isinstance(pid, str) or pid not in packaged or "a" in row:
                raise ValueError(f"{name}: use a packaged plan_id for A")
            a = Path(packaged[pid]["path"]).resolve()
            if not a.is_file() or sha(a) != packaged[pid]["sha256"]:
                raise ValueError(f"{pid}: packaged A changed or is missing; rerun package")
            plans.add(pid)
        else:
            if pid is not None:
                raise ValueError(f"{name}: plan_id needs a completed packaging job, or supply a ready A file")
            a = resolve_path(base, row.get("a"), "a")
        b = resolve_path(base, row.get("b"), "b")
        card = resolve_path(base, row.get("endcard", config.get("endcard")), "endcard")
        for path in (a, b, card):
            if not path.is_file():
                raise ValueError(f"{name}: missing input {path}")
        selection = row.get("b_range")
        if selection is not None and (not isinstance(selection, list) or len(selection) != 2
                or not all(type(n) is int for n in selection) or not 0 <= selection[0] < selection[1]):
            raise ValueError(f"{name}: b_range needs [start_frame, end_frame_exclusive] in source B frames")
        rows.append({"name": name, "plan_id": pid, "a": str(a), "b": str(b), "endcard": str(card),
                     "b_range": selection, "match": row.get("match"), "series_basis": row.get("series_basis")})
    if plans != set(packaged):
        raise ValueError(f"delivery is missing packaged A sides: {sorted(set(packaged) - plans)}")
    sources = {Path(r[k]) for r in rows for k in ("a", "b", "endcard")}
    if any((folder / r["name"]).resolve() in sources for r in rows):
        raise ValueError("a delivery filename would overwrite a source")
    return folder, float(target), rows


def deliver(job: Path, config: dict, base: Path, *, check_only=False) -> dict:
    folder, target, rows = assignments(job, config, base)
    report = job / "reports/final-delivery.json"
    # The job owns the report and a target folder may be shared by several jobs.
    with runtime.locked(f"deliver-job:{os.path.normcase(job.resolve())}"), runtime.locked(f"deliver-folder:{os.path.normcase(folder)}"):
        last = read(report) if report.is_file() else {}
        owned = last["files"] if last and Path(last["output_dir"]) == folder else {}
        previous_names = {os.path.normcase(name): name for name in owned}
        for row in rows:
            path = folder / row["name"]
            prior = owned.get(previous_names.get(os.path.normcase(path.name)), {})
            if path.exists() and (not path.is_file() or sha(path) not in (prior.get("sha256"), prior.get("previous_sha256"))):
                raise ValueError(f"existing destination belongs to another delivery or was modified: {path}")
        if check_only:
            return {"checked": len(rows), "output_dir": str(folder), "complete": False}

        from montage.delivery import assemble
        by_key = {item["key"]: (folder / name, item) for name, item in owned.items()}
        result = {"output_dir": str(folder), "target_lufs": target, "config_hash": digest(config),
                  "files": owned, "complete": False}
        folder.mkdir(parents=True, exist_ok=True)
        write(report, result)
        for index, row in enumerate(rows, 1):
            path = folder / row["name"]
            old_name = previous_names.get(os.path.normcase(path.name))
            if old_name is not None and old_name != path.name:
                if path.exists():
                    os.replace(folder / old_name, path)
                owned[path.name] = owned.pop(old_name)
            prior = owned.get(row["name"], {})
            inputs = [Path(row[field]) for field in ("a", "b", "endcard")]
            hashes = [sha(p) for p in inputs]
            key = digest([1, hashes, row["b_range"], target])
            if prior.get("key") == key and path.is_file() and sha(path) == prior["sha256"]:
                prior.update(row)
                log(f"deliver {index}/{len(rows)}: reused {path.name}")
                continue
            with TemporaryDirectory(prefix=".deliver-", dir=folder) as staging:
                staged = Path(staging) / "output.mp4"
                previous_file, previous_row = by_key.get(key, (None, {}))
                if previous_file and previous_file.is_file() and sha(previous_file) == previous_row["sha256"]:
                    log(f"deliver {index}/{len(rows)}: copying verified encode as {path.name}")
                    shutil.copyfile(previous_file, staged)
                    qc = previous_row["qa"]
                else:
                    log(f"deliver {index}/{len(rows)}: assembling {path.name}")
                    options = {"b_range": tuple(row["b_range"])} if row["b_range"] is not None else {}
                    qc = assemble(*inputs, staged, target_lufs=target, **options)
                owned[path.name] = {**row, "key": key, "input_sha256": hashes, "sha256": sha(staged),
                                    "previous_sha256": sha(path) if path.is_file() else None, "qa": qc}
                # Record both hashes before replacing the file, so an interrupted publish can be resumed.
                write(report, result)
                os.replace(staged, path)
        current = {row["name"] for row in rows}
        sources = {Path(row[k]) for row in rows for k in ("a", "b", "endcard")}
        for name in owned.keys() - current:
            path = folder / name
            if path.is_file() and path.resolve() not in sources:
                if path.parent != folder or sha(path) != owned[name]["sha256"]:
                    raise ValueError(f"previous delivery changed; inspect before removing {path}")
                path.unlink()
            del owned[name]
        for entry in owned.values():
            entry.pop("previous_sha256", None)
        result["complete"] = True
        write(report, result)
        return result
