"""Suite paths and the small file, hash, process and logging helpers shared by every module."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = ROOT / "assets/dependencies"
FFMPEG = DEPENDENCIES / "ffmpeg/bin/ffmpeg.exe"
FFPROBE = DEPENDENCIES / "ffmpeg/bin/ffprobe.exe"
MODEL_ROOT = DEPENDENCIES / "models"
LIBRARY = ROOT / "assets/text-library.json"
REJECTED = ROOT / "assets/rejected-source-intervals.json"
FONT = ROOT / "assets/packaging/fonts/WenYue-XinQingNianTi-W8.otf"
_DIGESTS: dict[tuple, str] = {}


def desktop() -> Path:
    """The user's Desktop folder, honouring OneDrive/folder redirection."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Desktop"


OUTPUT_ROOT = desktop()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path: Path, value) -> Path:
    """Atomic UTF-8 JSON write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def sha(path: Path) -> str:
    """SHA-256 of a file, cached per process while path, size and mtime are unchanged."""
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _DIGESTS:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        _DIGESTS[key] = digest.hexdigest()
    return _DIGESTS[key]


def digest(value) -> str:
    """SHA-256 of canonical JSON."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def run(command: list, *, binary: bool = False, cwd: Path | None = None):
    """Run a command and return its stdout; raise with the stderr tail on failure."""
    result = subprocess.run([str(part) for part in command], capture_output=True, text=not binary, cwd=cwd,
                            encoding=None if binary else "utf-8", errors=None if binary else "replace")
    if result.returncode:
        detail = result.stderr[-3000:] if result.stderr else ""
        if binary:
            detail = detail.decode("utf-8", errors="replace")
        raise RuntimeError(f"command failed ({result.returncode}): {detail.strip()}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path]))


def workers(jobs: int) -> int:
    """Concurrent FFmpeg jobs; override with VIDEO_MONTAGE_WORKERS."""
    configured = os.environ.get("VIDEO_MONTAGE_WORKERS")
    limit = int(configured) if configured else min(4, max(1, (os.cpu_count() or 1) // 4))
    return max(1, min(jobs, limit))


@contextmanager
def pool(jobs: int):
    executor = ThreadPoolExecutor(workers(jobs))
    try:
        yield executor
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


_STARTED = time.monotonic()


def log(message: str) -> None:
    """One progress line for the agent on stderr; results go to stdout."""
    print(f"[{time.monotonic() - _STARTED:6.1f}s] {message}", file=sys.stderr, flush=True)


def quiet_ml() -> None:
    """Silence model-loading progress bars and generation warnings nobody reads."""
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import warnings
    warnings.filterwarnings("ignore")
