from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ffmpeg_controller", ROOT / "scripts" / "ffmpeg_controller.py")
controller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(controller)


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ControllerOpeningGateTests(unittest.TestCase):
    def test_final_validation_rechecks_extracted_frame(self):
        root = Path(tempfile.mkdtemp())
        manifest = write(root / "manifest.json", {"output_count": 0, "results": []})
        release = write(root / "release.json", {"decision": "pass", "authorized_outputs": []})
        frame = root / "frame.jpg"
        frame.write_bytes(b"approved")
        pcm = root / "window.wav"
        pcm.write_bytes(b"audio")
        frames = [{"output_frame_number": 0, "path": str(frame), "sha256": controller.sha(frame)}]
        cut = {"plan_id": "P1", "cut_index": 0, "frames": frames, "frame_set_sha256": controller.evidence_module.frame_set_sha(frames), "pcm_path": str(pcm), "pcm_sha256": controller.sha(pcm)}
        exact = write(root / "exact.json", {"cuts": [cut]})
        machine = write(root / "machine.json", {})
        findings = write(root / "findings.json", {})
        post = write(root / "post.json", {"schema": "ffmpeg-post-encode-qc/v260928", "decision": "pass", "delivery_manifest_sha256": controller.sha(manifest), "machine_signal_gate": {"decision": "pass", "path": str(machine), "sha256": controller.sha(machine)}, "independent_findings": {"path": str(findings), "sha256": controller.sha(findings)}, "exact_cut_evidence": {"path": str(exact), "sha256": controller.sha(exact)}})
        opening = write(root / "opening.json", {"schema": "opening-visual-family-release-gate/v260928", "decision": "pass", "delivery_manifest_sha256": controller.sha(manifest)})
        frame.write_bytes(b"tampered after review")
        report = root / "report.json"
        code = controller.validate(SimpleNamespace(manifest=manifest, semantic_release=release, post_qc=post, opening_family_report=opening, report=report))
        self.assertEqual(2, code)
        self.assertIn("exact_cut_files:P1:0", json.loads(report.read_text(encoding="utf-8"))["failures"])

    def run_case(self, opening_decision: str):
        root = Path(tempfile.mkdtemp())
        manifest = write(root / "manifest.json", {"output_count": 0, "results": []})
        release = write(root / "release.json", {"decision": "pass", "authorized_outputs": []})
        machine_file = write(root / "machine.json", {"x": 1})
        findings_file = write(root / "findings.json", {"x": 1})
        exact_file = write(root / "exact.json", {"x": 1})
        post = write(root / "post.json", {
            "schema": "ffmpeg-post-encode-qc/v260928", "decision": "pass", "delivery_manifest_sha256": controller.sha(manifest),
            "machine_signal_gate": {"decision": "pass", "path": str(machine_file), "sha256": controller.sha(machine_file)},
            "independent_findings": {"path": str(findings_file), "sha256": controller.sha(findings_file)},
            "exact_cut_evidence": {"path": str(exact_file), "sha256": controller.sha(exact_file)},
        })
        opening = write(root / "opening.json", {"schema": "opening-visual-family-release-gate/v260928", "decision": opening_decision, "delivery_manifest_sha256": controller.sha(manifest)})
        report = root / "report.json"
        code = controller.validate(SimpleNamespace(manifest=manifest, semantic_release=release, post_qc=post, opening_family_report=opening, report=report))
        return code, json.loads(report.read_text(encoding="utf-8"))

    def test_passing_opening_family_gate_is_required(self):
        code, report = self.run_case("pass")
        self.assertEqual(0, code)
        self.assertEqual("pass", report["decision"])

    def test_rejected_opening_family_gate_blocks_release(self):
        code, report = self.run_case("reject")
        self.assertEqual(2, code)
        self.assertIn("opening_visual_family_gate", report["failures"])


if __name__ == "__main__":
    unittest.main()
