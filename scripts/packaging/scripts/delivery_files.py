"""Publish a verified batch with rollback on any file replacement failure."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from tempfile import mkdtemp


def publish_files(copies: list[tuple[Path, Path]], temporary_root: Path) -> None:
    copies = [(source.resolve(), target.resolve()) for source, target in copies]
    if len({target for _, target in copies}) != len(copies):
        raise ValueError("duplicate publication target")
    temporary_root.mkdir(parents=True, exist_ok=True)
    prepared = []
    committed = []
    token = uuid.uuid4().hex
    directory = Path(mkdtemp(prefix="publish-backup-", dir=temporary_root))
    rollback_errors = []
    try:
        for index, (source, target) in enumerate(copies):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = directory / str(index)
            existed = target.exists()
            if existed:
                shutil.copy2(target, backup)
            staged = target.with_name(f".{target.name}.{token}.publish")
            prepared.append((staged, target, backup, existed))
            shutil.copy2(source, staged)
        for staged, target, backup, existed in prepared:
            os.replace(staged, target)
            committed.append((target, backup, existed))
    except Exception as publication_error:
        for target, backup, existed in reversed(committed):
            try:
                if existed:
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except Exception as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(f"publication failed; rollback incomplete; backups retained at {directory}; "
                               + "; ".join(rollback_errors)) from publication_error
        raise
    finally:
        for staged, _, _, _ in prepared:
            staged.unlink(missing_ok=True)
        if not rollback_errors:
            shutil.rmtree(directory)
