"""BGM origin rules: asset-pack audio, or a registered commercial-use download."""
import functools
import http.server
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from montage.common import FFMPEG, sha
from montage.media import bgm as BGM


def tone(path: Path, seconds: float, volume: float = 0.3) -> None:
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=220:duration={seconds}", "-af", f"volume={volume}", str(path)], check=True)


class BgmTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.job, self.pack, self.web = root / "job", root / "pack", root / "web"
        for folder in (self.job, self.pack, self.web):
            folder.mkdir()
        (self.pack / "copy.txt").write_text("文案", encoding="utf-8")
        tone(self.web / "good.wav", 25)
        tone(self.web / "short.wav", 5)
        tone(self.web / "quiet.wav", 25, 0.0001)
        (self.web / "page.html").write_text("<html>not audio</html>", encoding="utf-8")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(self.web))
        handler.log_message = lambda *args: None
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def fetch(self, name="good.wav", **override):
        arguments = dict(url=f"{self.base}/{name}", file=None, license="CC0 1.0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                         license_evidence="CC0 1.0 Universal Public Domain Dedication", source_page="https://example.org/track",
                         title="Calm", creator="Someone", instrumental=True)
        arguments.update(override)
        return BGM.acquire(self.job, self.pack, **arguments)

    def test_status_requires_download_without_pack_audio(self):
        self.assertTrue(BGM.status(self.job, self.pack)["download_required"])
        tone(self.pack / "bgm.mp3", 25)
        self.assertFalse(BGM.status(self.job, self.pack)["download_required"])

    def test_download_registers_hash_bound_track(self):
        entry = self.fetch()
        self.assertEqual(entry["license"], "cc0")
        self.assertTrue(Path(entry["path"]).is_file())
        self.assertEqual(sha(Path(entry["path"])), entry["sha256"])
        origin = BGM.require_authorized(self.job, self.pack, {"path": entry["path"], "sha256": entry["sha256"]})
        self.assertEqual(origin["origin"], "licensed_download")
        self.fetch()  # the same bytes register once
        self.assertEqual(len(BGM.load_registry(self.job)), 1)

    def test_local_file_registration(self):
        entry = self.fetch(url=None, file=self.web / "good.wav")
        self.assertIsNone(entry["download_url"])

    def test_rejected_inputs(self):
        for name, override in (("short.wav", {}), ("quiet.wav", {}), ("page.html", {}), ("missing.wav", {}),
                               ("good.wav", {"license": "CC BY-NC 4.0"}), ("good.wav", {"license": "CC BY 4.0"}),
                               ("good.wav", {"instrumental": False}), ("good.wav", {"license_evidence": ""}),
                               ("good.wav", {"source_page": "file:///x"}), ("good.wav", {"url": "ftp://x/y.wav"})):
            with self.subTest(name=name, override=override), self.assertRaises(Exception):
                self.fetch(name, **override)
        self.assertEqual(BGM.load_registry(self.job), [])
        self.assertEqual(list((self.job / "config" / "bgm").glob("*.partial")), [])

    def test_pack_audio_blocks_download_unless_unsuitable(self):
        tone(self.pack / "sfx.wav", 25)
        with self.assertRaises(ValueError):
            self.fetch()
        self.assertEqual(self.fetch(asset_audio_unsuitable="only sound effects")["asset_audio_unsuitable"], "only sound effects")

    def test_authorization(self):
        tone(self.pack / "bgm.wav", 25)
        packed = self.pack / "bgm.wav"
        self.assertEqual(BGM.require_authorized(self.job, self.pack, {"path": str(packed), "sha256": sha(packed)})["origin"], "asset_pack")
        stray = self.web / "good.wav"
        with self.assertRaises(ValueError):
            BGM.require_authorized(self.job, self.pack, {"path": str(stray), "sha256": sha(stray)})
        entry = self.fetch(asset_audio_unsuitable="demo")
        with self.assertRaises(ValueError):  # a registered hash must match its registered file
            BGM.require_authorized(self.job, self.pack, {"path": entry["path"], "sha256": "0" * 64})


if __name__ == "__main__":
    unittest.main()
