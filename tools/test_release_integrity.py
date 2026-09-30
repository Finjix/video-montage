from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
EXECUTOR = ROOT / "scripts/executor/scripts/three_suite_ff.py"
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
            (suite / "scripts").mkdir()
            (suite / "scripts/verify_runtime.py").write_text("", encoding="utf-8")
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
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.source / "scripts/executor/scripts").mkdir(parents=True)
        (self.source / "scripts/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        self.target = self.base / "codex/skills/video-montage"
        self.legacy = self.base / "video-montage"
        self.agent_link = self.base / ".agents/skills/video-montage"

    def test_first_install_and_reinstall_replace_skill_directory(self):
        (self.source / "release").mkdir()
        (self.source / "release/large.zip").write_bytes(b"archive")
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
            self.assertTrue((self.target / "SKILL.md").is_file())
            self.assertFalse(self.target.is_junction())
            (self.target / "personal.txt").write_text("old", encoding="utf-8")
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        self.assertFalse((self.target / "personal.txt").exists())
        self.assertFalse((self.target / "release").exists())
        self.assertFalse(self.legacy.exists())
        self.assertFalse(self.agent_link.exists())

    def test_installed_skill_keeps_batch_rule_link_readable(self):
        guide = self.source / "references/autonomous/batch-diversity.md"
        guide.parent.mkdir(parents=True)
        guide.write_text("Batch diversity rules", encoding="utf-8")
        (self.source / "SKILL.md").write_text(
            "name: video-montage\n[batch rules](references/autonomous/batch-diversity.md)\n",
            encoding="utf-8")
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        skill = (self.target / "SKILL.md").read_text(encoding="utf-8")
        target = re.search(r"\]\(([^)]+)\)", skill).group(1)
        self.assertTrue((self.target / target).is_file())

    def test_install_excludes_development_files_and_keeps_runtime_resources(self):
        excluded = ("docs/guide.md", ".git/config", ".gitignore", ".gitattributes",
                    "README.md", "install.cmd", "uninstall.cmd", "package.cmd",
                    "tools/package_release.py", "tools/validate_release.py", "tools/uninstall.ps1",
                    "tools/test_release_integrity.py", "scripts/autonomous/tests/test_plan.py",
                    "scripts/semantic/records/job.json", "work/output.mp4", ".runtime/jobs/state.json")
        required = ("assets/dependencies/python/python.exe", "scripts/verify_runtime.py", "scripts/validate_skill.py",
                    "scripts/autonomous/scripts/autonomous_montage.py", "references/autonomous/workflow.md",
                    "assets/packaging/fonts/style.otf", "references/semantic/semantic-contract.md",
                    "references/workflows/autonomous-workflow.md", "agents/openai.yaml")
        for name in (*excluded, *required):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        with patch.object(self.deployer, "run_checked") as checked:
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        for name in excluded:
            self.assertFalse((self.target / name).exists(), name)
        for name in required:
            self.assertTrue((self.target / name).is_file(), name)
        self.assertEqual({"SKILL.md", "agents", "scripts", "references", "assets"},
                         {path.name for path in self.target.iterdir()})
        self.assertEqual([self.target / "SKILL.md"], list(self.target.rglob("SKILL.md")))
        self.assertEqual((self.source / "SKILL.md").read_bytes(),
                         (self.target / "SKILL.md").read_bytes())
        self.assertTrue((self.target / "agents/openai.yaml").is_file())
        commands = [call.args[1] for call in checked.call_args_list]
        self.assertEqual(str(self.source / "tools/validate_release.py"), commands[0][-1])
        self.assertTrue(any(command[-1].endswith("scripts\\verify_runtime.py") for command in commands[1:]))

    def test_migrates_legacy_install_and_links(self):
        (self.legacy / "skill/video-montage").mkdir(parents=True)
        (self.legacy / "skill/video-montage/SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.legacy / "components/executor/scripts").mkdir(parents=True)
        (self.legacy / "components/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        self.target.parent.mkdir(parents=True)
        self.deployer.create_junction(self.target, self.legacy / "skill/video-montage")
        self.agent_link.parent.mkdir(parents=True)
        self.deployer.create_junction(self.agent_link, self.legacy / "skill/video-montage")
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertFalse(self.target.is_junction())
        self.assertFalse(self.legacy.exists())
        self.assertFalse(self.agent_link.exists())

    def test_failure_restores_previous_skill(self):
        with patch.object(self.deployer, "run_checked"):
            self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        (self.target / "marker.txt").write_text("keep", encoding="utf-8")
        self.agent_link.parent.mkdir(parents=True)
        self.deployer.create_junction(self.agent_link, self.target)
        original_rename = Path.rename
        def fail_agent_rename(path, destination):
            if path == self.agent_link:
                raise OSError("injected failure")
            return original_rename(path, destination)
        with patch.object(self.deployer, "run_checked"), patch.object(Path, "rename", fail_agent_rename):
            with self.assertRaisesRegex(OSError, "injected failure"):
                self.deployer.install(self.source, self.target, self.legacy, self.agent_link)
        self.assertEqual("keep", (self.target / "marker.txt").read_text(encoding="utf-8"))
        self.assertTrue(self.agent_link.is_junction())

    def test_preflight_does_not_create_install(self):
        with patch.dict(os.environ, {"USERPROFILE": str(self.base), "CODEX_HOME": str(self.base / "codex")}):
            with patch.object(sys, "argv", [str(ROOT / "install.cmd"), str(ROOT / "install.cmd"), "-PreflightOnly"]):
                with patch.object(self.deployer, "run_checked"):
                    self.deployer.main()
        self.assertFalse(self.target.exists())

    def test_install_cmd_shows_preflight_completion_prompt(self):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "install.cmd"), "-PreflightOnly"],
            input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("[安装中] 正在检查运行环境", result.stdout)
        self.assertIn("Preflight completed successfully.", result.stdout)
        self.assertIn("Press any key to close this window.", result.stdout)

    def test_uninstall_removes_skill_directory(self):
        (self.target / "scripts/executor/scripts").mkdir(parents=True)
        (self.target / "SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.target / "scripts/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "uninstall.cmd"), "-NoPause"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(self.target.exists())

    def test_uninstall_refuses_unrelated_skill(self):
        self.target.mkdir(parents=True)
        (self.target / "personal.txt").write_text("keep", encoding="utf-8")
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "uninstall.cmd"), "-NoPause"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(1, result.returncode)
        self.assertTrue((self.target / "personal.txt").exists())

    def test_uninstall_cmd_shows_completion_prompt(self):
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "uninstall.cmd")],
            input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Uninstall completed.", result.stdout)
        self.assertIn("Press any key to close this window.", result.stdout)

    def test_uninstall_from_installed_cmd(self):
        (self.target / "tools").mkdir(parents=True)
        (self.target / "scripts/executor/scripts").mkdir(parents=True)
        (self.target / "SKILL.md").write_text("name: video-montage\n", encoding="utf-8")
        (self.target / "scripts/executor/scripts/three_suite_ff.py").write_text("", encoding="utf-8")
        (self.target / "uninstall.cmd").write_bytes((ROOT / "uninstall.cmd").read_bytes())
        (self.target / "tools/uninstall.ps1").write_bytes((ROOT / "tools/uninstall.ps1").read_bytes())
        env = os.environ.copy()
        env["USERPROFILE"] = str(self.base)
        env["CODEX_HOME"] = str(self.base / "codex")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(self.target / "uninstall.cmd")],
            input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30,
        )
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("Press any key to start uninstall.", result.stdout)
        deadline = time.monotonic() + 10
        while self.target.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(self.target.exists())


class PackagingTests(unittest.TestCase):
    def test_package_cmd_reaches_version_validation(self):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "package.cmd"), "invalid"],
            input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("Archive version must look like", result.stderr)
        self.assertIn("Packaging failed with exit code 1.", result.stdout)
        self.assertIn("Press any key to close this window.", result.stdout)

    def test_package_cmd_no_pause(self):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "package.cmd"), "invalid", "-NoPause"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(1, result.returncode)
        self.assertNotIn("Press any key", result.stdout)

    def test_clean_archive_and_independent_archive_name(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            for name in packager.FILES:
                (root / name).write_text(name, encoding="utf-8")
            for name in packager.DIRS:
                (root / name).mkdir()
            skill = root
            (skill / "SKILL.md").write_text('metadata:\n  version: "v260928"\n', encoding="utf-8")
            (root / "scripts/semantic/records/job.json").parent.mkdir(parents=True)
            (root / "scripts/semantic/records/job.json").write_text("task", encoding="utf-8")
            (root / "scripts/semantic/scripts/__pycache__/cached.pyc").parent.mkdir(parents=True)
            (root / "scripts/semantic/scripts/__pycache__/cached.pyc").write_text("cache", encoding="utf-8")
            (root / "scripts/semantic/scripts/run.py").write_text("runtime", encoding="utf-8")
            (root / "assets/dependencies/python").mkdir(parents=True)
            (root / "assets/dependencies/python/python.exe").write_text("runtime", encoding="utf-8")
            (root / "artifacts").mkdir()
            (root / "artifacts/result.mp4").write_text("task", encoding="utf-8")
            archive_path, count = packager.build_archive(root, "v260929")
            self.assertEqual("video-montage-v260929.zip", archive_path.name)
            self.assertEqual(root / "release", archive_path.parent)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                self.assertEqual(count, len(names))
                self.assertIn("video-montage-v260928/install.cmd", names)
                self.assertIn("video-montage-v260928/assets/dependencies/python/python.exe", names)
                self.assertIn("video-montage-v260928/scripts/semantic/scripts/run.py", names)
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
            root.mkdir(parents=True)
            (root / "SKILL.md").write_text('  version: "v260928"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Archive version"):
                packager.build_archive(root, "../wrong")


class DocumentationTests(unittest.TestCase):
    def test_current_markdown_has_no_release_history_or_broken_links(self):
        legacy = re.compile(r"(?i)\bv(?:[6-9]|1[0-9]|20)(?:\.\d+)*\b|旧版|旧版本|旧安装|历史版本|兼容旧|迁移")
        links = re.compile(r"\]\(([^)]+\.md)\)")
        for path in ROOT.rglob("*.md"):
            if {"dependencies", "release", "test"} & set(path.parts) or path == ROOT / "docs/版本更新.md":
                continue
            self.assertNotRegex(path.as_posix(), legacy.pattern)
            content = path.read_text(encoding="utf-8-sig")
            self.assertNotRegex(content, legacy)
            for link in links.findall(content):
                self.assertTrue((path.parent / link).is_file(), f"Broken Markdown link in {path}: {link}")


if __name__ == "__main__":
    unittest.main()
