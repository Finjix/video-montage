"""Calibrated flower-text styles and the video encoding they require."""
from __future__ import annotations

from montage.common import ROOT, read, sha

ASSET_ROOT = ROOT / "assets/packaging/flowers"


def effect_styles() -> dict:
    manifest = read(ASSET_ROOT / "styles.json")
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
