from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
EXECUTOR = ROOT / "components/executor/scripts/three_suite_ff.py"


def load_executor():
    spec = importlib.util.spec_from_file_location("video_montage_executor_test", EXECUTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_deployer():
    path = ROOT / "install.cmd"
    source = path.read_text(encoding="utf-8-sig").split("# BEGIN PYTHON DEPLOY\n", 1)[1]
    source = source.split("\ntry:\n    main()", 1)[0]
    module = types.ModuleType("video_montage_install_test")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


class ExecutorIntegrityTests(unittest.TestCase):
    def test_task_reference_rejects_changed_content(self):
        executor = load_executor()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            path.write_text("{}", encoding="utf-8")
            reference = {"path": str(path), "sha256": executor.sha(path)}
            self.assertEqual(path, executor.require_reference(reference, "evidence"))
            path.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                executor.require_reference(reference, "evidence")

    def test_runtime_verifier_failure_is_fatal(self):
        executor = load_executor()
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            (suite / "tools").mkdir()
            (suite / "tools/verify_runtime.py").write_text("", encoding="utf-8")
            with patch.object(executor, "run", return_value=types.SimpleNamespace(stdout='{"decision":"reject"}')):
                with self.assertRaisesRegex(RuntimeError, "runtime verification failed"):
                    executor.verify(suite)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.deployer = load_deployer()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "skill/video-montage").mkdir(parents=True)
        (self.source / "skill/video-montage/SKILL.md").write_text("new", encoding="utf-8")
        self.target = self.base / "video-montage"
        self.old = self.base / "CodexMontageFF/20.2.5"
        self.roots = (self.base / "codex/skills", self.base / "agents/skills")

    def test_first_install_and_reinstall_clear_target(self):
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.old, self.roots)
            self.assertTrue(all((root / "video-montage").is_junction() for root in self.roots))
            (self.target / "personal.txt").write_text("old", encoding="utf-8")
            self.deployer.install(self.source, self.target, self.old, self.roots)
        self.assertFalse((self.target / "personal.txt").exists())
        self.assertFalse((self.target / ".manifests").exists())
        self.assertFalse((self.target / "artifacts").exists())

    def test_legacy_links_removed_and_failure_restores_install(self):
        self.old.mkdir(parents=True)
        (self.old / "old.txt").write_text("previous", encoding="utf-8")
        for root in self.roots:
            root.mkdir(parents=True)
            self.deployer.create_junction(root / self.deployer.OLD_SKILLS[0], self.old / "components")
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.old, self.roots)
        self.assertFalse(self.old.exists())
        self.assertTrue(all(not (root / self.deployer.OLD_SKILLS[0]).exists() for root in self.roots))
        (self.target / "marker.txt").write_text("keep until success", encoding="utf-8")
        with patch.object(self.deployer, "run_checked"), patch.object(self.deployer, "create_junction", side_effect=OSError("injected failure")):
            with self.assertRaisesRegex(OSError, "injected failure"):
                self.deployer.install(self.source, self.target, self.old, self.roots)
        self.assertEqual("keep until success", (self.target / "marker.txt").read_text(encoding="utf-8"))
        self.assertTrue(all((root / "video-montage").is_junction() for root in self.roots))

    def test_preflight_does_not_create_install(self):
        deployer = self.deployer
        with patch.dict(os.environ, {"USERPROFILE": str(self.base)}):
            with patch.object(sys, "argv", [str(ROOT / "install.cmd"), str(ROOT / "install.cmd"), "-PreflightOnly"]):
                with patch.object(deployer, "run_checked"):
                    deployer.main()
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
