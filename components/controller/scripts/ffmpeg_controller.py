from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

CONTROLLER_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_BIN = CONTROLLER_ROOT.parent.parent / "dependencies" / "ffmpeg" / "bin"
FFMPEG = str(BUNDLED_BIN / "ffmpeg.exe")
FFPROBE = str(BUNDLED_BIN / "ffprobe.exe")


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


def preflight(output: Path) -> int:
    checks={}
    for name,binary in (("ffmpeg",FFMPEG),("ffprobe",FFPROBE)):
        run=subprocess.run([binary,"-version"],capture_output=True,text=True)
        checks[name]=run.returncode==0
    encoders=subprocess.run([FFMPEG,"-hide_banner","-encoders"],capture_output=True,text=True).stdout
    encoder="h264_nvenc" if "h264_nvenc" in encoders else "libx264"
    report={"schema":"ffmpeg-controller-preflight/v260928","decision":"pass" if all(checks.values()) else "reject","checks":checks,"ffmpeg_path":FFMPEG,"ffprobe_path":FFPROBE,"bundled_runtime":Path(FFMPEG).is_file() and Path(FFPROBE).is_file(),"selected_encoder":encoder,"at":datetime.now().astimezone().isoformat(timespec="seconds")}
    atomic(output,report); print(json.dumps(report,ensure_ascii=False)); return 0 if report["decision"]=="pass" else 2


def finalize_one(item: dict, output_dir: Path, width: int, height: int, fps: int, encoder: str) -> dict:
    source=Path(item["export_path"]).resolve(); plan_id=item["plan_id"]
    target=output_dir/f"{plan_id}.mp4"; partial=target.with_suffix(".partial.mp4")
    if target.exists() or partial.exists(): raise RuntimeError(f"refusing overwrite: {target}")
    codec=["-c:v","h264_nvenc","-preset","p4","-tune","hq","-rc","vbr","-cq","19","-b:v","0"] if encoder=="h264_nvenc" else ["-c:v","libx264","-preset","veryfast","-crf","18"]
    command=[FFMPEG,"-hide_banner","-nostdin","-y","-i",str(source),"-map","0:v:0","-map","0:a:0","-vf",f"fps={fps},scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p","-r",str(fps),"-fps_mode","cfr",*codec,"-c:a","copy","-movflags","+faststart",str(partial)]
    run=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if run.returncode: raise RuntimeError(run.stderr[-3000:])
    info=probe(partial); video=next(x for x in info["streams"] if x["codec_type"]=="video"); audio=next(x for x in info["streams"] if x["codec_type"]=="audio")
    checks={"h264":video["codec_name"]=="h264","dimensions":int(video["width"])==width and int(video["height"])==height,"fps":video["avg_frame_rate"]==f"{fps}/1","aac":audio["codec_name"]=="aac","audio_rate":int(audio["sample_rate"])==48000}
    if not all(checks.values()): raise RuntimeError(f"technical validation failed: {checks}")
    partial.replace(target)
    return {"plan_id":plan_id,"premaster_path":str(source),"premaster_sha256":sha(source),"output_path":str(target),"output_sha256":sha(target),"duration":float(info["format"]["duration"]),"render_evidence_path":str(Path(item["render_evidence_path"]).resolve()),"render_evidence_sha256":item["render_evidence_sha256"],"command":command,"checks":checks}


def finalize(args) -> int:
    source=read(args.premaster_manifest); release=read(args.semantic_release)
    if release.get("decision")!="pass" or release.get("package_version")!="v260928": raise RuntimeError("current semantic release is not pass")
    require_frame_native_manifest(source)
    items=source.get("results",[]); args.output_dir.mkdir(parents=True,exist_ok=True)
    premaster_ids=[str(item.get("plan_id") or "") for item in items]; authorized=[str(item) for item in release.get("authorized_outputs",[])]
    if source.get("output_count")!=len(items) or set(premaster_ids)!=set(authorized) or len(premaster_ids)!=len(authorized) or len(set(premaster_ids))!=len(premaster_ids): raise RuntimeError("premaster plan set must equal complete semantic authorization")
    encoder=args.encoder
    if encoder=="auto": encoder="h264_nvenc" if "h264_nvenc" in subprocess.run([FFMPEG,"-hide_banner","-encoders"],capture_output=True,text=True).stdout else "libx264"
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(finalize_one,item,args.output_dir,args.width,args.height,args.fps,encoder) for item in items]
        for future in as_completed(futures): results.append(future.result())
    results.sort(key=lambda item:item["plan_id"])
    manifest={"schema":"ffmpeg-controller-delivery/v260928","render_mode":"source_frame_ranges/v1","seconds_only_fallback":False,"created_at":datetime.now().astimezone().isoformat(timespec="seconds"),"semantic_release_path":str(args.semantic_release.resolve()),"semantic_release_sha256":sha(args.semantic_release),"output_root":str(args.output_dir.resolve()),"output_count":len(results),"video_spec":{"width":args.width,"height":args.height,"fps":f"{args.fps}/1","codec":"h264"},"audio_spec":{"codec":"aac","sample_rate":48000,"bgm_added":False},"results":results}
    atomic(args.manifest,manifest); print(json.dumps({"decision":"pass","count":len(results),"manifest":str(args.manifest)},ensure_ascii=False)); return 0


def validate(args) -> int:
    manifest,release,post=read(args.manifest),read(args.semantic_release),read(args.post_qc)
    opening=read(args.opening_family_report)
    failures=[]
    if release.get("decision")!="pass": failures.append("semantic_release")
    if {str(item.get("plan_id")) for item in manifest.get("results",[])}!={str(item) for item in release.get("authorized_outputs",[])}: failures.append("authorized_output_scope")
    machine=post.get("machine_signal_gate") if isinstance(post.get("machine_signal_gate"),dict) else {}
    machine_path=Path(str(machine.get("path", "")))
    independent=post.get("independent_findings") if isinstance(post.get("independent_findings"),dict) else {}
    independent_path=Path(str(independent.get("path", "")))
    exact=post.get("exact_cut_evidence") if isinstance(post.get("exact_cut_evidence"),dict) else {}
    exact_path=Path(str(exact.get("path", "")))
    if post.get("schema")!="ffmpeg-post-encode-qc/v260928" or post.get("decision")!="pass" or post.get("delivery_manifest_sha256")!=sha(args.manifest): failures.append("post_encode_qc")
    if machine.get("decision")!="pass" or not machine_path.is_file() or sha(machine_path)!=str(machine.get("sha256", "")).lower(): failures.append("machine_signal_gate")
    if not independent_path.is_file() or sha(independent_path)!=str(independent.get("sha256", "")).lower(): failures.append("independent_findings")
    if not exact_path.is_file() or sha(exact_path)!=str(exact.get("sha256", "")).lower(): failures.append("exact_cut_evidence")
    if opening.get("schema")!="opening-visual-family-release-gate/v260928" or opening.get("decision")!="pass" or opening.get("delivery_manifest_sha256")!=sha(args.manifest): failures.append("opening_visual_family_gate")
    for item in manifest.get("results",[]):
        for segment in item.get("applied_repairs",[]):
            if float((segment.get("profile") or {}).get("video_hold",0.0) or 0.0)>0:
                failures.append(f"artificial_video_hold:{item.get('plan_id')}:{segment.get('candidate_id')}")
        path=Path(item["output_path"])
        if not path.is_file() or sha(path)!=item["output_sha256"]: failures.append(f"output:{item.get('plan_id')}")
        else:
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
