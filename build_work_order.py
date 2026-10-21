import json
import glob
import os

sources = sorted(glob.glob(".素材/*原片/*.mp4"))
sources = [os.path.abspath(s).replace("\\", "/") for s in sources]
asset_root = os.path.abspath(".素材/包装素材").replace("\\", "/")

celebs = [
    {"person_id": "person:wu-yue", "name": "吴樾",
     "nameplate": asset_root + "/明星铭牌/吴樾人名条_固定左侧.png"},
    {"person_id": "person:lin-zicong", "name": "林子聪",
     "nameplate": asset_root + "/明星铭牌/林子聪人名条_固定左侧.png"},
    {"person_id": "person:xu-juncong", "name": "许君聪",
     "nameplate": asset_root + "/明星铭牌/许君聪人名条_固定左侧.png"},
]

order = {
    "requested_outputs": 40,
    "sources": sources,
    "asset_root": asset_root,
    "celebrities": celebs,
    "request": "使用 素材 跑四十条成片",
}

with open("work_order.json", "w", encoding="utf-8") as f:
    json.dump(order, f, ensure_ascii=False, indent=2)

print(len(sources), "sources")
