from __future__ import annotations

import importlib
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "assets/dependencies/python/python.exe",
    "assets/dependencies/ffmpeg/bin/ffmpeg.exe",
    "assets/dependencies/ffmpeg/bin/ffprobe.exe",
    "assets/dependencies/models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf/model.bin",
    "SKILL.md",
    "scripts/semantic/scripts/v15_orchestrator.py",
    "scripts/semantic/scripts/v20_frame_plan_gate.py",
    "scripts/semantic/scripts/portable_frame_renderer.py",
    "scripts/semantic/scripts/source_timing.py",
    "scripts/semantic/scripts/frame_range_repair.py",
    "scripts/controller/scripts/ffmpeg_controller.py",
    "scripts/packaging/scripts/package_video.py",
    "scripts/packaging/scripts/delivery_files.py",
    "scripts/packaging/scripts/packaging_design.py",
    "scripts/packaging/scripts/design_renderer.py",
    "scripts/packaging/scripts/reference_animation.py",
    "scripts/packaging/scripts/animation_common.py",
    "scripts/packaging/scripts/animation_shader.py",
    "assets/packaging/design/catalog.json",
    "assets/packaging/animations/parameters/static.json",
    "assets/packaging/animations/parameters/bounce_up.json",
    "assets/packaging/animations/parameters/shout_wave.json",
    "assets/packaging/animations/parameters/ice_drift.json",
    "scripts/executor/scripts/three_suite_ff.py",
    "scripts/autonomous/scripts/autonomous_montage.py",
)


def packaging_resources() -> list[str]:
    failures = []
    checked = {}
    def check(path: Path, expected=None):
        if not path.is_file():
            failures.append(f"missing packaging resource: {path}")
        elif expected:
            if path not in checked:
                hasher = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        hasher.update(chunk)
                checked[path] = hasher.hexdigest()
            if checked[path] != expected:
                failures.append(f"changed packaging resource: {path}")
    def visit(item, base):
        if isinstance(item, dict):
            if item.get("table_file"):
                check(base / item["table_file"], item.get("table_sha256"))
            if isinstance(item.get("preview"), dict):
                check(base / item["preview"]["path"], item["preview"].get("sha256"))
            if isinstance(item.get("motion_preview"), dict):
                check(base / item["motion_preview"]["path"], item["motion_preview"].get("sha256"))
            for child in item.values():
                visit(child, base)
        elif isinstance(item, list):
            for child in item:
                visit(child, base)
    for path in [ROOT / "assets/packaging/design/catalog.json", *(ROOT / "assets/packaging/animations/parameters").glob("*.json")]:
        try:
            visit(json.loads(path.read_text(encoding="utf-8")), path.parent)
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"invalid packaging resource: {path}: {exc}")
    return failures


def verify() -> dict:
    failures = [f"missing: {path}" for path in REQUIRED if not (ROOT / path).is_file()]
    failures.extend(packaging_resources())
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32:
        failures.append("bundled 64-bit Python 3.13.15 required")
    for name in ("faster_whisper", "numpy", "PIL", "av", "cv2", "onnxruntime", "yaml"):
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"module {name}: {exc}")
    for binary in ("ffmpeg", "ffprobe"):
        path = ROOT / "assets" / "dependencies" / "ffmpeg" / "bin" / f"{binary}.exe"
        if path.is_file():
            result = subprocess.run([str(path), "-version"], capture_output=True, text=True)
            if result.returncode:
                failures.append(f"{binary} failed")
    ffmpeg = ROOT / "assets/dependencies/ffmpeg/bin/ffmpeg.exe"
    if ffmpeg.is_file():
        filters = subprocess.run([str(ffmpeg), "-hide_banner", "-filters"], capture_output=True, text=True)
        if filters.returncode or any(f" {name} " not in filters.stdout for name in ("ass", "overlay", "amix")):
            failures.append("FFmpeg packaging filters unavailable: ass, overlay, amix")
    model = ROOT / REQUIRED[3]
    if model.is_file() and model.stat().st_size < 1_500_000_000:
        failures.append("Whisper model incomplete")
    return {"schema": "video-montage-runtime-check/v260928", "decision": "pass" if not failures else "reject", "failures": failures}


if __name__ == "__main__":
    report = verify()
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["decision"] == "pass" else 2)
