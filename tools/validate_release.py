from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / "assets/dependencies/python/python.exe"
SEMANTIC = ROOT / "scripts/semantic"
CONTROLLER = ROOT / "scripts/controller"
PACKAGING = ROOT / "scripts/packaging"
EXECUTOR = ROOT / "scripts/executor"
AUTONOMOUS = ROOT / "scripts/autonomous"


def run(name: str, command: list[str]) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return {"name": name, "passed": result.returncode == 0,
            "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}


def validate() -> dict:
    checks = [
        run("runtime", [str(PYTHON), str(ROOT / "scripts/verify_runtime.py")]),
        run("semantic_tests", [str(PYTHON), "-B", "-m", "unittest", "discover", "-s", str(SEMANTIC / "tests"), "-p", "test_*.py"]),
        run("controller_tests", [str(PYTHON), "-B", "-m", "unittest", "discover", "-s", str(CONTROLLER / "tests"), "-p", "test_*.py"]),
        run("packaging_tests", [str(PYTHON), "-B", "-m", "unittest", "discover", "-s", str(PACKAGING / "tests"), "-p", "test_*.py"]),
        run("autonomous_tests", [str(PYTHON), "-B", "-m", "unittest", "discover", "-s", str(AUTONOMOUS / "tests"), "-p", "test_*.py"]),
        run("integrity_tests", [str(PYTHON), "-B", "-m", "unittest", "discover", "-s", str(ROOT / "tools"), "-p", "test_release_integrity.py"]),
        run("single_skill", [str(PYTHON), str(ROOT / "scripts/validate_skill.py"), str(ROOT)]),
        run("suite_preflight", [str(PYTHON), str(EXECUTOR / "scripts/three_suite_ff.py"), "--suite-root", str(ROOT), "preflight"]),
    ]
    with tempfile.TemporaryDirectory(prefix="video-montage-check-") as temporary:
        checks.append(run("controller_preflight", [str(PYTHON), str(CONTROLLER / "scripts/ffmpeg_controller.py"), "preflight", "--output", str(Path(temporary) / "controller.json")]))
    for script in (*SEMANTIC.glob("scripts/*.py"), *CONTROLLER.glob("scripts/*.py"), *PACKAGING.glob("scripts/*.py"),
                   *EXECUTOR.glob("scripts/*.py"), *AUTONOMOUS.glob("scripts/*.py")):
        try:
            ast.parse(script.read_text(encoding="utf-8-sig"), filename=str(script))
        except SyntaxError as exc:
            checks.append({"name": str(script.relative_to(ROOT)), "passed": False, "stderr": str(exc)})
    return {"schema": "video-montage-release-check/v260928",
            "decision": "pass" if all(check["passed"] for check in checks) else "reject",
            "checks": checks}


if __name__ == "__main__":
    report = validate()
    failed = [check for check in report["checks"] if not check["passed"]]
    print(json.dumps({"decision": report["decision"], "passed": len(report["checks"]) - len(failed),
                      "total": len(report["checks"]), "failed": failed}, ensure_ascii=True))
    raise SystemExit(0 if report["decision"] == "pass" else 2)
