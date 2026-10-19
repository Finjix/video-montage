"""Full-feature draft generation test (bridge-test): clips, transition, scene effect, flower text, text anim, BGM, filter."""
import os, sys, json, subprocess
sys.stdout.reconfigure(encoding="utf-8")
SKILL = r"D:\project\jy-lab\jianying-editor"
sys.path.insert(0, os.path.join(SKILL, "scripts"))
os.environ["JY_SKILL_ROOT"] = SKILL
from jy_wrapper import JyProject
import pyJianYingDraft as d
from pyJianYingDraft import TransitionType, VideoSceneEffectType, FilterType, TextIntro, trange, tim

ROOT = r"c:/Bytedance/JianyingPro/JianyingPro Drafts"
M = r"D:\project\jy-lab\media"
FF = r"D:\project\video-montage\skill\assets\dependencies\ffmpeg\bin\ffmpeg.exe"
bgm = os.path.join(M, "bgm.mp3")
if not os.path.exists(bgm):  # stand-in BGM (a real library track needs the cloud API)
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=12", "-c:a", "libmp3lame", bgm], check=True)

report = {}
def step(name, fn):
    try: fn(); report[name] = "ok"
    except Exception as e: report[name] = "FAIL %r" % e

p = JyProject("bridge-test", width=1280, height=720, drafts_root=ROOT, overwrite=True)
v1 = p.add_media_safe(os.path.join(M, "clip1.mp4"), "0s", "5s", "VideoTrack")
v2 = p.add_media_safe(os.path.join(M, "clip2.mp4"), "5s", "5s", "VideoTrack")
step("transition", lambda: v1.add_transition(TransitionType.叠化, duration="0.5s"))

def fx():
    p._ensure_track(d.TrackType.effect, "EffectTrack")
    p.script.add_segment(d.EffectSegment(VideoSceneEffectType.故障, trange("1s", "2s")), "EffectTrack")
step("scene_effect", fx)

def flt():
    p._ensure_track(d.TrackType.filter, "FilterTrack")
    p.script.add_segment(d.FilterSegment(FilterType.清晰明亮, trange("0s", "10s"), 0.8), "FilterTrack")
step("filter", flt)

def flower():
    seg = p.add_text_simple("花字测试", start_time="2s", duration="3s", track_name="Title", clip_settings=d.ClipSettings(transform_y=0.5))
    seg.add_effect("7351316503771368713")  # flower-text id shipped with the repo
    seg.add_animation(TextIntro.弹入, duration="0.5s")
step("flower_text+intro", flower)

step("subtitle", lambda: p.add_text_simple("bridge test 字幕", start_time="6s", duration="3s", track_name="Subtitles"))

def music():
    seg = p.add_audio_safe(bgm, start_time="0s", duration="10s", track_name="BGM")
    seg.volume = 0.5
step("bgm_local_file", music)

p.save()
print(json.dumps(report, ensure_ascii=False, indent=1))
