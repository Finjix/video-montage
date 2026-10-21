"""Check pinned remote model files and the FFmpeg archive against the release manifest."""
import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin/assets/runtime.json"


def check(item):
    name, spec = item
    url = spec["urls"][0]
    if name.endswith(".safetensors"):
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=45) as response:
            assert int(response.headers["Content-Length"]) == spec["size"], name
        return name
    with urllib.request.urlopen(url, timeout=45) as response:
        content = response.read()
    assert len(content) == spec["size"] and hashlib.sha256(content).hexdigest() == spec["sha256"], name
    return name


def main():
    value = json.loads(MANIFEST.read_text())
    archive = Path(os.environ["LOCALAPPDATA"]) / "video-montage/downloads/ffmpeg.zip"
    if archive.is_file():
        assert hashlib.file_digest(archive.open("rb"), "sha256").hexdigest() == value["ffmpeg"]["archive"]["sha256"]
        with zipfile.ZipFile(archive) as zipped:
            for name, spec in value["ffmpeg"]["files"].items():
                entry = next(v for v in zipped.infolist() if v.filename.endswith("/bin/" + name))
                with zipped.open(entry) as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if "--pin-ffmpeg" in sys.argv:
                    spec.update(sha256=actual, size=entry.file_size)
                else:
                    assert actual == spec["sha256"] and entry.file_size == spec["size"], name
        if "--pin-ffmpeg" in sys.argv:
            MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = [(f"{name}/{file}", spec) for name, model in value["models"].items() for file, spec in model["files"].items()]
    with concurrent.futures.ThreadPoolExecutor(4) as executor:
        for name in executor.map(check, files):
            print(f"verified remote: {name}", flush=True)


if __name__ == "__main__":
    main()
