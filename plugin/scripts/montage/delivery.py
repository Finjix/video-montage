"""Publish checked clean renders without loading media dependencies."""
from pathlib import Path
import os
import shutil
from montage.common import read, write, sha


def deliver_clean(job: Path, state: dict) -> list[str]:
    """Unpackaged delivery (only on the user's explicit request): the checked clean videos as they are."""
    plan_ids = [row["plan_id"] for row in read(job / "config/plan.json")["outputs"]]
    records = {pid: read(job / "clean" / f"{pid}.json") for pid in plan_ids}
    problems = {pid: record["errors"] for pid, record in records.items() if record["errors"]}
    if problems:
        raise ValueError(f"clean renders have problems: {problems}")
    delivery = Path(state["delivery_dir"])
    rows = []
    for pid, record in records.items():
        target = delivery / f"{pid}.mp4"
        partial = target.with_name(f".{target.name}.partial")
        shutil.copyfile(record["path"], partial)
        os.replace(partial, target)
        rows.append({"plan_id": pid, "path": str(target), "sha256": sha(target), "frames": record["frames"], "packaged": False})
    write(job / "delivery.json", {"outputs": rows, "config": None})
    return plan_ids
