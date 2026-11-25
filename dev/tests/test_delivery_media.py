"""Decode a three-part delivery and check its picture, original sound and boundaries."""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

if not importlib.util.find_spec("numpy") or not importlib.util.find_spec("PIL"):
    raise unittest.SkipTest("requires the managed media environment")

import numpy as np
from montage import runtime
from montage.delivery import assemble


class DeliveryMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The base regression suite can run offline without downloading the optional media tools.
        folder = runtime.cache() / "tools" / ("ffmpeg-" + runtime.manifest()["ffmpeg"]["version"])
        if not all((folder / (name + ".exe")).is_file() for name in ("ffmpeg", "ffprobe")):
            raise unittest.SkipTest("requires the cached media runtime")
        cls.ffmpeg = runtime.binary("ffmpeg")
        cls.ffprobe = runtime.binary("ffprobe")

    def source(self, path, color, size, fps, length, *, tone=None, gain=0, delay=0):
        command = [self.ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                   f"color={color}:s={size}:r={fps}:d={length}"]
        if tone:
            command += ["-itsoffset", str(delay), "-f", "lavfi", "-i",
                        f"sine=f={tone}:r=48000:d={length - delay}", "-af", f"volume={gain}dB",
                        "-c:a", "pcm_s16le"]
        subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)

    def measure(self, path, start, end):
        # An independent meter over each section of the encoded file, including the B lead-in.
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", path,
                                 "-vn", "-af", f"atrim=start={start}:end={end},ebur128=peak=true",
                                 "-f", "null", os.devnull], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=True)
        summary = result.stderr.rsplit("Summary:", 1)[1]
        return (float(re.search(r"I:\s*(-?[\d.]+) LUFS", summary)[1]),
                float(re.search(r"Peak:\s*(-?[\d.]+) dBFS", summary)[1]))

    def frame_centers(self, path):
        raw = subprocess.check_output([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", path, "-an",
                                      "-vf", "crop=2:2", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        return np.frombuffer(raw, np.uint8).reshape(-1, 4, 3).mean(axis=1)

    def pcm(self, path):
        raw = subprocess.check_output([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", path, "-vn",
                                      "-ac", "1", "-ar", "48000", "-f", "f32le", "-"])
        return np.frombuffer(raw, dtype="<f4")

    def test_mixed_clocks_delayed_b_audio_and_silent_card(self):
        with tempfile.TemporaryDirectory(prefix="video-montage-delivery-") as temp:
            a, b, card, out = (Path(temp) / name for name in ("口播.mov", "玩法.mov", "尾板.mov", "成片.mp4"))
            self.source(a, "red", "160x96", 24, 2, tone=440, gain=-10)
            self.source(b, "lime", "96x160", 30, 1.5, tone=880, gain=9, delay=0.125)
            self.source(card, "blue", "160x96", 25, 1)
            assemble(a, b, card, out, target_lufs=-12.0)

            info = subprocess.check_output([self.ffprobe, "-v", "error", "-show_streams", "-of", "json", out])
            streams = {s["codec_type"]: s for s in json.loads(info)["streams"]}
            video, audio = streams["video"], streams["audio"]
            self.assertEqual((video["width"], video["height"]), (160, 96))
            fps = Fraction(video["avg_frame_rate"])
            self.assertEqual(Fraction(video["start_time"]), 0)
            self.assertEqual(Fraction(audio["start_time"]), 0)
            self.assertEqual(Fraction(audio["time_base"]) * audio["duration_ts"], Fraction(9, 2))

            frames = self.frame_centers(out)
            # Reading every frame catches one-frame flashes or an off-by-one at either join.
            expected = np.repeat([0, 1, 2], [int(2 * fps), int(Fraction(3, 2) * fps), int(fps)])
            np.testing.assert_array_equal(frames.argmax(axis=1), expected)
            self.assertGreater(frames.max(axis=1).min(), 200)

            for start, end in ((0, 2), (2, 3.5)):
                loudness, peak = self.measure(out, start, end)
                self.assertAlmostEqual(loudness, -12, delta=1)
                self.assertLessEqual(peak, -1)

            rate = 48000
            signal = self.pcm(out)
            for start, tone in ((0.25, 440), (2.25, 880)):
                window = signal[int(start * rate):int((start + 0.5) * rate)]
                self.assertEqual(np.abs(np.fft.rfft(window)).argmax() * 2, tone)
            for start, end in ((2.025, 2.100), (3.525, 4.48)):
                self.assertLess(np.abs(signal[round(start * rate):round(end * rate)]).max(), 0.001)

    def test_audible_card_and_custom_target(self):
        with tempfile.TemporaryDirectory(prefix="video-montage-delivery-") as temp:
            a, b, card, out = (Path(temp) / name for name in ("a.mov", "b.mov", "card.mov", "out.mp4"))
            for path, gain in ((a, -4), (b, 8), (card, -14)):
                self.source(path, "white", "96x160", 30, 1, tone=1000, gain=gain)
            assemble(a, b, card, out, target_lufs=-16.0)
            for start, end in ((0, 1), (1, 2), (2, 3), (0, 3)):
                loudness, peak = self.measure(out, start, end)
                self.assertAlmostEqual(loudness, -16, delta=1)
                self.assertLessEqual(peak, -1)

    def test_old_ad_keeps_only_the_selected_native_b_frames_and_audio(self):
        with tempfile.TemporaryDirectory(prefix="video-montage-delivery-") as temp:
            root = Path(temp)
            a, b, card, out = (root / name for name in ("a.mov", "old-ad.mov", "card.mov", "out.mp4"))
            self.source(a, "red", "96x160", 30, 1, tone=400)
            self.source(card, "blue", "96x160", 30, 0.5, tone=1600)
            shots = [("white", 100), ("lime", 800), ("cyan", 1200), ("yellow", 3000)]
            for i, (color, tone) in enumerate(shots):
                self.source(root / f"{i}.mov", color, "96x160", 24, 0.5, tone=tone)
            concat_list = root / "shots.txt"
            concat_list.write_text("".join(f"file '{i}.mov'\n" for i in range(4)), encoding="ascii")
            subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-f", "concat", "-i", concat_list,
                            "-c", "copy", b], check=True)
            assemble(a, b, card, out, b_range=(12, 36))

            rgb = np.array([[1, 0, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1]], dtype=bool)
            expected = np.repeat(rgb, [60, 30, 30, 30], axis=0)
            np.testing.assert_array_equal(self.frame_centers(out) > 128, expected)
            signal = self.pcm(out)
            # 20 ms of tone close to both edges of each shot detects even one stray 24 fps source frame.
            for start, end, tone in ((0, 1, 400), (1, 1.5, 800), (1.5, 2, 1200), (2, 2.5, 1600)):
                for pos in (start + 0.01, end - 0.03):
                    first = round(pos * 48000)
                    window = signal[first:first + 960]
                    self.assertEqual(np.abs(np.fft.rfft(window)).argmax() * 50, tone)
