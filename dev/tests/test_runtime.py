import concurrent.futures
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from montage import runtime


class Downloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = b"verified-runtime-resource\n" * 100
        cls.hits = []
        owner = cls

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.hits.append((self.path, self.headers.get("Range")))
                if self.path == "/missing":
                    self.send_error(404); return
                body = b"x" * len(owner.body) if self.path == "/bad" else owner.body
                offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                self.send_response(206 if offset else 200)
                if offset:
                    self.send_header("Content-Range", f"bytes {offset}-{len(body)-1}/{len(body)}")
                self.send_header("Content-Length", str(len(body) - offset))
                self.end_headers()
                self.wfile.write(body[offset:])

            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-test-")
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"VIDEO_MONTAGE_CACHE_DIR": str(self.root), "VIDEO_MONTAGE_OFFLINE": "0"})
        self.env.start()
        self.path = self.root / "files/data.bin"
        self.spec = {"size": len(self.body), "sha256": hashlib.sha256(self.body).hexdigest(), "urls": [self.url + "/good"]}
        self.hits.clear()

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def test_verified_download_and_offline_reuse(self):
        runtime.fetch(self.path, self.spec)
        self.assertEqual(self.path.read_bytes(), self.body)
        with patch.dict(os.environ, {"VIDEO_MONTAGE_OFFLINE": "1"}), patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(runtime.fetch(self.path, self.spec), self.path)
        self.assertEqual(len(self.hits), 1)

    def test_resume_uses_range(self):
        self.path.parent.mkdir()
        self.path.with_name("data.bin.partial").write_bytes(self.body[:40])
        runtime.fetch(self.path, self.spec)
        self.assertEqual(self.hits[0][1], "bytes=40-")
        self.assertFalse(self.path.with_name("data.bin.partial").exists())

    def test_source_failure_falls_back(self):
        self.spec["urls"] = [self.url + "/missing", self.url + "/good"]
        runtime.fetch(self.path, self.spec)
        self.assertEqual(self.path.read_bytes(), self.body)
        self.assertEqual(self.hits[-1][0], "/good")

    def test_hash_mismatch_never_published(self):
        self.spec["urls"] = [self.url + "/bad"]
        with self.assertRaisesRegex(runtime.RuntimeFailure, "SHA-256 mismatch"):
            runtime.fetch(self.path, self.spec)
        self.assertFalse(self.path.exists())

    def test_corruption_after_receipt_is_repaired(self):
        runtime.fetch(self.path, self.spec)
        self.path.write_bytes(b"x" * len(self.body))
        runtime.fetch(self.path, self.spec)
        self.assertEqual(self.path.read_bytes(), self.body)
        self.assertEqual(len(self.hits), 2)

    def test_concurrent_callers_download_once(self):
        with concurrent.futures.ThreadPoolExecutor(4) as executor:
            results = list(executor.map(lambda _: runtime.fetch(self.path, self.spec), range(4)))
        self.assertEqual(len(self.hits), 1)
        self.assertEqual(results, [self.path] * 4)

    def test_offline_missing_is_actionable(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_OFFLINE": "1"}):
            with self.assertRaisesRegex(runtime.RuntimeFailure, "retry online"):
                runtime.fetch(self.path, self.spec)
        self.assertFalse(self.hits)

    def test_full_partial_is_verified_and_reused(self):
        self.path.parent.mkdir()
        self.path.with_name("data.bin.partial").write_bytes(self.body)
        runtime.fetch(self.path, self.spec)
        self.assertFalse(self.hits)

    def test_wrong_full_partial_is_restarted(self):
        self.path.parent.mkdir()
        self.path.with_name("data.bin.partial").write_bytes(b"x" * len(self.body))
        runtime.fetch(self.path, self.spec)
        self.assertEqual(self.path.read_bytes(), self.body)
        self.assertEqual(self.hits[0][1], None)

    def test_animation_checks_manifest_before_network(self):
        with self.assertRaisesRegex(runtime.RuntimeFailure, "Unregistered"):
            runtime.animation("../unknown.npz", "bad")
        self.assertFalse(self.hits)

    def test_status_has_no_filesystem_or_network_side_effects(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            info = runtime.status("base")
        self.assertEqual(info["missing"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_readonly_validation_does_not_create_receipt(self):
        self.path.parent.mkdir()
        self.path.write_bytes(self.body)
        self.assertTrue(runtime.valid(self.path, self.spec, remember=False))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_dependency_receipt_reuses_unicode_python_path(self):
        python = self.root / "中文 路径/python.exe"
        versions = runtime.FEATURES["media"]
        with patch.object(sys, "executable", str(python)), patch.object(sys, "prefix", str(python.parent)), \
                patch.object(runtime, "uv", return_value=self.root / "uv.exe"), \
                patch("subprocess.run", return_value=Mock(returncode=0)) as installer, \
                patch("importlib.metadata.version", side_effect=lambda name: versions[name]):
            runtime.ensure("media")
            runtime.ensure("media")
        self.assertEqual(installer.call_count, 1)


class Devices(unittest.TestCase):
    def test_cpu_without_nvidia(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "auto"}), patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(runtime.device(), "cpu")

    def test_cuda_driver_threshold(self):
        for version, expected in (("12.7", "cpu"), ("12.8", "cu128"), ("13.0", "cu128")):
            with self.subTest(version=version), patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "auto"}), patch("subprocess.run", return_value=Mock(returncode=0, stdout=f"CUDA Version: {version}")):
                self.assertEqual(runtime.device(), expected)

    def test_explicit_cpu_never_probes(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "cpu"}), patch("subprocess.run", side_effect=AssertionError):
            self.assertEqual(runtime.device(), "cpu")

    def test_new_nvidia_umd_output(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "auto"}), patch("subprocess.run", return_value=Mock(returncode=0, stdout="CUDA UMD Version: 13.4")):
            self.assertEqual(runtime.device(), "cu128")

    def test_timeout_falls_back(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "auto"}), patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 10)):
            self.assertEqual(runtime.device(), "cpu")

    def test_invalid_override(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_DEVICE": "typo"}):
            with self.assertRaises(runtime.RuntimeFailure):
                runtime.device()


class Boundaries(unittest.TestCase):
    def test_cache_defaults_to_plugin_directory(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_CACHE_DIR": ""}):
            self.assertEqual(runtime.cache(), (runtime.ROOT / "runtime-cache").resolve())

    def test_cache_can_be_overridden_inside_plugin(self):
        with patch.dict(os.environ, {"VIDEO_MONTAGE_CACHE_DIR": str(runtime.ROOT / "cache")}):
            self.assertEqual(runtime.cache(), (runtime.ROOT / "cache").resolve())

    def test_base_ensure_never_installs(self):
        with patch("subprocess.run", side_effect=AssertionError):
            runtime.ensure("base")

    def test_plan_and_cli_import_without_optional_modules(self):
        code = '''import sys
class Deny:
 def find_spec(self, fullname, *args):
  if fullname.split('.')[0] in ('numpy','PIL','cv2','torch','qwen_asr'):
   raise AssertionError(fullname)
sys.meta_path.insert(0,Deny())
from montage import plan, cli
assert plan.MIN_FRAMES == 1020
'''
        result = subprocess.run([sys.executable, "-B", "-c", code], env={**os.environ, "PYTHONPATH": str(runtime.ROOT / "scripts")}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_binary_import_does_not_download(self):
        with patch.object(runtime, "binary", side_effect=AssertionError):
            tool = runtime.Executable("ffmpeg")
            self.assertEqual(tool.name, "ffmpeg")

    def test_runtime_error_uses_exit_three(self):
        from montage import cli
        with patch.object(sys, "argv", ["video_montage.py", "prepare", "--job-dir", "unused"]), patch.object(runtime, "ensure", side_effect=runtime.RuntimeFailure("test failure")):
            with self.assertRaises(SystemExit) as outcome:
                cli.main()
        self.assertEqual(outcome.exception.code, 3)

    def test_manifest_and_upload_folder(self):
        import build_plugin
        metadata, files = build_plugin.validate()
        self.assertRegex(metadata["version"], r"^\d+\.\d+\.\d+$")
        self.assertLess(sum(p.stat().st_size for p in files), 10 * 1024**2)
