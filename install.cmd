@echo off
setlocal
set "SUITE_DIR=%~dp0"
if not exist "%SUITE_DIR%dependencies\python\python.exe" (
  echo Bundled Python is missing: "%SUITE_DIR%dependencies\python\python.exe"
  pause
  exit /b 2
)
"%SUITE_DIR%dependencies\python\python.exe" -X utf8 -c "import pathlib,sys; body=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig').split(chr(10)+'# BEGIN PYTHON DEPLOY'+chr(10),1)[1]; exec(compile(body,sys.argv[1],'exec'))" "%~f0" %*
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Installation failed. Keep this window open and send the error text.
  pause
)
exit /b %EXIT_CODE%
# BEGIN PYTHON DEPLOY
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
import uuid
from pathlib import Path


VERSION = "v260928"
SKILL = "video-montage"
OLD_SKILLS = (
    "ffmpeg-montage-controller",
    "semantic-analysis-training-backup-v20",
    "montage-three-part-orchestrator-ff",
)


def run_checked(label: str, command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"{label} failed ({result.returncode})\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    if result.stdout.strip():
        print(result.stdout.strip())


def create_junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise RuntimeError(f"Cannot register skill {link}: {result.stdout} {result.stderr}")


def remove_tree(path: Path) -> None:
    if path.is_junction():
        path.rmdir()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def owned_link(path: Path, target: Path, old_target: Path) -> bool:
    if not path.is_junction():
        return False
    destination = path.resolve()
    return destination.is_relative_to(target) or destination.is_relative_to(old_target)


def copy_filter(directory: str, names: list[str], source: Path) -> set[str]:
    ignored = {name for name in names if name in {"__pycache__", ".pytest_cache"} or name.endswith(".pyc")}
    current = Path(directory).resolve()
    if current == source:
        ignored.update({".git", ".manifests", "artifacts"} & set(names))
    if current == source / "components" / "semantic":
        ignored.update({"records"} & set(names))
    return ignored


def install(source: Path, target: Path, old_target: Path, skill_roots: tuple[Path, Path]) -> None:
    token = uuid.uuid4().hex
    stage = target.parent / f".{SKILL}.stage-{token}"
    old_backup = old_target.parent / f".{old_target.name}.backup-{token}"
    target_backup = target.parent / f".{SKILL}.backup-{token}"
    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    promoted = False
    try:
        shutil.copytree(source, stage, ignore=lambda directory, names: copy_filter(directory, names, source))
        staged_python = stage / "dependencies/python/python.exe"
        run_checked("Release validation", [str(staged_python), "-X", "utf8", str(stage / "tools/validate_release.py")])

        if old_target.exists():
            old_target.rename(old_backup)
            moved.append((old_target, old_backup))
        if target.exists():
            target.rename(target_backup)
            moved.append((target, target_backup))
        stage.rename(target)
        promoted = True

        skill_dir = target / "skill" / SKILL
        for root in skill_roots:
            root.mkdir(parents=True, exist_ok=True)
            link = root / SKILL
            if os.path.lexists(link):
                if not owned_link(link, target, old_target):
                    raise RuntimeError(f"Unrelated skill entry exists: {link}")
                backup = root / f".{SKILL}.backup-{token}"
                link.rename(backup)
                moved.append((link, backup))
            pending = root / f".{SKILL}.pending-{token}"
            create_junction(pending, skill_dir)
            pending.rename(link)
            created.append(link)

        for root in skill_roots:
            for name in OLD_SKILLS:
                link = root / name
                if os.path.lexists(link) and owned_link(link, target, old_target):
                    backup = root / f".{name}.backup-{token}"
                    link.rename(backup)
                    moved.append((link, backup))

        print(json.dumps({"schema": "video-montage-install/v260928", "version": VERSION,
                          "install_root": str(target), "skill": SKILL,
                          "restart_codex_required": True}, ensure_ascii=False, indent=2))
    except Exception:
        for link in reversed(created):
            remove_tree(link)
        if promoted:
            remove_tree(target)
        for original, backup in reversed(moved):
            if backup.exists() or backup.is_junction():
                backup.rename(original)
        raise
    finally:
        if stage.exists():
            remove_tree(stage)
        for root in skill_roots:
            pending = root / f".{SKILL}.pending-{token}"
            if pending.is_junction():
                pending.rmdir()
    for _, backup in moved:
        try:
            remove_tree(backup)
        except OSError as exc:
            print(f"Backup cleanup failed: {backup}: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the portable video-montage skill.")
    parser.add_argument("-PreflightOnly", "--PreflightOnly", action="store_true")
    args = parser.parse_args(sys.argv[2:])

    source = Path(sys.argv[1]).resolve().parent
    profile = Path(os.environ.get("USERPROFILE") or Path.home()).resolve()
    target = profile / SKILL
    old_target = profile / "CodexMontageFF" / "20.2.5"
    if source == target or source.is_relative_to(target) or source == old_target or source.is_relative_to(old_target):
        raise RuntimeError("Installation source is inside a directory that would be removed")
    if target.is_junction() or target.is_symlink() or old_target.is_junction() or old_target.is_symlink():
        raise RuntimeError("Installation directories must not be links")

    python = source / "dependencies/python/python.exe"
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32 or Path(sys.executable).resolve() != python.resolve():
        raise RuntimeError("The bundled 64-bit Python 3.13.15 is required")
    run_checked("Runtime preflight", [str(python), "-X", "utf8", str(source / "tools/verify_runtime.py")])
    if args.PreflightOnly:
        print(json.dumps({"schema": "video-montage-preflight/v260928", "decision": "pass",
                          "version": VERSION}, ensure_ascii=False))
        return

    codex_home = Path(os.environ.get("CODEX_HOME") or profile / ".codex").resolve()
    skill_roots = (codex_home / "skills", profile / ".agents" / "skills")
    install(source, target, old_target, skill_roots)


try:
    main()
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
