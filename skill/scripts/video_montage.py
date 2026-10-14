"""Command-line entry point: python scripts/video_montage.py <command> --job-dir <job_dir> [options]."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.workflow import main

if __name__ == "__main__":
    main()
