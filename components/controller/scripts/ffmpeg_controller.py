from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

CONTROLLER_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_BIN = CONTROLLER_ROOT.parent.parent / "dependencies" / "ffmpeg" / "bin"
FFMPEG = str(BUNDLED_BIN / "ffmpeg.exe")
FFPROBE = str(BUNDLED_BIN / "ffprobe.exe")
EVIDENCE_SPEC = importlib.util.spec_from_file_location("post_encode_evidence", Path(__file__).resolve().with_name("post_encode_evidence.py"))
evidence_module = importlib.util.module_from_spec(EVIDENCE_SPEC)
assert EVIDENCE_SPEC.loader
EVIDENCE_SPEC.loader.exec_module(evidence_module)
verify_boundary_files = evidence_module.verify_boundary_files
OPENING_SPEC = importlib.util.spec_from_file_location("opening_visual_family_gate", CONTROLLER_ROOT.parent / "semantic/scripts/v20_opening_visual_family_gate.py")
opening_module = importlib.util.module_from_spec(OPENING_SPEC)
assert OPENING_SPEC.loader
OPENING_SPEC.loader.exec_module(opening_module)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(8*1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path,value: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.replace(temporary,path)


def probe(path: Path) -> dict:
    return json.loads(subprocess.run([FFPROBE,"-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,check=True).stdout)


def require_frame_native_manifest(source: dict) -> None:
    if source.get("render_mode") != "source_frame_ranges/v1" or source.get("seconds_only_fallback") is not False:
        raise RuntimeError("premaster manifest is not frame-native")
    for item in source.get("results", []):
        if item.get("render_mode") != "source_frame_ranges/v1":
            raise RuntimeError(f"premaster item is not frame-native: {item.get('plan_id')}")
        render_path=Path(str(item.get("render_evidence_path", "")))
        if not render_path.is_file() or sha(render_path)!=str(item.get("render_evidence_sha256", "")).lower():
            raise RuntimeError(f"hash-bound render evidence required: {item.get('plan_id')}")


def require_authorized_premasters(items: list[dict], release: dict) -> None:
    outputs = release.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(items):
        raise RuntimeError("complete semantic delivery outputs required")
    by_id = {str(row.get("plan_id")): row for row in outputs if isinstance(row, dict)}
    if len(by_id) != len(items):
        raise RuntimeError("duplicate semantic delivery plan IDs")
    for item in items:
        plan_id = str(item.get("plan_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", plan_id):
            raise RuntimeError(f"unsafe plan ID: {plan_id}")
        row = by_id.get(plan_id)
        if row is None:
            raise RuntimeError(f"semantic delivery missing plan: {plan_id}")
        export = Path(str(item.get("export_path") or ""))
        delivered = Path(str(row.get("path") or ""))
        render = read(Path(item["render_evidence_path"]))
        expected_hash = str(item.get("export_sha256") or "").lower()
        plan = Path(str(item.get("plan_path") or ""))
        plan_hash = str(item.get("plan_sha256") or "").lower()
        if (
            not export.is_file() or not delivered.is_file()
            or not plan.is_file() or sha(plan) != plan_hash
            or not expected_hash or sha(export) != expected_hash
            or sha(delivered) != expected_hash
            or str(row.get("sha256") or "").lower() != expected_hash
            or render.get("render_mode") != "source_frame_ranges/v1"
            or Path(str(render.get("export_path") or "")).resolve() != export.resolve()
            or str(render.get("export_sha256") or "").lower() != expected_hash
            or Path(str(render.get("plan_path") or "")).resolve() != plan.resolve()
            or str(render.get("plan_sha256") or "").lower() != plan_hash
        ):
            raise RuntimeError(f"premaster is not the semantic-authorized render: {plan_id}")


def preflight(output: Path) -> int:
    checks={}
    for name,binary in (("ffmpeg",FFMPEG),("ffprobe",FFPROBE)):
        run=subprocess.run([binary,"-version"],capture_output=True,text=True)
        checks[name]=run.returncode==0
    encoders=subprocess.run([FFMPEG,"-hide_banner","-encoders"],capture_output=True,text=True)
    checks["libx264"]=encoders.returncode==0 and "libx264" in encoders.stdout
    encoder="libx264"
    report={"schema":"ffmpeg-controller-preflight/v260928","decision":"pass" if all(checks.values()) else "reject","checks":checks,"ffmpeg_path":FFMPEG,"ffprobe_path":FFPROBE,"bundled_runtime":Path(FFMPEG).is_file() and Path(FFPROBE).is_file(),"selected_encoder":encoder,"at":datetime.now().astimezone().isoformat(timespec="seconds")}
    atomic(output,report); print(json.dumps(report,ensure_ascii=False)); return 0 if report["decision"]=="pass" else 2


def finalize_one(item: dict, output_dir: Path, width: int, height: int, fps: int, encoder: str) -> dict:
    source=Path(item["export_path"]).resolve(); plan_id=item["plan_id"]
    target=output_dir/f"{plan_id}.mp4"; partial=target.with_suffix(".partial.mp4")
    receipt_path=output_dir/".finalize_receipts"/f"{plan_id}.json"
    parameters={"width":width,"height":height,"fps":fps,"encoder":encoder}
    source_hash=sha(source)
    if partial.exists(): raise RuntimeError(f"refusing stale partial output: {partial}")
    if target.exists():
        if receipt_path.is_file():
            receipt=read(receipt_path)
            result=receipt.get("result") if isinstance(receipt.get("result"),dict) else {}
            if (receipt.get("schema")=="ffmpeg-controller-item-receipt/v260928"
                    and receipt.get("parameters")==parameters
                    and receipt.get("source_sha256")==source_hash
                    and Path(str(result.get("premaster_path") or "")).resolve()==source
                    and result.get("premaster_sha256")==source_hash
                    and Path(str(result.get("output_path") or "")).resolve()==target.resolve()
                    and result.get("output_sha256")==sha(target)
                    and Path(str(result.get("render_evidence_path") or "")).resolve()==Path(item["render_evidence_path"]).resolve()
                    and result.get("render_evidence_sha256")==item["render_evidence_sha256"]
                    and all(result.get("checks",{}).get(key) is True for key in ("h264","dimensions","fps","aac","audio_rate"))):
                return result
        raise RuntimeError(f"refusing overwrite without matching receipt: {target}")
    codec=["-c:v","h264_nvenc","-preset","p4","-tune","hq","-rc","vbr","-cq","19","-b:v","0"] if encoder=="h264_nvenc" else ["-c:v","libx264","-preset","veryfast","-crf","18"]
    command=[FFMPEG,"-hide_banner","-nostdin","-y","-i",str(source),"-map","0:v:0","-map","0:a:0","-vf",f"fps={fps},scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p","-r",str(fps),"-fps_mode","cfr",*codec,"-c:a","copy","-movflags","+faststart",str(partial)]
    try:
        run=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
        if run.returncode: raise RuntimeError(run.stderr[-3000:])
        info=probe(partial); video=next(x for x in info["streams"] if x["codec_type"]=="video"); audio=next(x for x in info["streams"] if x["codec_type"]=="audio")
        checks={"h264":video["codec_name"]=="h264","dimensions":int(video["width"])==width and int(video["height"])==height,"fps":video["avg_frame_rate"]==f"{fps}/1","aac":audio["codec_name"]=="aac","audio_rate":int(audio["sample_rate"])==48000}
        if not all(checks.values()): raise RuntimeError(f"technical validation failed: {checks}")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    result={"plan_id":plan_id,"premaster_path":str(source),"premaster_sha256":source_hash,"output_path":str(target),"output_sha256":sha(target),"duration":float(info["format"]["duration"]),"render_evidence_path":str(Path(item["render_evidence_path"]).resolve()),"render_evidence_sha256":item["render_evidence_sha256"],"command":command,"checks":checks}
    try:
        atomic(receipt_path,{"schema":"ffmpeg-controller-item-receipt/v260928","parameters":parameters,"source_sha256":source_hash,"result":result})
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return result


def finalize(args) -> int:
    source=read(args.premaster_manifest); release=read(args.semantic_release)
    if release.get("decision")!="pass" or release.get("package_version")!="v260928": raise RuntimeError("current semantic release is not pass")
    require_frame_native_manifest(source)
    items=source.get("results",[])
    premaster_ids=[str(item.get("plan_id") or "") for item in items]; authorized=[str(item) for item in release.get("authorized_outputs",[])]
    if source.get("output_count")!=len(items) or set(premaster_ids)!=set(authorized) or len(premaster_ids)!=len(authorized) or len(set(premaster_ids))!=len(premaster_ids): raise RuntimeError("premaster plan set must equal complete semantic authorization")
    require_authorized_premasters(items, release)
    encoder=args.encoder
    if encoder=="auto": encoder="libx264"
    args.output_dir.mkdir(parents=True,exist_ok=True)
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(finalize_one,item,args.output_dir,args.width,args.height,args.fps,encoder) for item in items]
        for future in as_completed(futures): results.append(future.result())
    results.sort(key=lambda item:item["plan_id"])
    manifest={"schema":"ffmpeg-controller-delivery/v260928","render_mode":"source_frame_ranges/v1","seconds_only_fallback":False,"created_at":datetime.now().astimezone().isoformat(timespec="seconds"),"semantic_release_path":str(args.semantic_release.resolve()),"semantic_release_sha256":sha(args.semantic_release),"output_root":str(args.output_dir.resolve()),"output_count":len(results),"video_spec":{"width":args.width,"height":args.height,"fps":f"{args.fps}/1","codec":"h264"},"audio_spec":{"codec":"aac","sample_rate":48000,"bgm_added":False},"results":results}
    atomic(args.manifest,manifest); print(json.dumps({"decision":"pass","count":len(results),"manifest":str(args.manifest)},ensure_ascii=False)); return 0


def recheck_post_review(post: dict, exact_path: Path, machine_path: Path, independent_path: Path, authority_path: Path) -> bool:
    reviewer=post.get("reviewer") if isinstance(post.get("reviewer"),dict) else {}
    review_id=reviewer.get("review_id")
    if not review_id or reviewer.get("role")!="independent_post_encode_reviewer":
        return False
    with TemporaryDirectory(prefix="video-montage-post-recheck-") as temporary:
        recheck_path=Path(temporary)/"recheck.json"
        command=[sys.executable,str(CONTROLLER_ROOT/"scripts/review_post_encode.py"),"--evidence",str(exact_path),
                 "--signal-scan",str(machine_path),"--findings",str(independent_path),
                 "--review-authority",str(authority_path),"--review-id",str(review_id),
                 "--output",str(recheck_path)]
        checked=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
        return checked.returncode==0 and recheck_path.is_file() and read(recheck_path).get("decision")=="pass"


def output_spec_matches(path: Path, manifest: dict) -> bool:
    declared_video=manifest.get("video_spec") if isinstance(manifest.get("video_spec"),dict) else {}
    declared_audio=manifest.get("audio_spec") if isinstance(manifest.get("audio_spec"),dict) else {}
    if (declared_video.get("codec")!="h264" or declared_audio.get("codec")!="aac"
            or declared_audio.get("sample_rate")!=48000):
        return False
    try:
        info=probe(path)
        videos=[item for item in info["streams"] if item.get("codec_type")=="video"]
        audios=[item for item in info["streams"] if item.get("codec_type")=="audio"]
        if len(videos)!=1 or len(audios)!=1 or len(info["streams"])!=2:
            return False
        video,audio=videos[0],audios[0]
        return (video.get("codec_name")=="h264" and audio.get("codec_name")=="aac"
                and int(video["width"])==int(declared_video["width"])
                and int(video["height"])==int(declared_video["height"])
                and video.get("avg_frame_rate")==declared_video.get("fps")
                and int(audio["sample_rate"])==48000)
    except (KeyError,TypeError,ValueError,subprocess.CalledProcessError):
        return False


def validate(args) -> int:
    manifest,release,post=read(args.manifest),read(args.semantic_release),read(args.post_qc)
    opening=read(args.opening_family_report)
    failures=[]
    results=manifest.get("results") if isinstance(manifest.get("results"),list) else []
    ids=[str(item.get("plan_id") or "") for item in results if isinstance(item,dict)]
    authorized=release.get("authorized_outputs") if isinstance(release.get("authorized_outputs"),list) else []
    if (manifest.get("schema")!="ffmpeg-controller-delivery/v260928" or not results
            or len(ids)!=len(results) or len(set(ids))!=len(ids) or any(not item for item in ids)
            or manifest.get("output_count")!=len(results)):
        failures.append("delivery_scope")
    if (release.get("schema")!="semantic-delivery-manifest/v260928" or release.get("decision")!="pass"
            or release.get("package_version")!="v260928"
            or Path(str(manifest.get("semantic_release_path") or "")).resolve()!=args.semantic_release.resolve()
            or manifest.get("semantic_release_sha256")!=sha(args.semantic_release)):
        failures.append("semantic_release")
    if sorted(ids)!=sorted(str(item) for item in authorized): failures.append("authorized_output_scope")
    machine=post.get("machine_signal_gate") if isinstance(post.get("machine_signal_gate"),dict) else {}
    machine_path=Path(str(machine.get("path", "")))
    independent=post.get("independent_findings") if isinstance(post.get("independent_findings"),dict) else {}
    independent_path=Path(str(independent.get("path", "")))
    exact=post.get("exact_cut_evidence") if isinstance(post.get("exact_cut_evidence"),dict) else {}
    exact_path=Path(str(exact.get("path", "")))
    if post.get("schema")!="ffmpeg-post-encode-qc/v260928" or post.get("decision")!="pass" or post.get("delivery_manifest_sha256")!=sha(args.manifest): failures.append("post_encode_qc")
    if machine.get("decision")!="pass" or not machine_path.is_file() or sha(machine_path)!=str(machine.get("sha256", "")).lower(): failures.append("machine_signal_gate")
    if not independent_path.is_file() or sha(independent_path)!=str(independent.get("sha256", "")).lower(): failures.append("independent_findings")
    authority=post.get("review_authority") if isinstance(post.get("review_authority"),dict) else {}
    authority_path=Path(str(authority.get("path", "")))
    if not authority_path.is_file() or sha(authority_path)!=str(authority.get("sha256", "")).lower(): failures.append("review_authority")
    if not exact_path.is_file() or sha(exact_path)!=str(exact.get("sha256", "")).lower(): failures.append("exact_cut_evidence")
    else:
        exact_value=read(exact_path)
        cuts=exact_value.get("cuts") if isinstance(exact_value.get("cuts"),list) else []
        if (exact_value.get("schema")!="ffmpeg-post-encode-evidence/v260928"
                or exact_value.get("delivery_manifest_sha256")!=sha(args.manifest)
                or exact_value.get("boundary_count")!=len(cuts)):
            failures.append("exact_cut_scope")
        by_plan={pid:[] for pid in ids}
        for cut in cuts:
            if not isinstance(cut,dict) or cut.get("plan_id") not in by_plan:
                failures.append("exact_cut_scope")
                continue
            by_plan[cut["plan_id"]].append(cut)
            if verify_boundary_files(cut):
                failures.append(f"exact_cut_files:{cut.get('plan_id')}:{cut.get('cut_index')}")
        for item in results:
            render_path=Path(str(item.get("render_evidence_path") or ""))
            if not render_path.is_file() or sha(render_path)!=str(item.get("render_evidence_sha256") or "").lower():
                failures.append(f"render_evidence:{item.get('plan_id')}")
                continue
            try:
                segments=read(render_path).get("segments",[])
                plan_cuts=by_plan.get(item.get("plan_id"),[])
                valid_scope=(isinstance(segments,list) and bool(segments) and len(plan_cuts)==len(segments)+1
                    and sorted((cut.get("cut_index"),cut.get("boundary_kind")) for cut in plan_cuts)
                    == [(0,"output_start"),*[(index,"concat_cut") for index in range(1,len(segments))],(len(segments),"output_end")])
            except (OSError,ValueError,TypeError,KeyError):
                valid_scope=False
            if not valid_scope:
                failures.append(f"exact_cut_scope:{item.get('plan_id')}")
    if not any(name in failures for name in ("post_encode_qc","machine_signal_gate","independent_findings","exact_cut_evidence","exact_cut_scope","review_authority")):
        if not recheck_post_review(post,exact_path,machine_path,independent_path,authority_path):
            failures.append("post_encode_review_recheck")
    request_path=Path(str(opening.get("request_path") or ""))
    opening_recheck=False
    if request_path.is_file() and sha(request_path)==opening.get("request_sha256"):
        try:
            opening_recheck=opening_module.audit(request_path).get("decision")=="pass"
        except (OSError,ValueError,TypeError,KeyError):
            opening_recheck=False
    if (opening.get("schema")!="opening-visual-family-release-gate/v260928" or opening.get("decision")!="pass"
            or opening.get("delivery_manifest_sha256")!=sha(args.manifest)
            or not opening_recheck):
        failures.append("opening_visual_family_gate")
    for item in results:
        for segment in item.get("applied_repairs",[]):
            if float((segment.get("profile") or {}).get("video_hold",0.0) or 0.0)>0:
                failures.append(f"artificial_video_hold:{item.get('plan_id')}:{segment.get('candidate_id')}")
        path=Path(item["output_path"])
        if not path.is_file() or sha(path)!=item["output_sha256"]: failures.append(f"output:{item.get('plan_id')}")
        else:
            if not output_spec_matches(path,manifest): failures.append(f"output_spec:{item.get('plan_id')}")
            run=subprocess.run([FFMPEG,"-v","error","-i",str(path),"-f","null","NUL"],capture_output=True,text=True)
            if run.returncode: failures.append(f"decode:{item.get('plan_id')}")
    report={"schema":"ffmpeg-controller-validation/v260928","decision":"pass" if not failures else "reject","failures":failures,"manifest_path":str(args.manifest.resolve()),"manifest_sha256":sha(args.manifest),"opening_visual_family_report_path":str(args.opening_family_report.resolve()),"opening_visual_family_report_sha256":sha(args.opening_family_report),"validated_outputs":len(manifest.get("results",[]))}
    atomic(args.report,report); print(json.dumps(report,ensure_ascii=False)); return 0 if not failures else 2


def main() -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("preflight"); p.add_argument("--output",type=Path,required=True)
    f=sub.add_parser("finalize"); f.add_argument("--premaster-manifest",type=Path,required=True); f.add_argument("--semantic-release",type=Path,required=True); f.add_argument("--output-dir",type=Path,required=True); f.add_argument("--manifest",type=Path,required=True); f.add_argument("--width",type=int,default=1440); f.add_argument("--height",type=int,default=2560); f.add_argument("--fps",type=int,default=60); f.add_argument("--encoder",choices=("auto","h264_nvenc","libx264"),default="auto"); f.add_argument("--workers",type=int,default=2)
    v=sub.add_parser("validate"); v.add_argument("--manifest",type=Path,required=True); v.add_argument("--semantic-release",type=Path,required=True); v.add_argument("--post-qc",type=Path,required=True); v.add_argument("--opening-family-report",type=Path,required=True); v.add_argument("--report",type=Path,required=True)
    args=parser.parse_args(); return preflight(args.output) if args.command=="preflight" else finalize(args) if args.command=="finalize" else validate(args)


if __name__=="__main__": raise SystemExit(main())
