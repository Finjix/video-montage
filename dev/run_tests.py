"""Run the regression tests with the bundled Python (it ignores PYTHONPATH, so paths are injected here).

    skill/assets/dependencies/python/python.exe dev/run_tests.py [pattern]
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "skill/scripts"), str(ROOT / "dev/tests")]

if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else "test_*.py"
    suite = unittest.defaultTestLoader.discover(str(ROOT / "dev/tests"), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
