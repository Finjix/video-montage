from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
EXECUTOR = ROOT / "components/executor/scripts/three_suite_ff.py"
PACKAGE = ROOT / "tools/package_release.py"


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


def load_packager():
    spec = importlib.util.spec_from_file_location("video_montage_packager_test", PACKAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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

    def test_uninstall_removes_install_and_owned_links_only(self):
        (self.target / "skill/video-montage").mkdir(parents=True)
        (self.target / "components/executor/scripts").mkdir(parents=True)
        (self.target / "skill/video-montage/SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.target / "components/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        for root in self.roots:
            root.mkdir(parents=True)
            self.deployer.create_junction(root / "video-montage", self.target / "skill/video-montage")
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
        self.deployer.create_junction(self.roots[0] / "foreign", unrelated)
        self.deployer.create_junction(self.target / "external", unrelated)
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools/uninstall.ps1")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(self.target.exists())
        self.assertTrue(all(not (root / "video-montage").exists() for root in self.roots))
        self.assertTrue((self.roots[0] / "foreign").is_junction())
        self.assertEqual("keep", (unrelated / "keep.txt").read_text(encoding="utf-8"))

    def test_uninstall_refuses_unrelated_target(self):
        self.target.mkdir()
        (self.target / "personal.txt").write_text("keep", encoding="utf-8")
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools/uninstall.ps1")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertTrue((self.target / "personal.txt").exists())

    def test_uninstall_from_installed_cmd(self):
        (self.target / "tools").mkdir(parents=True)
        (self.target / "skill/video-montage").mkdir(parents=True)
        (self.target / "components/executor/scripts").mkdir(parents=True)
        (self.target / "skill/video-montage/SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.target / "components/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        (self.target / "uninstall.cmd").write_bytes((ROOT / "uninstall.cmd").read_bytes())
        (self.target / "tools/uninstall.ps1").write_bytes((ROOT / "tools/uninstall.ps1").read_bytes())
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(self.target / "uninstall.cmd")],
            input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(self.target.exists())

    def test_uninstall_preserves_same_name_foreign_skill(self):
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        root = self.roots[0]
        root.mkdir(parents=True)
        self.deployer.create_junction(root / "video-montage", unrelated)
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools/uninstall.ps1")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((root / "video-montage").is_junction())


class PackagingTests(unittest.TestCase):
    def test_clean_archive_and_independent_archive_name(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            for name in packager.FILES:
                (root / name).write_text(name, encoding="utf-8")
            for name in packager.DIRS:
                (root / name).mkdir()
            skill = root / "skill/video-montage"
            skill.mkdir()
            (skill / "SKILL.md").write_text('metadata:\n  version: "v260928"\n', encoding="utf-8")
            (root / "components/semantic/records/job.json").parent.mkdir(parents=True)
            (root / "components/semantic/records/job.json").write_text("task", encoding="utf-8")
            (root / "components/semantic/scripts/__pycache__/cached.pyc").parent.mkdir(parents=True)
            (root / "components/semantic/scripts/__pycache__/cached.pyc").write_text("cache", encoding="utf-8")
            (root / "components/semantic/scripts/run.py").write_text("runtime", encoding="utf-8")
            (root / "dependencies/python").mkdir()
            (root / "dependencies/python/python.exe").write_text("runtime", encoding="utf-8")
            (root / "artifacts").mkdir()
            (root / "artifacts/result.mp4").write_text("task", encoding="utf-8")
            archive_path, count = packager.build_archive(root, "v260929")
            self.assertEqual("video-montage-v260929.zip", archive_path.name)
            self.assertEqual(root / "release", archive_path.parent)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                self.assertEqual(count, len(names))
                self.assertIn("video-montage-v260928/install.cmd", names)
                self.assertIn("video-montage-v260928/dependencies/python/python.exe", names)
                self.assertIn("video-montage-v260928/components/semantic/scripts/run.py", names)
                self.assertFalse(any("records" in name or "__pycache__" in name or "artifacts" in name for name in names))
            repeated_path, repeated_count = packager.build_archive(root, "v260929")
            self.assertEqual(archive_path, repeated_path)
            self.assertEqual(count, repeated_count)
            with zipfile.ZipFile(repeated_path) as archive:
                self.assertFalse(any("/release/" in name for name in archive.namelist()))

    def test_archive_version_format(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            (root / "skill/video-montage").mkdir(parents=True)
            (root / "skill/video-montage/SKILL.md").write_text('  version: "v260928"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Archive version"):
                packager.build_archive(root, "../wrong")


class DocumentationTests(unittest.TestCase):
    def test_current_markdown_has_no_release_history_or_broken_links(self):
        legacy = re.compile(r"(?i)\bv(?:[6-9]|1[0-9]|20)(?:\.\d+)*\b|旧版|旧版本|旧安装|历史版本|兼容旧|迁移")
        links = re.compile(r"\]\(([^)]+\.md)\)")
        for path in ROOT.rglob("*.md"):
            if "dependencies" in path.parts or "release" in path.parts or path == ROOT / "docs/版本更新.md":
                continue
            self.assertNotRegex(path.as_posix(), legacy.pattern)
            content = path.read_text(encoding="utf-8-sig")
            self.assertNotRegex(content, legacy)
            for link in links.findall(content):
                self.assertTrue((path.parent / link).is_file(), f"Broken Markdown link in {path}: {link}")


if __name__ == "__main__":
    unittest.main()
