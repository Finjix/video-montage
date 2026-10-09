"""Lab context and shared production animation primitives."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/packaging/scripts"))
from animation_common import *
LAB = Path(__file__).resolve().parent
