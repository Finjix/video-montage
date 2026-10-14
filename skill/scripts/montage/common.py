"""Suite paths and the small file, hash and process helpers shared by every module."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = ROOT / "assets/dependencies"
PYTHON = DEPENDENCIES / "python/python.exe"
FFMPEG = DEPENDENCIES / "ffmpeg/bin/ffmpeg.exe"
FFPROBE = DEPENDENCIES / "ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = DEPENDENCIES / "models"
LARGE_FILE_BYTES = 8 * 1024 * 1024
_LARGE_DIGESTS: dict[tuple, str] = {}


def read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path: Path, value) -> None:
    """Atomic UTF-8 JSON write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha(path: Path) -> str:
    # Media is rehashed at every gate; reuse a digest only while its path, size
    # and mtime are unchanged. Small records are always rehashed.
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key in _LARGE_DIGESTS:
        return _LARGE_DIGESTS[key]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if stat.st_size >= LARGE_FILE_BYTES:
        _LARGE_DIGESTS[key] = digest.hexdigest()
    return digest.hexdigest()


def digest(value) -> str:
    """SHA-256 of canonical JSON (sorted keys, compact separators)."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def ref(path: Path) -> dict:
    return {"path": str(Path(path).resolve()), "sha256": sha(Path(path))}


def require_ref(value: dict, label: str) -> Path:
    if not isinstance(value, dict) or not value.get("path") or not value.get("sha256"):
        raise ValueError(f"{label}: missing reference")
    path = Path(value["path"]).resolve()
    if not path.is_file() or sha(path) != value["sha256"]:
        raise ValueError(f"{label}: changed or missing")
    return path


def run(command: list, *, binary: bool = False, cwd: Path | None = None):
    """Run a command and return its stdout; raise with the stderr tail on failure."""
    result = subprocess.run([str(part) for part in command], capture_output=True, text=not binary, cwd=cwd,
                            encoding=None if binary else "utf-8", errors=None if binary else "replace")
    if result.returncode:
        detail = result.stderr[-3000:] if result.stderr else ""
        if binary:
            detail = detail.decode("utf-8", errors="replace")
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path]))


def workers(jobs: int) -> int:
    """Concurrent FFmpeg jobs; override with VIDEO_MONTAGE_WORKERS."""
    configured = os.environ.get("VIDEO_MONTAGE_WORKERS")
    limit = int(configured) if configured else min(4, (os.cpu_count() or 1) // 4)
    return max(1, min(jobs, limit))


@contextmanager
def background(jobs: int):
    """Run independent FFmpeg-bound work beside the GPU/ASR loop."""
    pool = ThreadPoolExecutor(workers(jobs))
    try:
        yield pool
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def quietly(function, *args) -> None:
    """Warm an on-disk evidence cache; the caller recomputes and reports failures."""
    try:
        function(*args)
    except Exception:
        pass
