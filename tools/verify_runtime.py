from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "dependencies/python/python.exe",
    "dependencies/ffmpeg/bin/ffmpeg.exe",
    "dependencies/ffmpeg/bin/ffprobe.exe",
    "dependencies/models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf/model.bin",
    "skill/video-montage/SKILL.md",
    "uninstall.cmd",
    "tools/uninstall.ps1",
    "package.cmd",
    "tools/package_release.py",
    "components/semantic/scripts/v15_orchestrator.py",
    "components/semantic/scripts/v20_frame_plan_gate.py",
    "components/semantic/scripts/portable_frame_renderer.py",
    "components/semantic/scripts/frame_range_repair.py",
    "components/controller/scripts/ffmpeg_controller.py",
    "components/packaging/scripts/package_video.py",
    "components/executor/scripts/three_suite_ff.py",
)


def verify() -> dict:
    failures = [f"missing: {path}" for path in REQUIRED if not (ROOT / path).is_file()]
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32:
        failures.append("bundled 64-bit Python 3.13.15 required")
    for name in ("faster_whisper", "numpy", "PIL", "av", "cv2", "onnxruntime", "yaml"):
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f"module {name}: {exc}")
    for binary in ("ffmpeg", "ffprobe"):
        path = ROOT / "dependencies" / "ffmpeg" / "bin" / f"{binary}.exe"
        if path.is_file():
            result = subprocess.run([str(path), "-version"], capture_output=True, text=True)
            if result.returncode:
                failures.append(f"{binary} failed")
    ffmpeg = ROOT / "dependencies/ffmpeg/bin/ffmpeg.exe"
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
