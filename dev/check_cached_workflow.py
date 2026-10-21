"""Prove warm prepare, annotations and check need neither network nor ASR setup."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin/scripts"))
from montage import cli, runtime


def main():
    job = Path(json.loads((ROOT / "dev/smoke/latest.json").read_text(encoding="utf-8"))["job_dir"])
    commands = [["prepare", "--job-dir", str(job)],
                ["annotate", "--job-dir", str(job), "--file", str(job / "config/smoke-annotations.json")],
                ["check", "--job-dir", str(job), "--plan", str(job / "config/smoke-plan.json")]]
    with patch.dict(os.environ, {"VIDEO_MONTAGE_OFFLINE": "1"}), \
            patch.object(runtime, "models", side_effect=AssertionError("ASR models requested")), \
            patch("urllib.request.urlopen", side_effect=AssertionError("Network requested")):
        for arguments in commands:
            with patch.object(sys, "argv", ["video_montage.py", *arguments]):
                try:
                    cli.main()
                except SystemExit as result:
                    assert result.code == 0, (arguments[0], result.code)
    print("CACHED_WORKFLOW_PASS")


if __name__ == "__main__":
    main()
