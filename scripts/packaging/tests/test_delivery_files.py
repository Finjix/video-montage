from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/delivery_files.py"
SPEC = importlib.util.spec_from_file_location("delivery_files_regression", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublicationRecoveryTests(unittest.TestCase):
    def test_rollback_failure_keeps_backup_and_restores_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copies = []
            for index in range(3):
                source, target = root / f"new-{index}.mp4", root / f"old-{index}.mp4"
                source.write_bytes(f"new {index}".encode())
                target.write_bytes(f"old {index}".encode())
                copies.append((source, target))
            original_replace = publisher.os.replace

            def fail(source, target):
                if str(source).endswith(".publish") and target == copies[2][1]:
                    raise OSError("third publication failed")
                if Path(source).parent.name.startswith("publish-backup-") and target == copies[1][1]:
                    raise OSError("second rollback failed")
                return original_replace(source, target)

            with patch.object(publisher.os, "replace", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "backups retained") as raised:
                    publisher.publish_files(copies, root / "runtime")
            self.assertEqual(b"old 0", copies[0][1].read_bytes())
            self.assertEqual(b"old 2", copies[2][1].read_bytes())
            backups = list((root / "runtime").glob("publish-backup-*"))
            self.assertEqual(1, len(backups))
            self.assertIn(str(backups[0]), str(raised.exception))
            self.assertEqual(b"old 1", (backups[0] / "1").read_bytes())
            self.assertFalse(list(root.glob("*.publish")))


if __name__ == "__main__":
    unittest.main()
