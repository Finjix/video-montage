from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v15_orchestrator", ROOT / "scripts" / "v15_orchestrator.py")
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runtime)


class V15OrchestratorContractTests(unittest.TestCase):
    def make_config(self, root: Path) -> dict:
        profile = root / "profile.json"
        manifest = root / "sources.json"
        contract = root / "content-contract.json"
        profile.write_text(json.dumps({"profile": "test/v260928", "policy": {"inventory_mode": "witness_sufficient"}}), encoding="utf-8")
        manifest.write_text(json.dumps({"source_count": 1, "sources": [{"source_id": "s1"}]}), encoding="utf-8")
        contract.write_text(json.dumps({"schema": "celebrity-product-content-grounding-contract/v260928", "contract_id": "test-content/v260928"}), encoding="utf-8")
        return {
            "schema": "semantic-production-pipeline-config/v260928",
            "task_id": "test-v17",
            "task_root": str(root),
            "mode": "production",
            "phases": [{"name": "NOOP", "kind": "local", "command": ["python", "-c", "pass"]}],
            "heartbeat": {"required": True},
            "checkpoint_command": ["python", "-c", "pass"],
            "execution": {"foreground_required": True, "heartbeat_recovery_only": True, "heartbeat_stale_after_seconds": 180, "recovery_budget_seconds": 60},
            "job_contract": {
                "requested_outputs": 1,
                "style_profile_id": "test/v260928",
                "content_grounding_contract_id": "test-content/v260928",
                "inventory_mode": "witness_sufficient",
                "source_scope_mode": "explicit_witness_manifest",
                "source_scope_total": 1,
                "source_scope_justification": "test witness",
            },
            "variables": {
                "style_profile": str(profile),
                "content_grounding_contract": str(contract),
                "source_manifest": str(manifest),
                "source_scope_total": "1",
                "requested_outputs": "1",
            },
        }

    def test_valid_content_contract_binding_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime.validate_config(self.make_config(Path(temp)))

    def test_missing_content_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = self.make_config(Path(temp))
            Path(config["variables"]["content_grounding_contract"]).unlink()
            with self.assertRaisesRegex(ValueError, "V18_CONTENT_GROUNDING_CONTRACT_MISSING"):
                runtime.validate_config(config)


if __name__ == "__main__":
    unittest.main()
