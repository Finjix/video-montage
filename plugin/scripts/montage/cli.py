"""video-montage command line: init -> prepare -> annotate -> check/render -> package."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from montage import job as JOB
from montage import plan as PLAN
from montage.common import read, write
from montage import runtime, text_library

RULE_FAILURE = 2


def show(lines) -> None:
    print("\n".join(lines) if isinstance(lines, list) else lines, flush=True)


def errors_lines(errors: dict[str, list[str]]) -> list[str]:
    return [f"  {scope}: {problem}" for scope, problems in errors.items() for problem in problems]


def library_terms(job: Path) -> tuple[list[str], tuple[str, ...]]:
    library = text_library.load(job)
    games = [e["text"] for e in library["entries"] if e["category"] == "game"]
    return games, tuple(e["text"] for e in library["entries"])


def cmd_init(args) -> int:
    order = json.loads(sys.stdin.buffer.read().decode("utf-8-sig")) if str(args.work_order) == "-" else read(args.work_order)
    job, state = JOB.init(order, args.job_dir)
    count = state["order"]["requested_outputs"]
    assets = JOB.asset_inventory(Path(state["order"]["asset_root"]))
    need = -(-count // 8)
    lines = [f"job_dir: {job}", f"delivery: {state['delivery_dir']}",
             f"sources: {len(state['sources'])}, outputs requested: {count}",
             f"asset pack: {len(assets['text_pins'])} text pins, {len(assets['disclaimers'])} disclaimer PNGs, "
             f"{len(assets['audio'])} audio files (BGM needs {need} tracks; download any shortfall with fetch-bgm)"]
    if not assets["text_pins"]:
        lines.append("warning: no text pin PNGs found (folder 文字钉/pin); ask the user now or set defaults.text_pins later")
    if len(assets["disclaimers"]) != 1:
        lines.append("warning: expected exactly one disclaimer PNG (folder 免责/disclaimer); ask the user now or set "
                     "defaults.disclaimer later")
    show(lines + ["next: prepare"])
    return 0


def cmd_prepare(args) -> int:
    runtime.ensure("media")
    from montage import catalog as CATALOG
    job = args.job_dir.resolve()
    state = JOB.load(job)
    value = CATALOG.prepare(job, state, fresh=args.fresh)
    units = sum(len(s["units"]) for s in value["sources"])
    shots = sum(len(s["shots"]) for s in value["sources"])
    annotated = (job / "config/annotations.json").is_file()
    show([f"catalog: {job / 'catalog/catalog.md'} ({len(value['sources'])} sources, {units} units, {shots} shots)",
          f"sheets: {job / 'catalog'} (S01.jpg... per source, assets.jpg for the overlay PNGs)",
          "next: " + ("existing annotations were re-applied; routes" if annotated
                      else "read the catalog and sheets, then annotate --file <annotations.json>")])
    return 0


def cmd_annotate(args) -> int:
    runtime.ensure("media")
    from montage import catalog as CATALOG
    job = args.job_dir.resolve()
    state = JOB.load(job)
    value = CATALOG.check_annotations(read(args.file), state)
    write(job / "config/annotations.json", value)
    catalog = CATALOG.build(job, state)
    missing = [s["id"] for s in catalog["sources"] if not s["annotated"]]
    unbeaten = [s["id"] for s in catalog["sources"] if not s["beats"]]
    edited = sum(1 for s in catalog["sources"] for u in s["units"] if u.get("edited"))
    beats = sum(len(s["beats"]) for s in catalog["sources"])
    show([f"catalog updated: {job / 'catalog/catalog.md'} ({edited} corrected units, {beats} beats)"]
         + ([f"not annotated yet: {', '.join(missing)}"] if missing else [])
         + ([f"no beats yet (unusable in plans): {', '.join(unbeaten)}"] if unbeaten else [])
         + ["next: routes (clips and joins that pass the machine checks)"])
    return 0


def resolve(job: Path, state: dict, plan_path: Path) -> tuple[dict, list[dict], dict, dict]:
    plan = read(plan_path)
    outputs, errors, summary = PLAN.resolve(plan, job, state)
    report = {"outputs": outputs, "errors": errors, "batch": summary}
    write(job / "reports/check.json", report)
    scripts = [line for output in outputs for line in (*PLAN.script(output), "")]
    (job / "reports/scripts.md").write_text("\n".join(scripts), encoding="utf-8")
    return plan, outputs, errors, summary


def summary_lines(outputs: list[dict], errors: dict, summary: dict) -> list[str]:
    lines = [PLAN.describe(output) for output in outputs]
    if summary:
        lines.append(f"batch: {summary['openings']} openings, {summary['opening_cameras']} opening cameras, "
                     f"{summary['closings']} closings, most-used unit x{summary['most_used_unit']}, at most "
                     f"{summary['most_shared_positions']:.0%} of positions shared by two videos, one piece of material at one "
                     f"position in at most {summary['most_used_at_one_position']} videos")
    if errors:
        lines.append(f"{sum(len(v) for v in errors.values())} problems:")
        lines += errors_lines(errors)
    return lines


def cmd_check(args) -> int:
    job = args.job_dir.resolve()
    state = JOB.load(job)
    _, outputs, errors, summary = resolve(job, state, args.plan)
    show(summary_lines(outputs, errors, summary)
         + [f"scripts (read every video as a viewer hears it): {job / 'reports/scripts.md'}"]
         + ([] if errors else ["plan ok; next: render --plan <plan.json>"]))
    return RULE_FAILURE if errors else 0


def cmd_routes(args) -> int:
    from montage import routes as ROUTES
    job = args.job_dir.resolve()
    state = JOB.load(job)
    show(ROUTES.run(job, state, joins=args.joins, mix=args.mix, max_clips=args.max_clips, seconds=args.seconds,
                    fresh=args.fresh))
    return 0


def cmd_render(args) -> int:
    runtime.ensure("media")
    from montage import render as RENDER
    job = args.job_dir.resolve()
    state = JOB.load(job)
    plan, outputs, errors, summary = resolve(job, state, args.plan)
    if errors:
        show(summary_lines(outputs, errors, summary) + ["nothing rendered; fix the plan"])
        return RULE_FAILURE
    write(job / "config/plan.json", plan)
    ids = {output["plan_id"] for output in outputs}
    for path in (job / "clean").glob("*.*"):
        if path.stem not in ids:
            path.unlink()
    games, terms = library_terms(job)
    results = RENDER.render_all(job, outputs, games, terms)
    write(job / "reports/render.json", results)
    problems = {row["plan_id"]: row["errors"] for row in results if row["errors"]}
    notes = {row["plan_id"]: row["notes"] for row in results if row["notes"]}
    lines = [f"{row['plan_id']}: {'reused' if row['reused'] else 'rendered'}, {row['qc']['lufs']} LUFS, sheet {row['sheet']}"
             for row in results]
    if notes:
        lines += ["notes:"] + errors_lines(notes)
    lines.append(f"subtitles: {job / 'subtitles'}")
    if problems:
        lines += ["problems:"] + errors_lines(problems)
    else:
        lines.append("next: look at every cut sheet and subtitle file, then package [--config <packaging.json>]")
    show(lines)
    return RULE_FAILURE if problems else 0


def cmd_package(args) -> int:
    job = args.job_dir.resolve()
    state = JOB.load(job)
    if args.clean:
        from montage.delivery import deliver_clean
        published = deliver_clean(job, state)
        show(f"published unpackaged: {', '.join(published)} -> {state['delivery_dir']}")
        return 0
    runtime.ensure("packaging")
    from montage import package as PACKAGE
    stored = job / "config/packaging.json"
    if args.config:
        config = read(args.config)
        write(stored, config)
    else:
        config = read(stored) if stored.is_file() else {}
    result = PACKAGE.package(job, state, config)
    result.setdefault("complete", False)
    write(job / "reports/package.json", result)
    lines = [f"published: {', '.join(result['published']) or 'none'} -> {state['delivery_dir']}"]
    if result["warnings"]:
        lines += ["notes:"] + errors_lines(result["warnings"])
    if result["errors"]:
        lines += ["problems:"] + errors_lines(result["errors"])
    elif result.get("complete"):
        sheets = job / "sheets"
        lines.append(f"delivery complete ({len(result['published'])} videos); packaged sheets in {sheets}")
    show(lines)
    return RULE_FAILURE if result["errors"] else 0


def cmd_status(args) -> int:
    job = args.job_dir.resolve()
    state = JOB.load(job)
    count = state["order"]["requested_outputs"]
    delivered = read(job / "delivery.json")["outputs"] if (job / "delivery.json").is_file() else []
    catalog = read(job / "catalog/catalog.json") if (job / "catalog/catalog.json").is_file() else None
    beats = sum(len(s["beats"]) for s in catalog["sources"]) if catalog else 0
    plan = read(job / "config/plan.json")["outputs"] if (job / "config/plan.json").is_file() else []
    clean = len(list((job / "clean").glob("*.mp4")))
    packaged = read(job / "reports/package.json") if (job / "reports/package.json").is_file() else {}
    if not catalog:
        step = "prepare"
    elif not beats:
        step = "annotate --file <annotations.json>"
    elif len(plan) != count:
        step = "routes, then check --plan <plan.json>"
    elif clean < len(plan):
        step = "render --plan <plan.json>"
    elif len(delivered) < count or not packaged.get("complete", True):
        step = "package (the last run left problems: reports/package.json)" if packaged.get("errors") else "package"
    else:
        step = "done (revisions: change the plan or subtitles, then render / package)"
    show([f"delivery: {state['delivery_dir']}",
          f"catalog: {'yes' if catalog else 'no'}, beats: {beats}, plan outputs: {len(plan)}, clean videos: {clean}, "
          f"delivered: {len(delivered)}/{count}",
          f"decisions: {json.dumps(state.get('decisions', {}), ensure_ascii=False)}", f"next: {step}"])
    return 0


def cmd_decide(args) -> int:
    value = json.loads(args.value)
    decisions = JOB.decide(args.job_dir.resolve(), args.key, value, args.instruction)
    show(f"decisions: {json.dumps(decisions, ensure_ascii=False)}")
    return 0


def cmd_bgm_assets(args) -> int:
    runtime.ensure("media")
    from montage import bgm as BGM
    job = args.job_dir.resolve()
    state = JOB.load(job)
    status = BGM.status(job, Path(state["order"]["asset_root"]), state["order"]["requested_outputs"])
    print(json.dumps(status, ensure_ascii=False, indent=1))
    return 0


def cmd_fetch_bgm(args) -> int:
    runtime.ensure("media")
    from montage import bgm as BGM
    job = args.job_dir.resolve()
    state = JOB.load(job)
    entry = BGM.acquire(job, Path(state["order"]["asset_root"]), outputs=state["order"]["requested_outputs"],
                        url=args.url, file=args.file, license=args.license, license_url=args.license_url,
                        license_evidence=args.license_evidence, source_page=args.source_page, title=args.title,
                        creator=args.creator, instrumental=args.instrumental, asset_audio_unsuitable=args.asset_audio_unsuitable)
    show(f"registered BGM: {entry['path']} ({entry['duration_seconds']} s, {entry['license']})")
    return 0


def cmd_runtime(args) -> int:
    if args.install:
        runtime.ensure(args.feature)
        if args.feature in ("media", "packaging", "asr"):
            runtime.binary("ffmpeg")
        if args.feature == "packaging":
            runtime.prefetch_animations(("bounce_up", "ice_drift", "shout_wave"))
        if args.feature == "asr":
            runtime.models()
    from verify_runtime import verify
    report = verify(args.feature)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["decision"] == "pass" else 3


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, handler, job=True):
        action = sub.add_parser(name)
        action.set_defaults(handler=handler)
        if job:
            action.add_argument("--job-dir", type=Path, required=True)
        return action

    command("init", cmd_init, job=False).add_argument("--work-order", type=Path, required=True, help="JSON file, or - for stdin")
    sub.choices["init"].add_argument("--job-dir", type=Path)
    command("prepare", cmd_prepare).add_argument("--fresh", action="store_true", help="recognize speech again (unit IDs may change)")
    command("annotate", cmd_annotate).add_argument("--file", type=Path, required=True)
    command("check", cmd_check).add_argument("--plan", type=Path, required=True)
    routes = command("routes", cmd_routes)
    routes.add_argument("--joins", type=Path, help="accepted joins; builds config/plan-draft.json for the whole batch")
    routes.add_argument("--mix", action="store_true", help="also route across celebrities")
    routes.add_argument("--max-clips", type=int, help="optional cap on clips per video (default: none, the 17-30 s window decides)")
    routes.add_argument("--seconds", type=float, default=40.0, help="search time limit")
    routes.add_argument("--fresh", action="store_true", help="with --joins: compose the batch anew, ignoring the current plan's videos")
    command("render", cmd_render).add_argument("--plan", type=Path, required=True)
    packaging = command("package", cmd_package)
    packaging.add_argument("--config", type=Path)
    packaging.add_argument("--clean", action="store_true", help="deliver the clean videos without packaging (only on explicit request)")
    command("status", cmd_status)
    decide = command("decide", cmd_decide)
    decide.add_argument("--key", required=True, choices=sorted(JOB.DECISIONS))
    decide.add_argument("--value", required=True, help="JSON value, e.g. 2, true, \"reasonable\"")
    decide.add_argument("--instruction", required=True, help="the user's own words, or 'fallback: <why>' for a default you took")
    command("bgm-assets", cmd_bgm_assets)
    fetch = command("fetch-bgm", cmd_fetch_bgm)
    fetch.add_argument("--url")
    fetch.add_argument("--file", type=Path)
    for name in ("--license", "--license-url", "--license-evidence", "--source-page", "--title", "--creator"):
        fetch.add_argument(name, required=True)
    fetch.add_argument("--instrumental", action="store_true")
    fetch.add_argument("--asset-audio-unsuitable")
    inspection = command("runtime", cmd_runtime, job=False)
    inspection.add_argument("--feature", choices=("base", "media", "packaging", "asr"), default="base")
    inspection.add_argument("--install", action="store_true")
    inspection.add_argument("--job-dir", type=Path, help="ignored (the runtime is shared by every job)")
    args = parser.parse_args()
    try:
        code = args.handler(args)
    except runtime.RuntimeFailure as error:
        print(f"runtime error: {error}", file=sys.stderr, flush=True)
        code = 3
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        code = RULE_FAILURE
    except (OSError, ImportError) as error:
        print(f"runtime error: {error}; retry the command after fixing the runtime", file=sys.stderr, flush=True)
        code = 3
    except RuntimeError as error:
        print(f"media error: {error}", file=sys.stderr, flush=True)
        code = 3
    raise SystemExit(code)


if __name__ == "__main__":
    main()
