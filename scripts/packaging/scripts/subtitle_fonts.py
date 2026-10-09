"""Bundled subtitle fonts, with one random selection per packaging task."""
from __future__ import annotations

import secrets
from pathlib import Path

FONT_ROOT = Path(__file__).resolve().parents[3] / "assets/packaging/fonts"
# Two visual sizes calibrated against the supplied 1080x1920 speaking shots.
# At 1440x2560, W8's ordinary glyph height is about 100 px (75 at 1080).
# Keep the existing 8/9 config labels: ordinary / emphasis, about 13% larger.
EDITOR_PIXELS = {8: 108, 9: 122}
# Calibrated CapCut stroke 40 -> 10 reference pixels, at text scale 164%.
# Stroke is independent of selected font/size; flowers retain their own shader.
EDITOR_OUTLINE_WIDTH = 40
OUTLINE_PIXELS = round(EDITOR_OUTLINE_WIDTH / 4 * 1.64 * 1440 / 1920, 3)


def pixels(font: str, editor_size: int) -> int:
    if type(editor_size) is not int or editor_size not in EDITOR_PIXELS:
        raise ValueError("font_size must be 8 or 9")
    return round(EDITOR_PIXELS[editor_size] * FONTS[font]["size_scale"])
FONTS = {
    "w8": {"label": "文悦新青年体 W8", "path": str(FONT_ROOT / "WenYue-XinQingNianTi-W8.otf"),
           "sha256": "20b03dfe8dc982a19946726fe4acf156f9bb8b45adae8aac22a4a3590bb9a6bf",
           "family": "WenYue XinQingNianTi J W8", "postscript": "WenYue_XinQingNianTi_J-W8", "size_scale": 1.0},
    "smiley": {"label": "得意黑", "path": str(FONT_ROOT / "SmileySans-Oblique.otf"),
               "sha256": "139b5dcbe70d6d52de85a62d148784c6af4bd161e38fdb62ba0c1e7e5065fca6",
               "family": "Smiley Sans Oblique", "postscript": "SmileySans-Oblique", "size_scale": 0.93},
    "fangtang": {"label": "方糖体", "path": str(FONT_ROOT / "WenYue-FangTangTi-J-2.otf"),
                "sha256": "306bcecf10ffed57b823b8d04802f81ad746289661d00b14fab802e8f8625739",
                "family": "WenYue FangTangTi (Authorization Required) J", "postscript": "WenYue-FangTangTi-J", "size_scale": 0.93},
}


def by_hash(digest: str) -> tuple[str, dict]:
    for key, spec in FONTS.items():
        if spec["sha256"] == digest:
            return key, spec
    raise ValueError("subtitle font SHA-256 does not match any bundled font")


def select(value: str = "random") -> tuple[str, dict]:
    if value == "random":
        value = secrets.choice(tuple(FONTS))
    if not isinstance(value, str) or value not in FONTS:
        raise ValueError("subtitle_font must be random, w8, smiley or fangtang")
    return value, FONTS[value]


def require_selected(log: str, family: str, postscript: str) -> None:
    selections = [line for line in log.splitlines() if f"fontselect: ({family}," in line]
    if not selections or any(f"-> {postscript}," not in line for line in selections):
        raise RuntimeError(f"FFmpeg did not select the supplied subtitle font: {family}")
    if "Glyph 0x" in log and ("not found" in log or "failed to find" in log):
        raise RuntimeError(f"subtitle contains glyphs missing from the supplied font: {family}")
