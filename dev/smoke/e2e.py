"""Three-output integration check using local footage; outputs stay outside the repository.

Run with the managed Python: e2e.py [--job-dir existing/temp] [--prepare-only].
The generated annotations/plans are test fixtures, not editorial approval of the footage.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin/scripts"))
from montage import runtime
from montage.common import read, write, run, FFMPEG

SOURCES = ["吴樾原片/片场偷窥-A版-AC-0910.mp4", "吴樾原片/片场偷窥B版-AC-0910.mp4", "吴樾原片/茶室比熔炉-AC-0910.mp4",
           "吴樾原片/能打三十个_Aco_终版.mp4", "吴樾原片/办公室看号-AI重制-AC-0910.mp4",
           "林子聪原片/GOFCN55944_信息流达人_林子聪_混剪_短口播1_ge.mp4", "林子聪原片/GOFCN55945_信息流达人_林子聪_混剪_短口播2_ge.mp4",
           "许君聪原片/0924-成功学-无字幕.mp4", "许君聪原片/为所欲为.mp4", "许君聪原片/大哥来了.mp4"]
PEOPLE = [("person:wu-yue", "吴樾"), ("person:lin-zi-cong", "林子聪"), ("person:xu-jun-cong", "许君聪")]


def step(*args):
    result = subprocess.run([sys.executable, "-B", str(ROOT / "plugin/scripts/video_montage.py"), *map(str, args)],
                            capture_output=True, text=True, encoding="utf-8")
    print(result.stdout, flush=True)
    if result.returncode:
        print(result.stderr[-5000:], file=sys.stderr, flush=True)
        raise RuntimeError(f"{args[0]} failed ({result.returncode})")
    return result.stdout


def annotate(job):
    catalog = read(job / "catalog/catalog.json")
    annotations = {"sources": {}}
    for source in catalog["sources"]:
        person = next(pid for pid, name in PEOPLE if name in Path(source["path"]).parent.name)
        beats = []
        group = []
        for unit in source["units"]:
            group.append(unit)
            if unit["sentence_end"]:
                beats.append({"units": group[0]["id"] if len(group) == 1 else group[0]["id"] + "-" + group[-1]["id"],
                              "kind": "talk", "role": "body", "gist": "Integration fixture covering one complete source sentence"})
                group = []
        if group:
            beats.append({"units": group[0]["id"] if len(group) == 1 else group[0]["id"] + "-" + group[-1]["id"],
                          "kind": "talk", "role": "body", "gist": "Integration fixture covering the source ending sentence"})
        if beats:
            beats[-1]["role"] = "payoff"
        annotations["sources"][source["id"]] = {"people": [person], "speaker": person, "beats": beats}
    path = job / "config/smoke-annotations.json"
    write(path, annotations)
    step("annotate", "--job-dir", job, "--file", path)


def plan(job):
    from montage import plan as PLAN, rules, job as JOB
    catalog = read(job / "catalog/catalog.json")
    state = JOB.load(job)
    library = read(job / "config/text_library.json")
    games = [entry["text"] for entry in library["entries"] if entry["category"] == "game"]
    sources = {s["id"]: s for s in catalog["sources"]}
    registered = {p["person_id"] for p in state["celebrities"]}
    choices, endings = [], []
    for source in sources.values():
        for index in range(len(source["beats"])):
            for end in range(index + 1, min(index + 4, len(source["beats"])) + 1):
                beats = source["beats"][index:end]
                ids = [uid for beat in beats for uid in beat["units"]]
                units = [u for u in source["units"] if u["id"] in ids]
                if units and units[0]["in_frames"] and units[-1]["out_frames"] and 2 <= units[-1]["end"] - units[0]["start"] <= 9:
                    entry = {"source": source["id"], "units": units[0]["id"] if len(units) == 1 else units[0]["id"] + "-" + units[-1]["id"]}
                    (endings if beats[-1]["role"] == "payoff" else choices).append(entry)
    print(f"Integration candidates: {len(choices)} body clips, {len(endings)} endings", flush=True)
    if not choices or not endings:
        raise RuntimeError("No complete source spans available for integration fixtures")
    rng = random.Random(21)
    outputs = []
    for attempt in range(15000):
        clips = [*rng.sample(choices, 3), rng.choice(endings)]
        if len({c["source"] for c in clips}) < 3:
            continue
        transitions = []
        for left, right in zip(clips, clips[1:]):
            units = PLAN.unit_slice(sources[right["source"]], right["units"])
            text = "".join(u["text"] for u in units)
            relation = "claim_support"
            for connector, allowed in rules.CONNECTORS.items():
                if text.startswith(connector):
                    relation = sorted(allowed)[0]; break
            if rules.contains(text, rules.RESULT_ANCHORS):
                relation = "cause_effect"
            transitions.append({"relation": relation, "reason": "Integration fixture tests the join between distinct source sentences"})
        candidate = {"plan_id": f"P{len(outputs)+1:02d}", "clips": clips, "transitions": transitions}
        resolved, errors = PLAN.resolve_output(candidate, sources, registered, games, 3)
        if errors:
            continue
        from montage import batch
        previous = [PLAN.resolve_output(o, sources, registered, games, 3)[0] for o in outputs]
        if batch.audit([*previous, resolved], {})[0]:
            continue
        outputs.append(candidate)
        if len(outputs) == 3:
            path = job / "config/smoke-plan.json"
            write(path, {"outputs": outputs})
            print(f"Resolved 3 fixture plans in {attempt+1} attempts", flush=True)
            return path
    raise RuntimeError("Could not resolve integration fixtures; inspect the catalog and provide smoke-plan.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.job_dir:
        job = args.job_dir
    else:
        footage = ROOT / ".素材"
        output = Path(tempfile.mkdtemp(prefix="vm-e2e-"))
        order = {"requested_outputs": 3, "sources": [str(footage / source) for source in SOURCES],
                 "asset_root": str(footage / "包装素材"), "output_root": str(output),
                 "celebrities": [{"person_id": pid, "name": name,
                     "nameplate": str(footage / f"包装素材/明星铭牌/{name}人名条_固定左侧.png")} for pid, name in PEOPLE],
                 "request": "Integration test: three outputs from local footage; test fixtures only"}
        path = output / "order.json"
        write(path, order)
        text = step("init", "--work-order", path)
        job = Path(text.splitlines()[0].split(": ", 1)[1])
        print(f"JOB_DIR={job}", flush=True)
        write(ROOT / "dev/smoke/latest.json", {"job_dir": str(job)})
    if not (job / "catalog/catalog.json").is_file():
        step("prepare", "--job-dir", job)
    if args.prepare_only:
        return
    annotate(job)
    plan_path = plan(job)
    step("check", "--job-dir", job, "--plan", plan_path)
    step("render", "--job-dir", job, "--plan", plan_path)
    for frequency in (220, 330):
        tone = job / f"tone{frequency}.wav"
        run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=40", tone])
        step("fetch-bgm", "--job-dir", job, "--file", tone, "--license", "cc0", "--license-url",
             "https://creativecommons.org/publicdomain/zero/1.0/", "--license-evidence", "Self-generated integration test tone, dedicated CC0",
             "--source-page", "https://github.com/Finjix/video-montage", "--title", f"Test tone {frequency}", "--creator", "integration test", "--instrumental")
        tone.unlink()
    step("package", "--job-dir", job)
    delivery = read(job / "delivery.json")
    hashes = {r["path"]: runtime.checksum(Path(r["path"])) for r in delivery["outputs"]}
    step("package", "--job-dir", job)
    assert all(runtime.checksum(Path(path)) == sha for path, sha in hashes.items())
    assert len(delivery["outputs"]) == 3
    print(f"END_TO_END_PASS: {job}", flush=True)


if __name__ == "__main__":
    main()
