"""Validate a bundled Codex skill entrypoint without host tooling."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml


def validate(root: Path) -> None:
    path = root / "SKILL.md"
    content = path.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", content, re.DOTALL)
    if match is None:
        raise ValueError(f"Missing YAML frontmatter: {path}")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid frontmatter: {path}")
    if set(metadata) - {"name", "description", "license", "allowed-tools", "metadata"}:
        raise ValueError(f"Unexpected frontmatter keys: {path}")
    name, description = metadata.get("name"), metadata.get("description")
    if name != root.name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name or "") or len(name) > 64:
        raise ValueError(f"Invalid skill name: {path}")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(f"Invalid skill description: {path}")
    if "[TODO:" in content:
        raise ValueError(f"Unfinished skill instructions: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_root", type=Path)
    args = parser.parse_args()
    validate(args.skill_root)
    print(f"Valid skill: {args.skill_root.name}")
