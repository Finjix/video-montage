from __future__ import annotations

import hashlib, json, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
from v20_fail_closed import audit_final, interval_hits


class KnownRegressionTests(unittest.TestCase):
    def test_all_confirmed_bad_intervals_reject_even_when_renamed(self):
        registry=json.loads((ROOT/"references"/"wuzimu-v20-invalid-intervals.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(registry["entries"]),11)
        for index,entry in enumerate(registry["entries"]):
            candidate={"candidate_id":f"renamed_{index}","source_path":f"C:/new/{entry['source_name_contains']}_copy.mp4","source_sha256":"new-hash","source_in":entry["start"],"source_out":entry["end"],"source_content_fingerprint":"different-file-same-family"}
            self.assertTrue(interval_hits(candidate,entry),entry["entry_id"])

    def test_s08_clean_intermediate_keeps_original_bad_interval_identity(self):
        registry=json.loads((ROOT/"references"/"wuzimu-v20-invalid-intervals.json").read_text(encoding="utf-8"))
        entry=next(item for item in registry["entries"] if item["entry_id"]=="bad-s08-feature-zero-gap-bei-jiu")
        candidate={
            "candidate_id":"S08_FEATURE_AUDIO_CLEAN_V204",
            "source_path":"C:/task/clean_sources/S08_FEATURE_AUDIO_CLEAN_V204.nut",
            "source_sha256":"a678e1c438d424d19a289eefa1182456df27efb3e6fb9b10124ad98b3318cd10",
            "source_content_fingerprint":"v20.2.3:05b7d304042009af9af370cc568ea8e66cad02e70d752b94521af9b20884cbfa:[769, 1490]:selected:0-560:S08_FEATURE_AUDIO_CLEAN_V204",
            "source_in_frame":0,
            "source_out_frame_exclusive":560,
        }
        self.assertTrue(interval_hits(candidate,entry))

    def test_final_release_requires_bound_post_encode_qc(self):
        temp=Path(tempfile.mkdtemp()); manifest=temp/"delivery.json"; manifest.write_text(json.dumps({"output_count":1,"results":[{}]}),encoding="utf-8")
        post=temp/"post.json"; post.write_text(json.dumps({"schema":"ffmpeg-post-encode-qc/v260928","decision":"pass","delivery_manifest_sha256":"wrong"}),encoding="utf-8")
        report=audit_final(manifest,post); self.assertEqual("reject",report["decision"]); self.assertIn("V20_POST_ENCODE_BINDING_MISMATCH",[x["code"] for x in report["failures"]])


if __name__=="__main__": unittest.main()
