from __future__ import annotations

import argparse
import os
import re
import runpy
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable

import yaml


ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "video-montage"
DIRS = ("agents", "scripts", "references", "assets")
FILES = ("SKILL.md",)
SKIP_DIRS = {".git", ".manifests", ".runtime", ".pytest_cache", "__pycache__", "artifacts", "records", "results", "output", "outputs", "work", "cache", "test"}
SKIP_FILES = {"Thumbs.db", ".DS_Store"}


def release_version(root: Path) -> str:
    skill = root / "SKILL.md"
    text = skill.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Missing YAML frontmatter: {skill}")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML frontmatter: {skill}") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != SKILL_NAME:
        raise RuntimeError(f"Skill name must be {SKILL_NAME}: {skill}")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise RuntimeError(f"Invalid skill description: {skill}")
    details = metadata.get("metadata")
    version = details.get("version") if isinstance(details, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"v\d{6}", version):
        raise RuntimeError(f"Cannot read release version from {skill}")
    return version


def validate_archive_version(version: str) -> None:
    if not re.fullmatch(r"v\d{6}", version):
        raise ValueError("Archive version must look like v260929")


def package_files(root: Path) -> list[Path]:
    files = []
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing or linked release file: {path}")
        files.append(path)
    for directory_name in DIRS:
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
            raise RuntimeError(f"Missing or linked release directory: {directory}")
        for current, folders, names in os.walk(directory, followlinks=False):
            current_path = Path(current)
            kept = []
            for folder in sorted(folders):
                if (folder in SKIP_DIRS or folder.startswith(".cache")
                        or (directory_name in {"scripts", "references"} and folder == "tests")):
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
    manifests = [path for path in files if path.name.casefold() == "skill.md"]
    if manifests != [root / "SKILL.md"]:
        raise RuntimeError("A skill ZIP must contain exactly one SKILL.md at the skill root")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_archive(root: Path, archive_version: str | None = None,
                  progress: Callable[[str], None] | None = None) -> tuple[Path, int]:
    root = root.resolve()
    project_version = release_version(root)
    archive_version = archive_version or project_version
    validate_archive_version(archive_version)
    if progress:
        progress("正在扫描文件...")
    entries = package_files(root)
    release_dir = root / "release"
    release_dir.mkdir(exist_ok=True)
    output = release_dir / f"video-montage-{archive_version}.zip"
    temporary = release_dir / f".video-montage-{archive_version}.{uuid.uuid4().hex}.zip"
    prefix = SKILL_NAME
    state = {"completed": 0, "current": ""}
    stopped = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stopped.wait(5):
            if progress:
                progress(f"已处理 {state['completed']}/{len(entries)} 个文件，"
                         f"正在压缩 {state['current']}，已用 {int(time.monotonic() - started)} 秒")

    if progress:
        progress(f"共 {len(entries)} 个文件，开始生成 ZIP...")
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for index, path in enumerate(entries, 1):
                state["current"] = path.relative_to(root).as_posix()
                archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
                state["completed"] = index
        os.replace(temporary, output)
    finally:
        stopped.set()
        if progress:
            worker.join()
        temporary.unlink(missing_ok=True)
    return output, len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a video-montage skill ZIP with the OpenAI folder layout.")
    parser.add_argument("archive_version", nargs="?", help="Optional ZIP filename version, such as v260929")
    parser.add_argument("-NoPause", "--NoPause", action="store_true")
    args = parser.parse_args()
    if args.archive_version:
        validate_archive_version(args.archive_version)
    def progress(message: str) -> None:
        print(f"[打包中] {message}", flush=True)

    progress("正在检查随包运行环境...")
    runpy.run_path(str(ROOT / "scripts/validate_skill.py"))["validate"](ROOT)
    verify = runpy.run_path(str(ROOT / "scripts/verify_runtime.py"))["verify"]
    report = verify()
    if report["decision"] != "pass":
        raise RuntimeError(f"Runtime check failed: {report['failures']}")
    output, count = build_archive(ROOT, args.archive_version, progress=progress)
    print(f"Created: {output}")
    print(f"Top-level folder: {SKILL_NAME}")
    print(f"Files: {count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
