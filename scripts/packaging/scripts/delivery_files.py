"""Publish a verified batch with rollback on any file replacement failure."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory


def publish_files(copies: list[tuple[Path, Path]], temporary_root: Path) -> None:
    copies = [(source.resolve(), target.resolve()) for source, target in copies]
    if len({target for _, target in copies}) != len(copies):
        raise ValueError("duplicate publication target")
    temporary_root.mkdir(parents=True, exist_ok=True)
    prepared = []
    committed = []
    token = uuid.uuid4().hex
    with TemporaryDirectory(prefix="publish-backup-", dir=temporary_root) as directory:
        try:
            for index, (source, target) in enumerate(copies):
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = Path(directory) / str(index)
                existed = target.exists()
                if existed:
                    shutil.copy2(target, backup)
                staged = target.with_name(f".{target.name}.{token}.publish")
                prepared.append((staged, target, backup, existed))
                shutil.copy2(source, staged)
            for staged, target, backup, existed in prepared:
                os.replace(staged, target)
                committed.append((target, backup, existed))
        except Exception:
            for target, backup, existed in reversed(committed):
                if existed:
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            raise
        finally:
            for staged, _, _, _ in prepared:
                staged.unlink(missing_ok=True)
