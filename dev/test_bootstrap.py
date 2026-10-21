"""Cold start with no Python on PATH, Unicode paths, JSON stdin and offline reuse."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def main():
    folder = Path(tempfile.mkdtemp(prefix="vm-bootstrap-")) / "中文 路径"
    folder.mkdir()
    plugin = folder / "plugin"
    shutil.copytree(ROOT / "plugin", plugin)
    before = snapshot(plugin)
    environment = {**os.environ, "VIDEO_MONTAGE_CACHE_DIR": str(folder / "cache"),
                   "VIDEO_MONTAGE_OFFLINE": "0", "PATH": str(Path(os.environ["SystemRoot"]) / "System32")}
    cmd = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"

    def invoke(args, text=None, expected=0):
        command = f'{cmd} /d /s /c "{subprocess.list2cmdline([str(plugin / "scripts/video_montage.cmd"), *args])}"'
        result = subprocess.run(command, input=text, capture_output=True, text=True, encoding="utf-8", env=environment, timeout=300)
        assert result.returncode == expected, (args, result.stdout, result.stderr)
        return result.stdout

    assert "prepare" in invoke(["--help"])
    report = json.loads(invoke(["runtime"]))
    assert report["decision"] == "pass"
    source = folder / "素材.mp4"
    source.write_bytes(b"integration fixture")
    nameplate = folder / "铭牌.png"
    nameplate.write_bytes(b"integration fixture")
    order = {"requested_outputs": 3, "sources": [str(source)], "asset_root": str(folder), "output_root": str(folder / "output"),
             "request": "中文 stdin 测试", "celebrities": [{"person_id": "person:test", "name": "测试", "nameplate": str(nameplate)}]}
    result = invoke(["init", "--work-order", "-"], json.dumps(order, ensure_ascii=False))
    job = Path(result.splitlines()[0].split(": ", 1)[1])
    saved = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert saved["order"]["request"] == order["request"]
    environment["VIDEO_MONTAGE_OFFLINE"] = "1"
    invoke(["runtime"])
    invoke(["status", "--job-dir", str(job)])
    invoke(["check", "--job-dir", str(job), "--plan", str(folder / "missing.json")], expected=2)
    python = folder / "cache/environment/Scripts/python.exe"
    result = subprocess.run([str(python), "-B", "-c", "import importlib.util; assert importlib.util.find_spec('numpy') is None"], env=environment)
    assert result.returncode == 0
    assert before == snapshot(plugin), "Plugin folder changed during execution"
    print(json.dumps({"cold_start": "pass", "unicode_json_stdin": "pass", "offline_reuse": "pass", "plugin_immutable": True,
                      "test_directory": str(folder)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
