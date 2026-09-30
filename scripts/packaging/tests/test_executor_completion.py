from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SUITE = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("packaging_executor_test", SUITE / "scripts/executor/scripts/three_suite_ff.py")
executor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(executor)


class ExecutorPackagingCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name)
        self.clean = self.job / "clean.mp4"
        self.clean.write_bytes(b"clean")
        self.semantic = self.write("semantic.json", {"schema": "semantic-delivery-manifest/v260928",
                                                      "authorized_outputs": ["P1"], "evidence": {}})
        self.delivery = self.write("delivery.json", {"schema": "ffmpeg-controller-delivery/v260928",
                                                      "semantic_release_path": str(self.semantic),
                                                      "semantic_release_sha256": executor.sha(self.semantic),
                                                      "results": [{"plan_id": "P1", "output_path": str(self.clean),
                                                                   "output_sha256": executor.sha(self.clean)}]})
        self.post = self.write("post.json", {"delivery_manifest_sha256": executor.sha(self.delivery)})
        self.opening = self.write("opening.json", {"delivery_manifest_sha256": executor.sha(self.delivery)})
        self.validation = self.write("validation.json", {"schema": "ffmpeg-controller-validation/v260928",
                                                          "decision": "pass", "manifest_path": str(self.delivery),
                                                          "manifest_sha256": executor.sha(self.delivery),
                                                          "opening_visual_family_report_path": str(self.opening),
                                                          "opening_visual_family_report_sha256": executor.sha(self.opening)})
        preflight = self.write("preflight.json", {})
        recheck = self.write("recheck.json", {})
        self.state = {"schema": "video-montage-state/v260928", "expected_indexes": [1],
                      "components": {}, "controller_invocations": [{}],
                      "semantic_prelock_recheck": self.ref(recheck),
                      "semantic_completion": self.ref(self.semantic),
                      "controller_preflight": self.ref(preflight),
                      "delivery_manifest": self.ref(self.delivery),
                      "controller_validation": {**self.ref(self.validation), "post_qc": str(self.post),
                                                "post_qc_sha256": executor.sha(self.post),
                                                "opening_family_report": str(self.opening),
                                                "opening_family_report_sha256": executor.sha(self.opening)}}

    def write(self, name, value):
        path = self.job / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    @staticmethod
    def ref(path):
        return {"path": str(path), "sha256": executor.sha(path)}

    def complete(self, mock_packaging_recheck=False):
        executor.atomic(self.job / "three_suite_ff_state.json", self.state)
        def fake_run(command, allowed={0}):
            target = Path(command[command.index("--report") + 1])
            executor.atomic(target, {"decision": "pass", "manifest_sha256": executor.sha(Path(command[command.index("--manifest") + 1]))})
        with patch.object(sys, "argv", ["three_suite_ff.py", "complete", "--job-dir", str(self.job)]), \
             patch.object(executor, "verify", return_value=executor.components(SUITE)):
            if mock_packaging_recheck:
                with patch.object(executor, "run", side_effect=fake_run):
                    executor.main()
            else:
                executor.main()
        return json.loads((self.job / "video_montage_completion.json").read_text(encoding="utf-8"))

    def test_clean_completion_keeps_original_path(self):
        receipt = self.complete()
        self.assertIn("controller_validation", receipt)
        self.assertNotIn("packaging_delivery", receipt)

    def test_packaging_completion_binds_review_and_output(self):
        packaged = self.job / "packaged.mp4"
        packaged.write_bytes(b"packaged")
        draft = self.write("draft.json", {})
        package = self.write("package.json", {"clean_delivery_sha256": executor.sha(self.delivery),
                                               "results": [{"output_path": str(packaged),
                                                            "output_sha256": executor.sha(packaged)}]})
        review = self.write("review.json", {})
        authority = self.write("authority.json", {})
        report = self.write("package_validation.json", {"decision": "pass",
                                                         "manifest_sha256": executor.sha(package),
                                                         "review_sha256": executor.sha(review),
                                                         "review_authority_sha256": executor.sha(authority)})
        self.state["packaging_draft"] = self.ref(draft)
        self.state["packaging_delivery"] = self.ref(package)
        self.state["packaging_validation"] = {**self.ref(report), "review_path": str(review),
                                                "review_sha256": executor.sha(review),
                                                "authority_path": str(authority),
                                                "authority_sha256": executor.sha(authority)}
        receipt = self.complete(mock_packaging_recheck=True)
        self.assertIn("packaging_validation", receipt)
        authority.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.complete(mock_packaging_recheck=True)


if __name__ == "__main__":
    unittest.main()
