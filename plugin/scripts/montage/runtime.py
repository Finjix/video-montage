"""On-demand, verified runtime downloads. Importing this module never installs anything."""
from __future__ import annotations

import hashlib
import base64
import http.client
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "assets/runtime.json"
FEATURES = {"build": {"setuptools": "81.0.0", "wheel": "0.45.1", "packaging": "26.3"},
            "media": {"numpy": "2.5.3", "Pillow": "12.3.0"},
            "packaging": {"numpy": "2.5.3", "Pillow": "12.3.0", "opencv-python": "5.0.0.93"},
            "asr": {"qwen-asr": "0.0.6", "transformers": "4.57.6", "accelerate": "1.12.0",
                    "huggingface-hub": "0.36.2"}}


class RuntimeFailure(RuntimeError):
    """Unavailable runtime; retry the original command after fixing the reported cause."""


def cache() -> Path:
    root = Path(os.environ.get("VIDEO_MONTAGE_CACHE_DIR") or
                Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "video-montage").resolve()
    if root == ROOT or ROOT in root.parents:
        raise RuntimeFailure("VIDEO_MONTAGE_CACHE_DIR must be outside the plugin directory")
    return root


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def note(message: str):
    print(f"runtime: {message}", file=sys.stderr, flush=True)


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def valid(path: Path, spec: dict, *, remember=True) -> bool:
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    receipt = path.with_name(path.name + ".verified.json")
    stat = path.stat()
    signature = {"sha256": spec["sha256"], "size": stat.st_size, "mtime": stat.st_mtime_ns}
    try:
        if json.loads(receipt.read_text(encoding="utf-8")) == signature:
            return True
    except (OSError, ValueError):
        pass
    if checksum(path) != spec["sha256"]:
        return False
    if remember:
        save(receipt, signature)
    return True


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + f".{os.getpid()}.partial")
    partial.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


@contextmanager
def locked(name: str):
    """OS locks are released on crashes, so abandoned setup cannot block future runs."""
    folder = cache() / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(name.encode()).hexdigest() + ".lock")
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        deadline = time.monotonic() + 1800
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeFailure(f"Timed out waiting for {name}; retry the command")
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def fetch(path: Path, spec: dict) -> Path:
    """Resume a verified file from approved sources, without publishing partial downloads."""
    with locked(str(path)):
        if valid(path, spec):
            return path
        if os.environ.get("VIDEO_MONTAGE_OFFLINE") == "1":
            raise RuntimeFailure(f"Offline cache missing or damaged: {path}; retry online")
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        failures = []
        for url in spec["urls"]:
            for attempt in range(2):
                try:
                    offset = partial.stat().st_size if partial.exists() else 0
                    if offset >= spec["size"]:
                        if offset == spec["size"] and checksum(partial) == spec["sha256"]:
                            os.replace(partial, path)
                            valid(path, spec)
                            return path
                        partial.unlink(); offset = 0
                    headers = {"User-Agent": "video-montage/0.2.0"}
                    if offset:
                        headers["Range"] = f"bytes={offset}-"
                    note(f"downloading {path.name} ({offset}/{spec['size']} bytes)")
                    request = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(request, timeout=45) as response:
                        resumed = response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
                        with partial.open("ab" if resumed else "wb") as output:
                            received = offset if resumed else 0
                            last_progress = time.monotonic()
                            while block := response.read(1024 * 1024):
                                received += len(block)
                                if received > spec["size"]:
                                    raise ValueError("download larger than declared size")
                                output.write(block)
                                if time.monotonic() - last_progress >= 10:
                                    note(f"{path.name}: {received}/{spec['size']} bytes")
                                    last_progress = time.monotonic()
                    if partial.stat().st_size != spec["size"] or checksum(partial) != spec["sha256"]:
                        partial.unlink(missing_ok=True)
                        raise ValueError("size or SHA-256 mismatch")
                    os.replace(partial, path)
                    valid(path, spec)
                    return path
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    failures.append(f"{url}: {exc}")
                    if attempt == 0:
                        time.sleep(.25)
        raise RuntimeFailure(f"Cannot download {path.name}. Retry the original command. " + "; ".join(failures))


def device() -> str:
    forced = os.environ.get("VIDEO_MONTAGE_DEVICE", "auto")
    if forced not in ("auto", "cpu", "cuda"):
        raise RuntimeFailure("VIDEO_MONTAGE_DEVICE must be auto, cpu or cuda")
    if forced != "auto":
        return "cu128" if forced == "cuda" else "cpu"
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10,
                                creationflags=0x08000000 if os.name == "nt" else 0)
        match = re.search(r"CUDA(?: UMD)? Version:\s*(\d+)\.(\d+)", result.stdout)
        if result.returncode == 0 and match and tuple(map(int, match.groups())) >= (12, 8):
            return "cu128"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "cpu"


def uv() -> Path:
    path = cache() / "bootstrap/uv-0.13.4/uv.exe"
    if not path.is_file():
        raise RuntimeFailure("Start with scripts/video_montage.ps1 to prepare uv and Python")
    return path


def adopt_torch(version: str):
    """Reuse the migrated CUDA installation after verifying every RECORD hash.

    A cold install still uses the pinned upstream wheel. Existing users avoid a multi-GB
    download when the exact distribution is already available in their private cache.
    """
    source = cache() / "migration/python/Lib/site-packages"
    target = Path(sysconfig.get_path("purelib")).resolve()
    if source.resolve() == target or not source.is_dir():
        return
    try:
        if importlib.metadata.version("torch") == version:
            return
    except importlib.metadata.PackageNotFoundError:
        pass
    distribution = next((d for d in importlib.metadata.distributions(path=[str(source)])
                         if d.metadata["Name"].lower() == "torch" and d.version == version), None)
    if distribution is None:
        return
    files = []
    note(f"verifying migrated torch {version} before reuse")
    for entry in distribution.files or []:
        path = Path(distribution.locate_file(entry)).resolve()
        if source.resolve() not in path.parents:
            continue  # Console wrappers are not needed by inference.
        if not path.is_file() or (entry.size is not None and path.stat().st_size != entry.size):
            note("migrated torch is incomplete; downloading the pinned wheel instead")
            return
        if entry.hash:
            with path.open("rb") as stream:
                actual = base64.urlsafe_b64encode(hashlib.file_digest(stream, entry.hash.mode).digest()).rstrip(b"=").decode()
            if actual != entry.hash.value:
                note("migrated torch checksum mismatch; downloading the pinned wheel instead")
                return
        files.append((entry, path))
    if not files:
        return
    note(f"reusing verified torch {version} from the migrated cache")
    result = subprocess.run([str(uv()), "pip", "uninstall", "--python", sys.executable, "torch"],
                            stdout=sys.stderr, stderr=sys.stderr)
    if result.returncode:
        raise RuntimeFailure("Cannot replace the private PyTorch installation; retry the command")
    for entry, path in sorted(files, key=lambda item: ".dist-info" in str(item[0])):
        destination = target / entry
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    importlib.invalidate_caches()


def ensure(feature: str):
    if feature == "base":
        return
    if feature not in FEATURES:
        raise RuntimeFailure(f"Unknown runtime feature: {feature}")
    if feature == "asr":
        ensure("media")
        ensure("build")
    selected = device() if feature == "asr" else None
    name = f"asr-{selected}" if selected else feature
    lock = ROOT / "requirements" / f"{name}.txt"
    stamp = {"lock": checksum(lock), "python": sys.executable}
    receipt = cache() / "installed" / (hashlib.sha256(sys.executable.encode()).hexdigest() + f"-{name}.json")
    with locked("packages-" + sys.prefix):
        try:
            installed = json.loads(receipt.read_text(encoding="utf-8")) == stamp
        except (OSError, ValueError):
            installed = False
        if installed:
            try:
                for package, version in FEATURES[feature].items():
                    if importlib.metadata.version(package) != version:
                        installed = False
                if selected and importlib.metadata.version("torch") != f"2.11.0+{selected}":
                    installed = False
            except importlib.metadata.PackageNotFoundError:
                installed = False
        if installed:
            return
        if cache() not in Path(sys.executable).resolve().parents:
            raise RuntimeFailure("Use scripts/video_montage.ps1; dependencies are only installed into the private cache")
        note(f"preparing {name} dependencies")
        if selected:
            adopt_torch(f"2.11.0+{selected}")
        command = [str(uv()), "pip", "install", "--python", sys.executable, "--require-hashes",
                   "-r", str(lock)]
        if selected:
            # sox is pure Python but publishes only an sdist. Its build tools are pinned above.
            command += ["--no-build-isolation"]
        else:
            command += ["--only-binary", ":all:"]
        if os.environ.get("VIDEO_MONTAGE_OFFLINE") == "1":
            command += ["--offline"]
        if selected:
            command += ["--index", f"https://download.pytorch.org/whl/{selected}",
                        "--index-strategy", "unsafe-best-match"]
        env = {**os.environ, "UV_CACHE_DIR": str(cache() / "uv-cache")}
        result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, env=env)
        if result.returncode:
            raise RuntimeFailure(f"Dependency setup failed ({name}); retry the original command")
        importlib.invalidate_caches()
        save(receipt, stamp)


def binary(name: str) -> Path:
    spec = manifest()["ffmpeg"]
    folder = cache() / "tools" / f"ffmpeg-{spec['version']}"
    target = folder / f"{name}.exe"
    with locked("ffmpeg"):
        files = spec["files"]
        if all(valid(folder / filename, item) for filename, item in files.items()):
            return target
        archive = fetch(cache() / "downloads" / "ffmpeg.zip", spec["archive"])
        with zipfile.ZipFile(archive) as zipped:
            for filename, item in files.items():
                if valid(folder / filename, item):
                    continue
                member = next((v for v in zipped.infolist() if v.filename.endswith("/bin/" + filename)), None)
                if member is None:
                    raise RuntimeFailure(f"FFmpeg archive missing {filename}")
                folder.mkdir(parents=True, exist_ok=True)
                partial = folder / (filename + ".partial")
                with zipped.open(member) as source, partial.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                os.replace(partial, folder / filename)
                if not valid(folder / filename, item):
                    raise RuntimeFailure(f"FFmpeg checksum mismatch: {filename}")
        return target


class Executable(os.PathLike):
    """Resolve only when a command is actually constructed, never during module import."""
    def __init__(self, name):
        self.name = name

    def __fspath__(self):
        return str(binary(self.name))

    def __str__(self):
        return self.__fspath__()


def models() -> Path:
    ensure("asr")
    folder = cache() / "models"
    for name, spec in manifest()["models"].items():
        for filename, item in spec["files"].items():
            fetch(folder / name / filename, item)
    return folder


def animation(filename: str, digest: str) -> Path:
    spec = manifest()["animations"].get(filename)
    if not spec or spec["sha256"] != digest:
        raise RuntimeFailure(f"Unregistered animation table: {filename}")
    return fetch(cache() / "animations" / filename, spec)


def status(feature="base") -> dict:
    """Inspect only: no network, no import of optional libraries, no cache writes."""
    value = manifest()
    packages = {}
    requested = dict(FEATURES.get(feature, {}))
    if feature == "asr":
        requested.update(FEATURES["media"], torch=f"2.11.0+{device()}")
    for package, expected in requested.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[package] = {"version": actual, "expected": expected}
    missing = [name for name, row in packages.items() if row["version"] != row["expected"]]
    if feature in ("media", "packaging", "asr"):
        folder = cache() / "tools" / f"ffmpeg-{value['ffmpeg']['version']}"
        missing += [name for name, spec in value["ffmpeg"]["files"].items()
                    if not valid(folder / name, spec, remember=False)]
    if feature == "asr":
        for name, spec in value["models"].items():
            missing += [f"{name}/{file}" for file, item in spec["files"].items()
                        if not valid(cache() / "models" / name / file, item, remember=False)]
    return {"feature": feature, "cache_dir": str(cache()), "python": sys.executable,
            "packages": packages, "missing": missing}
