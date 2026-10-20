"""Synthetic catalog jobs for plan, rules and packaging tests (no media needed)."""
import json
import tempfile
from pathlib import Path

LIBRARY = {"entries": [{"id": "game:wu-jin-dong-ri", "category": "game", "text": "无尽冬日"},
                       {"id": "person:wu-yue", "category": "person", "text": "吴樾"}]}
CELEBRITIES = [{"person_id": "person:wu-yue", "name": "吴樾", "nameplate": {"path": "wu.png", "sha256": "0" * 64}},
               {"person_id": "person:lin", "name": "林", "nameplate": {"path": "lin.png", "sha256": "1" * 64}}]


def unit(uid, text, start, end, fps=60.0, gap=0.3):
    """A unit whose clean cut windows span the pauses around it."""
    words = [{"word": ch, "start": round(start + (end - start) * i / len(text), 3),
              "end": round(start + (end - start) * (i + 1) / len(text), 3)} for i, ch in enumerate(text)]
    in_lo, in_hi = round((start - gap) * fps), round((start - 0.07) * fps)
    out_lo, out_hi = round((end + 0.08) * fps), round((end + gap) * fps)
    return {"id": uid, "text": text, "start": start, "end": end, "words": words, "protect": [start - 0.02, end + 0.03],
            "in_frames": [[max(0, in_lo), in_hi]] if in_hi >= max(0, in_lo) else [],
            "out_frames": [[out_lo, out_hi]], "sentence_end": True, "speaker": None, "excluded": False, "rejected": []}


def beat(bid, units, role="body", kind="talk", gist="a complete thought of the speaker"):
    return {"id": bid, "units": list(units), "kind": kind, "role": role, "gist": gist}


def source(sid, units, cuts=(), people=("person:wu-yue",), frames=6000, fps=(60, 1), beats=None):
    edges = [0, *cuts, frames]
    shots = [{"number": n, "numbers": [n], "start": a, "end": b, "people": list(people), "family": f"{sid}:shot{n}"}
             for n, (a, b) in enumerate(zip(edges, edges[1:]), 1)]
    return {"id": sid, "path": f"C:/fake/{sid}.mp4", "name": f"{sid}.mp4", "sha256": sid.lower() * 21 + "x",
            "fps_num": fps[0], "fps_den": fps[1], "frames": frames, "duration": frames * fps[1] / fps[0],
            "width": 1080, "height": 1920, "sheet": "", "cuts": list(cuts), "shots": shots, "units": units, "annotated": True,
            "beats": [beat(f"B{i:02d}", [u["id"]]) for i, u in enumerate(units, 1)] if beats is None else beats}


def job(sources, outputs=1, decisions=None):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    (root / "catalog").mkdir()
    (root / "config").mkdir()
    (root / "catalog/catalog.json").write_text(json.dumps({"sources": sources}, ensure_ascii=False), encoding="utf-8")
    (root / "config/text_library.json").write_text(json.dumps(LIBRARY, ensure_ascii=False), encoding="utf-8")
    state = {"celebrities": CELEBRITIES, "decisions": decisions or {}, "order": {"requested_outputs": outputs}}
    return directory, root, state


ROLES = {"S01": ["hook", "body", "body", "body"], "S02": ["body"] * 4, "S03": ["body", "payoff", "payoff", "cta"]}


def standard_sources():
    """Three sources, each with four 3-second sentences in their own camera shots; every unit is its own talk beat."""
    texts = {"S01": ["玩无尽冬日最重要的是什么？", "温度就是头等大事。", "开局一定要先砍树。", "砍树才能点燃熔炉。"],
             "S02": ["然后再收集幸存者。", "幸存者越来越多了。", "这游戏真好玩啊。", "大家快来玩吧朋友。"],
             "S03": ["无尽冬日真解压呀。", "中年人的解压神器。", "我每天都玩一会儿。", "一分钟就能上手了。"]}
    result = []
    for sid, lines in texts.items():
        units = [unit(f"U{i + 1:02d}", text, 1.0 + i * 4.0, 3.6 + i * 4.0) for i, text in enumerate(lines)]
        cuts = [round((0.6 + i * 4.0) * 60) for i in range(1, 4)]
        beats = [beat(f"B{i:02d}", [u["id"]], role) for i, (u, role) in enumerate(zip(units, ROLES[sid]), 1)]
        result.append(source(sid, units, cuts, beats=beats))
    return result


def transitions(count, relation="progression"):
    return [{"relation": relation, "reason": "the next clip continues with a concrete new step of the method"}
            for _ in range(count)]
