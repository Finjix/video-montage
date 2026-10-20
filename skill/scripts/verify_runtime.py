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
    "assets/dependencies/models/Qwen3-ASR-1.7B/model.safetensors.index.json",
    "assets/dependencies/models/Qwen3-ASR-1.7B/config.json",
    "assets/dependencies/models/Qwen3-ForcedAligner-0.6B/model.safetensors",
    "assets/dependencies/models/Qwen3-ForcedAligner-0.6B/config.json",
    "SKILL.md",
    "assets/text-library.json",
    "assets/rejected-source-intervals.json",
    "assets/packaging/design/catalog.json",
    *(f"assets/packaging/animations/parameters/{name}.json" for name in ("static", "bounce_up", "shout_wave", "ice_drift")),
    "scripts/video_montage.py",
    *(f"scripts/montage/{name}.py" for name in ("common", "cli", "job", "catalog", "asr", "plan", "rules", "batch",
                                                 "render", "audio", "package", "bgm", "text_library")),
    *(f"scripts/montage/packaging/{name}.py" for name in ("design", "design_renderer", "fonts", "segmentation",
                                                           "dynamic_flowers", "flower_effects", "reference_animation")),
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


def safetensors_complete(path: Path) -> bool:
    """A safetensors file is whole when its header's last tensor ends exactly at the end of the file."""
    try:
        with path.open("rb") as source:
            size = int.from_bytes(source.read(8), "little")
            header = json.loads(source.read(size))
        end = max((entry["data_offsets"][1] for name, entry in header.items() if name != "__metadata__"), default=0)
        return path.stat().st_size == 8 + size + end
    except (OSError, ValueError, KeyError):
        return False


def asr_model_failures() -> list[str]:
    """Qwen3-ASR and aligner weights must be complete, every indexed shard included."""
    models = ROOT / "assets/dependencies/models"
    failures = []
    try:
        index = json.loads((models / "Qwen3-ASR-1.7B/model.safetensors.index.json").read_text(encoding="utf-8"))
        shards = [models / "Qwen3-ASR-1.7B" / name for name in sorted(set(index["weight_map"].values()))]
    except (OSError, ValueError, KeyError):
        shards = []
    if not shards or not all(safetensors_complete(shard) for shard in shards):
        failures.append("Qwen3-ASR model incomplete")
    if not safetensors_complete(models / "Qwen3-ForcedAligner-0.6B/model.safetensors"):
        failures.append("Qwen3-ForcedAligner model incomplete")
    return failures


def verify() -> dict:
    failures = [f"missing: {path}" for path in REQUIRED if not (ROOT / path).is_file()]
    failures.extend(packaging_resources())
    if sys.version_info[:3] != (3, 13, 15) or sys.maxsize <= 2**32:
        failures.append("bundled 64-bit Python 3.13.15 required")
    for name in ("torch", "transformers", "qwen_asr", "numpy", "PIL", "cv2"):
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
        needed = ("overlay", "amix", "loudnorm", "aeval", "concat", "trim", "atrim")
        if filters.returncode or any(f" {name} " not in filters.stdout for name in needed):
            failures.append("FFmpeg filters unavailable: " + ", ".join(needed))
    failures.extend(asr_model_failures())
    return {"decision": "pass" if not failures else "reject", "failures": failures}


if __name__ == "__main__":
    report = verify()
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["decision"] == "pass" else 2)
