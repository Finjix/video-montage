"""End-to-end smoke run on the local footage in .素材 (3 packaged videos) with timings.

    skill/assets/dependencies/python/python.exe dev/smoke/e2e.py [output_root]

Uses dev/smoke/annotations.json and plan.json; two generated test tones stand in for licensed BGM.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skill"
PYTHON = SKILL / "assets/dependencies/python/python.exe"
FFMPEG = SKILL / "assets/dependencies/ffmpeg/bin/ffmpeg.exe"
FOOTAGE = ROOT / ".素材"
HERE = Path(__file__).resolve().parent
SOURCES = ["吴樾原片/片场偷窥-A版-AC-0910.mp4", "吴樾原片/片场偷窥B版-AC-0910.mp4", "吴樾原片/茶室比熔炉-AC-0910.mp4",
           "吴樾原片/能打三十个_Aco_终版.mp4", "吴樾原片/办公室看号-AI重制-AC-0910.mp4",
           "林子聪原片/GOFCN55944_信息流达人_林子聪_混剪_短口播1_ge.mp4", "林子聪原片/GOFCN55945_信息流达人_林子聪_混剪_短口播2_ge.mp4",
           "许君聪原片/0924-成功学-无字幕.mp4", "许君聪原片/为所欲为.mp4", "许君聪原片/大哥来了.mp4"]
PEOPLE = [("person:wu-yue", "吴樾"), ("person:lin-zi-cong", "林子聪"), ("person:xu-jun-cong", "许君聪")]


def step(name, *args, stdin=None):
    started = time.monotonic()
    result = subprocess.run([str(PYTHON), "-I", str(SKILL / "scripts/video_montage.py"), *args], input=stdin,
                            capture_output=True, text=True, encoding="utf-8")
    print(f"--- {name}: exit {result.returncode}, {time.monotonic() - started:.1f} s")
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr[-3000:])
        raise SystemExit(f"{name} failed")
    return result.stdout


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    output_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="vm-smoke-"))
    order = {"requested_outputs": 3, "sources": [str(FOOTAGE / s) for s in SOURCES], "asset_root": str(FOOTAGE / "包装素材"),
             "celebrities": [{"person_id": pid, "name": name, "nameplate": str(FOOTAGE / f"包装素材/明星铭牌/{name}人名条_固定左侧.png")}
                             for pid, name in PEOPLE],
             "request": "smoke test: 3 packaged videos", "output_root": str(output_root)}
    total = time.monotonic()
    job = step("init", "init", "--work-order", "-", stdin=json.dumps(order, ensure_ascii=False)).splitlines()[0].split(": ", 1)[1]
    step("prepare", "prepare", "--job-dir", job)
    step("annotate", "annotate", "--job-dir", job, "--file", str(HERE / "annotations.json"))
    step("check", "check", "--job-dir", job, "--plan", str(HERE / "plan.json"))
    step("render", "render", "--job-dir", job, "--plan", str(HERE / "plan.json"))
    for frequency in (220, 330):
        tone = Path(job) / f"tone{frequency}.mp3"
        subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=40",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency * 2}:duration=40", "-filter_complex",
                        "[0][1]amix=inputs=2,volume=0.5,apulsator=hz=2", str(tone)], check=True)
        step(f"fetch-bgm {frequency}", "fetch-bgm", "--job-dir", job, "--file", str(tone), "--license", "cc0",
             "--license-url", "https://creativecommons.org/publicdomain/zero/1.0/", "--license-evidence",
             "Self-generated test tone, dedicated CC0", "--source-page", "https://example.org/test-tone",
             "--title", f"Test tone {frequency}", "--creator", "smoke test", "--instrumental")
        tone.unlink()
    step("package", "package", "--job-dir", job)
    step("package again (cached)", "package", "--job-dir", job)
    print(f"=== total {time.monotonic() - total:.1f} s; delivery: {Path(job).parent}")


if __name__ == "__main__":
    main()
