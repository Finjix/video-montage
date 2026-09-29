from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ffmpeg_controller", ROOT / "scripts" / "ffmpeg_controller.py")
controller = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(controller)


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ControllerOpeningGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.video = root / "P1.mp4"
        self.video.write_bytes(b"approved video")
        self.release = write(root / "release.json", {"schema": "semantic-delivery-manifest/v260928", "decision": "pass",
                                                     "package_version": "v260928", "authorized_outputs": ["P1"]})
        self.render = write(root / "render.json", {"segments": [{}]})
        self.manifest = write(root / "manifest.json", {"schema": "ffmpeg-controller-delivery/v260928", "output_count": 1,
                                                     "semantic_release_path": str(self.release), "semantic_release_sha256": controller.sha(self.release),
                                                     "video_spec": {"width": 1440, "height": 2560, "fps": "60/1", "codec": "h264"},
                                                     "audio_spec": {"codec": "aac", "sample_rate": 48000},
                                                     "results": [{"plan_id": "P1", "output_path": str(self.video),
                                                                  "output_sha256": controller.sha(self.video),
                                                                  "render_evidence_path": str(self.render),
                                                                  "render_evidence_sha256": controller.sha(self.render)}]})
        self.frame = root / "frame.jpg"
        self.frame.write_bytes(b"approved frame")
        self.pcm = root / "window.wav"
        self.pcm.write_bytes(b"audio")
        frame_rows = [{"output_frame_number": 0, "path": str(self.frame), "sha256": controller.sha(self.frame)}]
        cuts = []
        for index, kind in ((0, "output_start"), (1, "output_end")):
            cuts.append({"plan_id": "P1", "cut_index": index, "boundary_kind": kind,
                         "frames": frame_rows, "frame_set_sha256": controller.evidence_module.frame_set_sha(frame_rows),
                         "pcm_path": str(self.pcm), "pcm_sha256": controller.sha(self.pcm)})
        self.exact = write(root / "exact.json", {"schema": "ffmpeg-post-encode-evidence/v260928",
                                                 "delivery_manifest_sha256": controller.sha(self.manifest),
                                                 "boundary_count": 2, "cuts": cuts})
        self.machine = write(root / "machine.json", {})
        self.findings = write(root / "findings.json", {})
        self.authority = write(root / "authority.json", {})
        self.post = write(root / "post.json", {"schema": "ffmpeg-post-encode-qc/v260928", "decision": "pass",
                                               "delivery_manifest_sha256": controller.sha(self.manifest),
                                               "reviewer": {"role": "independent_post_encode_reviewer", "review_id": "r"},
                                               "machine_signal_gate": {"decision": "pass", "path": str(self.machine), "sha256": controller.sha(self.machine)},
                                               "independent_findings": {"path": str(self.findings), "sha256": controller.sha(self.findings)},
                                               "review_authority": {"path": str(self.authority), "sha256": controller.sha(self.authority)},
                                               "exact_cut_evidence": {"path": str(self.exact), "sha256": controller.sha(self.exact)}})
        opening_evidence = write(root / "opening_evidence.json", {"schema": "encoded-opening-frame-evidence/v260928",
                                                              "decision": "pass", "plan_id": "P1", "opening_visual_family_id": "family",
                                                              "output_sha256": controller.sha(self.video),
                                                              "reviewed_opening_frame_count": 60, "frames": frame_rows * 60,
                                                              "independent_visual_review": "pass", "celebrity_present_from_first_frame": True,
                                                              "first_frame_sha256": controller.sha(self.frame),
                                                              "opening_perceptual_signature": "signature"})
        request = write(root / "opening_request.json", {"schema": "opening-visual-family-release-request/v260928",
                                                       "delivery_manifest_path": str(self.manifest),
                                                       "delivery_manifest_sha256": controller.sha(self.manifest),
                                                       "max_opening_visual_family_uses": 1, "max_opening_second_visual_pair_uses": 1,
                                                       "items": [{"plan_id": "P1", "opening_visual_family_id": "family",
                                                                  "second_visual_family_id": "second",
                                                                  "opening_frame_evidence_path": str(opening_evidence),
                                                                  "opening_frame_evidence_sha256": controller.sha(opening_evidence)}]})
        self.opening = write(root / "opening.json", controller.opening_module.audit(request))
        self.report = root / "report.json"

    def validate(self):
        video_info = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1440,
                                   "height": 2560, "avg_frame_rate": "60/1"},
                                  {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}]}
        with patch.object(controller, "recheck_post_review", return_value=True), \
             patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             patch.object(controller, "probe", return_value=video_info):
            code = controller.validate(SimpleNamespace(manifest=self.manifest, semantic_release=self.release,
                                                       post_qc=self.post, opening_family_report=self.opening,
                                                       report=self.report))
        return code, json.loads(self.report.read_text(encoding="utf-8"))

    def test_passing_opening_family_gate_is_required(self):
        code, report = self.validate()
        self.assertEqual(0, code)
        self.assertEqual("pass", report["decision"])

    def test_final_validation_rechecks_extracted_frame(self):
        self.frame.write_bytes(b"tampered after review")
        code, report = self.validate()
        self.assertEqual(2, code)
        self.assertIn("exact_cut_files:P1:0", report["failures"])

    def test_rejected_opening_family_gate_blocks_release(self):
        value = json.loads(self.opening.read_text(encoding="utf-8"))
        value["decision"] = "reject"
        write(self.opening, value)
        code, report = self.validate()
        self.assertEqual(2, code)
        self.assertIn("opening_visual_family_gate", report["failures"])

    def test_forged_opening_report_is_rejected(self):
        value = json.loads(self.opening.read_text(encoding="utf-8"))
        value.pop("request_path")
        write(self.opening, value)
        code, report = self.validate()
        self.assertEqual(2, code)
        self.assertIn("opening_visual_family_gate", report["failures"])

    def test_placeholder_post_review_cannot_be_rechecked(self):
        self.assertFalse(controller.recheck_post_review(json.loads(self.post.read_text(encoding="utf-8")),
                                                        self.exact, self.machine, self.findings, self.authority))

    def test_wrong_encoded_spec_is_rejected(self):
        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        value["video_spec"]["width"] = 64
        write(self.manifest, value)
        code, report = self.validate()
        self.assertEqual(2, code)
        self.assertIn("output_spec:P1", report["failures"])


if __name__ == "__main__":
    unittest.main()
