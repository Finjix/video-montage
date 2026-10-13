from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_balance as balance
import portable_frame_renderer as renderer
from v20_frame_plan_gate import validate_plan_value

FFMPEG = renderer.runtime_binary("ffmpeg")


class BalancePolicyTests(unittest.TestCase):
    def test_common_target_respects_peaks_and_boost_limit(self):
        target, rows = balance.choose_gains([
            {"integrated_lufs": -15, "true_peak_db": -1},
            {"integrated_lufs": -36, "true_peak_db": -20},
            {"integrated_lufs": None, "true_peak_db": None},
            {"integrated_lufs": -58, "true_peak_db": -52},
        ], balance.configuration())
        self.assertEqual(-24, target)
        self.assertEqual([-9, 12, 0, 0], [r["gain_db"] for r in rows])
        self.assertEqual(["balanced", "balanced", "silence", "below_signal_floor"], [r["status"] for r in rows])

    def test_short_clip_is_not_boosted_but_still_respects_peak_ceiling(self):
        target, rows = balance.choose_gains([{"integrated_lufs": None, "true_peak_db": -0.5}], balance.configuration())
        self.assertIsNone(target)
        self.assertEqual(-2.5, rows[0]["gain_db"])

    def test_invalid_settings_are_rejected_by_plan_gate(self):
        segment = {"source_path": "video.mp4", "source_in_frame": 0, "speech_end_frame": 59,
                   "source_out_frame_exclusive": 60, "source_fps_num": 60, "source_fps_den": 1}
        for config in ({"enabled": "false"}, {"target_lufs": float("nan")}, {"true_peak_db": 0},
                       {"max_boost_db": 100}, {"typo": -18}, False):
            with self.subTest(config=config):
                self.assertTrue(validate_plan_value({"segments": [segment], "audio_balance": config}))
        inherited = balance.for_output({"audio_balance": {"target_lufs": -22}}, {"audio_balance": {"enabled": False}})
        self.assertFalse(inherited["enabled"])
        self.assertEqual(-22, inherited["target_lufs"])

    def test_measurement_failure_and_missing_stats_fail_closed(self):
        for result in (subprocess.CompletedProcess([], 1, "", "decode failed"),
                       subprocess.CompletedProcess([], 0, "", "no stats")):
            with patch.object(balance.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "audio balance measurement"):
                    balance.measure(FFMPEG, Path("source.mp4"), "anull")

    def test_post_encode_check_rejects_bad_peak_and_loudness(self):
        config = balance.configuration()
        self.assertEqual("reject", balance.verify({"integrated_lufs": -18, "true_peak_db": -1}, -18, config)["decision"])
        self.assertEqual("reject", balance.verify({"integrated_lufs": -22, "true_peak_db": -5}, -18, config)["decision"])


class BalanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, name, amplitude, *, stereo=False, duration=3):
        path = self.root / f"{name}.mkv"
        expression = f"{amplitude}*sin(2*PI*440*t)"
        if stereo:
            expression += "|" + expression
        subprocess.run([str(FFMPEG), "-v", "error", "-f", "lavfi", "-i",
            f"color=c=blue:s=160x96:r=60:d={duration}", "-f", "lavfi", "-i",
            f"aevalsrc={expression}:s=48000:d={duration}", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "pcm_f32le", "-shortest", "-y", str(path)], check=True, capture_output=True)
        return {"source_path": str(path), "source_sha256": renderer.sha256(path), "source_in_frame": 0,
                "speech_end_frame": round(duration * 60) - 1, "source_out_frame_exclusive": round(duration * 60),
                "source_fps_num": 60, "source_fps_den": 1}

    def render(self, segments, name="out", config=None):
        plan = self.root / f"{name}.json"
        renderer.atomic_json(plan, {"segments": segments, **({"audio_balance": config} if config is not None else {})})
        output = self.root / f"{name}.mp4"
        evidence = renderer.render(plan, output, self.root / f"{name}.evidence.json", 160, 96, 60, "libx264")
        return output, evidence

    def test_actual_encoded_segments_are_equal_with_mixed_channel_layouts_and_speed(self):
        loud = self.source("loud", 0.25)
        quiet = self.source("quiet", 0.04, stereo=True)
        quiet["speed"] = 1.2
        _, evidence = self.render([loud, quiet])
        rows = [s["audio_balance"] for s in evidence["segments"]]
        before = abs(rows[0]["integrated_lufs"] - rows[1]["integrated_lufs"])
        after = abs(rows[0]["output_verification"]["integrated_lufs"] - rows[1]["output_verification"]["integrated_lufs"])
        self.assertGreater(before, 10)
        self.assertLess(after, 0.4)
        self.assertTrue(evidence["checks"]["audio_balance"])
        self.assertEqual(330, evidence["actual_output_frames"])
        self.assertTrue(all(r["output_verification"]["true_peak_db"] <= -2.5 for r in rows))

    def test_manual_attenuation_is_applied_after_balancing_and_can_disable(self):
        loud = self.source("loud", 0.25)
        quiet = self.source("quiet", 0.05)
        quiet["audio_gain_db"] = -3
        _, evidence = self.render([loud, quiet])
        rows = [s["audio_balance"]["output_verification"] for s in evidence["segments"]]
        self.assertAlmostEqual(3, rows[0]["integrated_lufs"] - rows[1]["integrated_lufs"], delta=0.4)
        output, disabled = self.render([loud, quiet], "disabled", {"enabled": False})
        self.assertFalse(disabled["audio_balance"]["enabled"])
        self.assertNotIn("audio_balance", disabled["segments"][0])
        measured = [balance.measure(FFMPEG, output, f"atrim=start={a}:end={b},asetpts=PTS-STARTPTS")
                    for a, b in ((0, 3), (3, 6))]
        self.assertGreater(measured[0]["integrated_lufs"] - measured[1]["integrated_lufs"], 16)

    def test_transient_peak_lowers_common_target_instead_of_clipping(self):
        transient = self.source("transient", "(0.02+0.85*between(t\\,1\\,1.002))")
        normal = self.source("normal", 0.15)
        _, evidence = self.render([transient, normal])
        target = evidence["audio_balance"]["common_target_lufs"]
        self.assertLess(target, -18)
        rows = [s["audio_balance"] for s in evidence["segments"]]
        self.assertLessEqual(rows[0]["true_peak_db"] + rows[0]["gain_db"], -3.0 + 1e-6)
        self.assertTrue(evidence["checks"]["audio_balance"])
        self.assertLess(abs(rows[0]["output_verification"]["integrated_lufs"]
                            - rows[1]["output_verification"]["integrated_lufs"]), 0.8)

    def test_silence_noise_floor_and_short_clip_do_not_receive_positive_gain(self):
        for name, amplitude, duration in (("silence", 0, 1), ("noise_floor", 0.0001, 1), ("short", 0.1, 0.2)):
            with self.subTest(name=name):
                output, evidence = self.render([self.source(name, amplitude, duration=duration)], name + "-out")
                self.assertLessEqual(evidence["segments"][0]["audio_balance"]["gain_db"], 0)
                self.assertTrue(evidence["checks"]["audio_balance"])
                self.assertEqual(round(duration * 60), evidence["actual_output_frames"])
                json.dumps(evidence, allow_nan=False)

    def test_failed_post_encode_check_preserves_previous_delivery(self):
        segment = self.source("source", 0.1)
        output, _ = self.render([segment])
        previous = renderer.sha256(output)
        with patch.object(balance, "verify", return_value={"decision": "reject"}):
            with self.assertRaisesRegex(RuntimeError, "render validation failed"):
                renderer.render(self.root / "out.json", output, self.root / "out.evidence.json",
                                160, 96, 60, "libx264", overwrite=True)
        self.assertEqual(previous, renderer.sha256(output))
        self.assertFalse(output.with_suffix(".partial.mp4").exists())


if __name__ == "__main__":
    unittest.main()
