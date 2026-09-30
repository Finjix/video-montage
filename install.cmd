@echo off
setlocal
set "SUITE_DIR=%~dp0"
set "NO_PAUSE="
set "ACTION=Installation"
if /I "%~1"=="-NoPause" set "NO_PAUSE=1"
if /I "%~2"=="-NoPause" set "NO_PAUSE=1"
if /I "%~1"=="-PreflightOnly" set "ACTION=Preflight"
if /I "%~2"=="-PreflightOnly" set "ACTION=Preflight"
if not exist "%SUITE_DIR%assets\dependencies\python\python.exe" (
  echo Bundled Python is missing: "%SUITE_DIR%assets\dependencies\python\python.exe"
  if not defined NO_PAUSE (
    echo Press any key to close this window.
    pause >nul
  )
  exit /b 2
)
"%SUITE_DIR%assets\dependencies\python\python.exe" -X utf8 -c "import pathlib,sys; body=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig').split(chr(10)+'# BEGIN PYTHON DEPLOY'+chr(10),1)[1]; exec(compile(body,sys.argv[1],'exec'))" "%~f0" %*
set "EXIT_CODE=%errorlevel%"
if "%EXIT_CODE%"=="0" (
  echo %ACTION% completed successfully.
) else (
  echo %ACTION% failed with exit code %EXIT_CODE%. See the error above.
)
if not defined NO_PAUSE (
  echo Press any key to close this window.
  pause >nul
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


def progress(message: str) -> None:
    print(f"[安装中] {message}", flush=True)


def run_checked(label: str, command: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
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


def copy_filter(directory: str, names: list[str], source: Path) -> set[str]:
    ignored = {name for name in names if name in {"__pycache__", ".pytest_cache"} or name.endswith(".pyc")}
    current = Path(directory).resolve()
    if current == source:
        ignored.update(set(names) - {"SKILL.md", "agents", "scripts", "references", "assets"})
    if current.is_relative_to(source / "scripts") or current.is_relative_to(source / "references"):
        ignored.update({"tests", "records", "__pycache__", ".pytest_cache"} & set(names))
    return ignored


def managed_install(path: Path, *, skill_at_root: bool) -> bool:
    if not path.is_dir() or path.is_junction() or path.is_symlink():
        return False
    markers = [path / "SKILL.md"]
    if not skill_at_root:
        markers.append(path / "skill/video-montage/SKILL.md")
    executors = [path / "scripts/executor/scripts/three_suite_ff.py",
                 path / "components/executor/scripts/three_suite_ff.py"]
    return any(executor.is_file() for executor in executors) and any(
        marker.is_file() and "name: video-montage" in marker.read_text(encoding="utf-8-sig")
        for marker in markers)


def install(source: Path, target: Path, legacy_target: Path, agent_link: Path) -> None:
    token = uuid.uuid4().hex
    stage = target.parent.parent / f".{SKILL}.stage-{token}"
    target_backup = target.parent.parent / f".{SKILL}.backup-{token}"
    legacy_backup = legacy_target.parent / f".{SKILL}.backup-{token}"
    moved: list[tuple[Path, Path]] = []
    promoted = False
    try:
        progress("正在运行源包发布校验，请稍候...")
        run_checked("Release validation", [str(source / "assets/dependencies/python/python.exe"),
                                            "-X", "utf8", str(source / "tools/validate_release.py")])
        target.parent.mkdir(parents=True, exist_ok=True)
        progress("正在复制工作流必需文件，请稍候...")
        shutil.copytree(source, stage, ignore=lambda directory, names: copy_filter(directory, names, source))
        staged_python = stage / "assets/dependencies/python/python.exe"
        progress("正在检查精简安装副本，请稍候...")
        run_checked("Staged runtime validation", [str(staged_python), "-X", "utf8", str(stage / "scripts/verify_runtime.py")])
        run_checked("Staged suite preflight", [str(staged_python), "-X", "utf8",
                                              str(stage / "scripts/executor/scripts/three_suite_ff.py"),
                                              "--suite-root", str(stage), "preflight"])

        progress("正在启用安装文件...")
        if os.path.lexists(target):
            if target.is_junction():
                if target.resolve() != (legacy_target / "skill" / SKILL).resolve():
                    raise RuntimeError(f"Unrelated skill entry exists: {target}")
            elif not managed_install(target, skill_at_root=True):
                raise RuntimeError(f"Unrelated skill entry exists: {target}")
            target.rename(target_backup)
            moved.append((target, target_backup))
        if legacy_target.exists():
            if not managed_install(legacy_target, skill_at_root=False):
                raise RuntimeError(f"Legacy installation contains unrelated files: {legacy_target}")
            legacy_target.rename(legacy_backup)
            moved.append((legacy_target, legacy_backup))
        stage.rename(target)
        promoted = True

        progress("正在注册 Codex 技能...")
        run_checked("Installed skill validation", [str(target / "assets/dependencies/python/python.exe"),
                                                   str(target / "scripts/validate_skill.py"), str(target)])
        if agent_link.is_junction() and agent_link.resolve() in {target, legacy_target / "skill" / SKILL}:
            backup = agent_link.parent / f".{SKILL}.backup-{token}"
            agent_link.rename(backup)
            moved.append((agent_link, backup))

        print(json.dumps({"schema": "video-montage-install/v260928", "version": VERSION,
                          "install_root": str(target), "skill": SKILL,
                          "restart_codex_required": True}, ensure_ascii=False, indent=2))
    except Exception:
        if promoted:
            remove_tree(target)
        for original, backup in reversed(moved):
            if backup.exists() or backup.is_junction():
                backup.rename(original)
        raise
    finally:
        if stage.exists():
            remove_tree(stage)
    for _, backup in moved:
        try:
            remove_tree(backup)
        except OSError as exc:
            print(f"Backup cleanup failed: {backup}: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the portable video-montage skill.")
    parser.add_argument("-PreflightOnly", "--PreflightOnly", action="store_true")
    parser.add_argument("-NoPause", "--NoPause", action="store_true")
    args = parser.parse_args(sys.argv[2:])

    source = Path(sys.argv[1]).resolve().parent
    profile = Path(os.environ.get("USERPROFILE") or Path.home()).resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or profile / ".codex").resolve()
    target = codex_home / "skills" / SKILL
    legacy_target = profile / SKILL
    if source == target or source.is_relative_to(target) or source == legacy_target or source.is_relative_to(legacy_target):
        raise RuntimeError("Installation source is inside a directory that would be removed")
    if legacy_target.is_junction() or legacy_target.is_symlink() or target.is_symlink():
        raise RuntimeError("Installation directories must not be links")

    python = source / "assets/dependencies/python/python.exe"
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32 or Path(sys.executable).resolve() != python.resolve():
        raise RuntimeError("The bundled 64-bit Python 3.13.15 is required")
    progress("正在检查运行环境...")
    run_checked("Runtime preflight", [str(python), "-X", "utf8", str(source / "scripts/verify_runtime.py")])
    if args.PreflightOnly:
        print(json.dumps({"schema": "video-montage-preflight/v260928", "decision": "pass",
                          "version": VERSION}, ensure_ascii=False))
        return

    agent_link = profile / ".agents" / "skills" / SKILL
    install(source, target, legacy_target, agent_link)


try:
    main()
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
