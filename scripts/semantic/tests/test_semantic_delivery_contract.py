from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("semantic_delivery_contract", SCRIPTS / "v9_deliver.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class SemanticDeliveryContractTests(unittest.TestCase):
    def test_manifest_binds_complete_batch_for_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"test-video")
            work = write(root / "work.json", {"requested_outputs": 1})
            inventory = write(root / "inventory.json", {})
            request = write(root / "request.json", {
                "package_id": "video-montage",
                "work_order_path": str(work), "work_order_sha256": delivery.gate.sha_file(work),
                "candidate_inventory_path": str(inventory), "candidate_inventory_sha256": delivery.gate.sha_file(inventory),
            })
            index = write(root / "index.json", {
                "request_path": str(request), "request_sha256": delivery.gate.sha_file(request),
                "plans": [{"plan_id": "01"}],
            })
            authorization = write(root / "authorization.json", {
                "schema": "semantic-release-authorization/v260928", "decision": "pass", "failures": [],
                "locked_index_path": str(index), "locked_index_sha256": delivery.gate.sha_file(index),
                "authorized_outputs": [{"plan_id": "01", "export_path": str(source), "export_sha256": delivery.gate.sha_file(source)}],
            })
            manifest = root / "manifest.json"
            args = ["--release-authorization", str(authorization), "--delivery-dir", str(root / "delivery"), "--manifest", str(manifest)]
            with patch.object(delivery.audit, "audit_request", return_value={"schema": "video-montage-fail-closed-prelock-report/v260928", "decision": "pass"}):
                self.assertEqual(0, delivery.main(args))
            result = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("v260928", result["package_version"])
            self.assertEqual(["01"], result["authorized_outputs"])
            self.assertEqual({"prelock_audit", "batch_lock_request", "candidate_inventory", "work_order"}, set(result["evidence"]))
            request.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "batch lock request changed"):
                delivery.main(args)


if __name__ == "__main__":
    unittest.main()
