"""Python entry point; use video_montage.ps1 to bootstrap the private runtime."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.cli import main

if __name__ == "__main__":
    main()
