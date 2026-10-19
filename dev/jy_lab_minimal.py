"""Minimal draft generation test against the installed JianYing (bridge-test)."""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8")
SKILL = r"D:\project\jy-lab\jianying-editor"
sys.path.insert(0, os.path.join(SKILL, "scripts"))
os.environ["JY_SKILL_ROOT"] = SKILL
from jy_wrapper import JyProject

ROOT = r"c:/Bytedance/JianyingPro/JianyingPro Drafts"
M = r"D:\project\jy-lab\media"
p = JyProject("bridge-test", width=1280, height=720, drafts_root=ROOT, overwrite=True)
v1 = p.add_media_safe(os.path.join(M, "clip1.mp4"), "0s", "5s", "VideoTrack")
v2 = p.add_media_safe(os.path.join(M, "clip2.mp4"), "5s", "5s", "VideoTrack")
p.add_text_simple("bridge test 字幕", start_time="1s", duration="3s", track_name="Subtitles")
try:
    p.add_transition_simple("叠化", video_segment=v1, duration="0.5s")
except Exception as e:
    print("transition failed:", repr(e))
p.save()
print(json.dumps({"saved": True}))
