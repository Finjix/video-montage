"""Calibrated flower-text styles and the video encoding they require."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets/packaging/flowers"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def effect_styles() -> dict:
    manifest = json.loads((ASSET_ROOT / "styles.json").read_text(encoding="utf-8"))
    for spec in manifest["styles"].values():
        path = ASSET_ROOT / spec["path"]
        if sha(path) != spec["sha256"]:
            raise ValueError(f"flower effect changed: {path}")
        spec["path"] = str(path.resolve())
    return manifest


def video_encoding(styles: list[str]) -> dict:
    # Fire's saturated, thin red strokes lose color in 4:2:0; ice styles keep 4:2:0.
    return {"pixel_format": "yuv444p", "crf": 8, "overlay_format": "rgb"} if "fire1" in styles else {
        "pixel_format": "yuv420p", "crf": 18, "overlay_format": "auto"}
