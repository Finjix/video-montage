import sys; sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:/project/jy-lab/jianying-editor/scripts/vendor")
import pyJianYingDraft as d
def names(e, kws): return [n for n in e.__members__ if any(k in n for k in kws)][:10]
print("transitions", names(d.TransitionType, ["叠化","闪白","推近","拉远"]), len(d.TransitionType.__members__))
print("scene fx", names(d.VideoSceneEffectType, ["故障","闪","模糊","胶片","氛围"]), len(d.VideoSceneEffectType.__members__))
print("filters", names(d.FilterType, ["清晰","暖"]), len(d.FilterType.__members__))
print("text intro", names(d.TextIntro, ["打字","弹","渐显"]), len(d.TextIntro.__members__))
