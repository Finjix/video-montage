from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIRS = ("components", "dependencies", "docs", "skill", "tools")
FILES = ("README.md", "install.cmd", "uninstall.cmd", "package.cmd")
SKIP_DIRS = {".git", ".manifests", ".pytest_cache", "__pycache__", "artifacts", "records", "results", "outputs", "cache"}
SKIP_FILES = {"Thumbs.db", ".DS_Store"}


def release_version(root: Path) -> str:
    skill = root / "skill/video-montage/SKILL.md"
    text = skill.read_text(encoding="utf-8-sig")
    match = re.search(r'^  version: "(v\d{6})"$', text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Cannot read release version from {skill}")
    return match.group(1)


def package_files(root: Path) -> list[Path]:
    files = []
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing or linked release file: {path}")
        files.append(path)
    for name in DIRS:
        directory = root / name
        if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
            raise RuntimeError(f"Missing or linked release directory: {directory}")
        for current, folders, names in os.walk(directory, followlinks=False):
            current_path = Path(current)
            kept = []
            for folder in sorted(folders):
                if folder in SKIP_DIRS or folder.startswith(".cache"):
                    continue
                path = current_path / folder
                if path.is_symlink() or path.is_junction():
                    raise RuntimeError(f"Linked directory in release: {path}")
                kept.append(folder)
            folders[:] = kept
            for name in sorted(names):
                if name in SKIP_FILES or name.endswith((".pyc", ".pyo", ".tmp", ".log")):
                    continue
                path = current_path / name
                if path.is_symlink():
                    raise RuntimeError(f"Linked file in release: {path}")
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_archive(root: Path, archive_version: str | None = None) -> tuple[Path, int]:
    root = root.resolve()
    project_version = release_version(root)
    archive_version = archive_version or project_version
    if not re.fullmatch(r"v\d{6}", archive_version):
        raise ValueError("Archive version must look like v260929")
    entries = package_files(root)
    release_dir = root / "release"
    release_dir.mkdir(exist_ok=True)
    output = release_dir / f"video-montage-{archive_version}.zip"
    temporary = release_dir / f".video-montage-{archive_version}.{uuid.uuid4().hex}.zip"
    prefix = f"video-montage-{project_version}"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for path in entries:
                archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output, len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a clean, portable video-montage ZIP.")
    parser.add_argument("archive_version", nargs="?", help="Optional ZIP filename version, such as v260929")
    args = parser.parse_args()
    from verify_runtime import verify

    report = verify()
    if report["decision"] != "pass":
        raise RuntimeError(f"Runtime check failed: {report['failures']}")
    output, count = build_archive(ROOT, args.archive_version)
    print(f"Created: {output}")
    print(f"Top-level folder: video-montage-{release_version(ROOT)}")
    print(f"Files: {count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
