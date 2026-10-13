from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
            (skill / "SKILL.md").write_text('---\nname: video-montage\ndescription: Test montage skill.\nmetadata:\n  version: "v260928"\n---\n', encoding="utf-8")
            (root / "scripts/semantic/records/job.json").parent.mkdir(parents=True)
            (root / "scripts/semantic/records/job.json").write_text("task", encoding="utf-8")
            (root / "scripts/semantic/scripts/__pycache__/cached.pyc").parent.mkdir(parents=True)
            (root / "scripts/semantic/scripts/__pycache__/cached.pyc").write_text("cache", encoding="utf-8")
            (root / "scripts/semantic/scripts/run.py").write_text("runtime", encoding="utf-8")
            (root / "assets/dependencies/python").mkdir(parents=True)
            (root / "assets/dependencies/python/python.exe").write_text("runtime", encoding="utf-8")
            (root / "artifacts").mkdir()
            (root / "artifacts/result.mp4").write_text("task", encoding="utf-8")
            excluded = ("README.md", "package.cmd", "docs/guide.md", "tools/build.py",
                        "scripts/autonomous/tests/test_plan.py", "references/tests/test_contract.py",
                        "work/output.mp4", "release/old.zip")
            for name in excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("development", encoding="utf-8")
            (root / "assets/dependencies/python/LICENSE.txt").write_text("license", encoding="utf-8")
            library_bytes = (ROOT / "assets/text-library.json").read_bytes()
            (root / "assets/text-library.json").write_bytes(library_bytes)
            library_guide = root / "references/autonomous/text-library.md"
            library_guide.parent.mkdir(parents=True)
            library_guide.write_bytes((ROOT / "references/autonomous/text-library.md").read_bytes())
            library_runtime = root / "scripts/packaging/scripts/text_library.py"
            library_runtime.parent.mkdir(parents=True)
            library_runtime.write_bytes((ROOT / "scripts/packaging/scripts/text_library.py").read_bytes())
            archive_path, count = packager.build_archive(root, "v260929")
            self.assertEqual("video-montage-v260929.zip", archive_path.name)
            self.assertEqual(root / "release", archive_path.parent)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                self.assertEqual(count, len(names))
                self.assertIn("video-montage/SKILL.md", names)
                self.assertEqual({"video-montage"}, {name.split("/")[0] for name in names})
                self.assertEqual(1, sum(Path(name).name.casefold() == "skill.md" for name in names))
                self.assertFalse(any(Path(name).suffix == ".cmd" for name in names))
                self.assertIn("video-montage/assets/dependencies/python/python.exe", names)
                self.assertIn("video-montage/scripts/semantic/scripts/run.py", names)
                self.assertIn("video-montage/assets/dependencies/python/LICENSE.txt", names)
                self.assertEqual(library_bytes, archive.read("video-montage/assets/text-library.json"))
                self.assertIn("video-montage/references/autonomous/text-library.md", names)
                self.assertIn("video-montage/scripts/packaging/scripts/text_library.py", names)
                for name in excluded:
                    self.assertNotIn(f"video-montage/{name}", names)
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
            (root / "SKILL.md").write_text('---\nname: video-montage\ndescription: Test montage skill.\nmetadata:\n  version: "v260928"\n---\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Archive version"):
                packager.build_archive(root, "../wrong")

    def test_invalid_skill_metadata_rejected(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for frontmatter in (
                    'name: video-montage\n',
                    '---\nname: other-skill\ndescription: Test skill.\nmetadata:\n  version: "v260928"\n---\n',
                    '---\nname: video-montage\ndescription: ""\nmetadata:\n  version: "v260928"\n---\n'):
                with self.subTest(frontmatter=frontmatter):
                    (root / "SKILL.md").write_text(frontmatter, encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        packager.build_archive(root)
                    self.assertFalse((root / "release").exists())

    def test_nested_skill_manifest_rejected(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text("manifest", encoding="utf-8")
            for name in packager.DIRS:
                (root / name).mkdir()
            (root / "references/skill.md").write_text("nested", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "exactly one SKILL.md"):
                packager.package_files(root)


class DocumentationTests(unittest.TestCase):
    def test_current_markdown_has_no_broken_links(self):
        links = re.compile(r"\]\(([^)]+\.md)\)")
        for path in ROOT.rglob("*.md"):
            if {"dependencies", "release", "test", "work", ".runtime", ".git"} & set(path.parts):
                continue
            content = path.read_text(encoding="utf-8-sig")
            for link in links.findall(content):
                self.assertTrue((path.parent / link).is_file(), f"Broken Markdown link in {path}: {link}")


class InstalledPackagingTests(unittest.TestCase):
    def test_production_effects_work_without_development_tools(self):
        packager = load_packager()
        with tempfile.TemporaryDirectory(prefix="installed-packaging-") as temporary:
            stage = Path(temporary) / "skill"
            for source in packager.package_files(ROOT):
                relative = source.relative_to(ROOT)
                if any(relative.is_relative_to(prefix) for prefix in
                       ("scripts/packaging", "assets/packaging", "assets/dependencies/ffmpeg/bin")):
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            self.assertFalse((stage / "tools").exists())
            self.assertFalse((stage / "scripts/packaging/tests").exists())
            # Run in a fresh interpreter so no module can resolve back to the repo.
            script = '''
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'scripts/packaging/scripts'))
import packaging_design as d, design_renderer as r, subtitle_fonts as f
from reference_animation import Animation
results=[]
for effect in ('bounce_up','shout_wave','ice_drift'):
    frame=Animation(effect,'冰雪世界').canonical_frame(10)
    assert frame.shape == (3414,1920,3) and frame.max() > 0
    results.append(effect)
for font in f.FONTS:
    image=r.text_sprite('来无尽冬日吧',f.FONTS[font]['path'],120,spans=[{'start':1,'end':5,'flower':'ice2'}])
    assert image.getbbox() and image.mode == 'RGBA'
assert d.TEMPLATES == ()
for effect in d.ENTRANCES:
    image=r.text_sprite('冰雪世界',f.FONTS['w8']['path'],120)
    event={'sprite':image,'entrance':{'effect':effect},'entrance_frames':18,'start_frame':0,
           'base_x':300,'base_y':1400,'cx':720,'cy':1766}
    assert r.paint_event(event,10)[0].mode == 'RGBA'
assert d.ROOT == root and all(Path(s['path']).is_relative_to(root) for s in f.FONTS.values())
print(json.dumps({'calibrated':results,'fonts':list(f.FONTS),'entrances':list(d.ENTRANCES)}))
'''
            process = subprocess.run([str(ROOT / "assets/dependencies/python/python.exe"), "-B", "-X", "utf8", "-c", script, str(stage)],
                                     capture_output=True, text=True, encoding="utf-8", cwd=stage)
            self.assertEqual(0, process.returncode, process.stderr[-3000:])
            self.assertEqual(3, len(json.loads(process.stdout)["calibrated"]))


if __name__ == "__main__":
    unittest.main()
