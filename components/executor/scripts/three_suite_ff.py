from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path


def now(): return datetime.now().astimezone().isoformat(timespec="seconds")
def read(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()
def atomic(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(path.suffix+".tmp"); temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); os.replace(temp,path)
def run(command,allowed={0}):
    result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if result.returncode not in allowed: raise RuntimeError(f"command failed {result.returncode}: {command}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result
def root(explicit):
    if explicit: return Path(explicit).resolve()
    environment=os.environ.get("VIDEO_MONTAGE_ROOT")
    if environment: return Path(environment).resolve()
    portable=Path(__file__).resolve().parents[3]
    if (portable/"skill"/"video-montage"/"SKILL.md").is_file(): return portable
    raise RuntimeError("cannot locate video-montage suite")
def components(suite):
    semantic=suite/"components"/"semantic"
    controller=suite/"components"/"controller"
    executor=suite/"components"/"executor"
    packaging=suite/"components"/"packaging"
    return {
        "semantic":{"root":semantic,"interface":{"orchestrator":"scripts/v15_orchestrator.py","run_command":"run-continuous","gate_runtime":"scripts/v9_gate_runtime.py","frame_plan_gate":"scripts/v20_frame_plan_gate.py","portable_renderer":"scripts/portable_frame_renderer.py","frame_range_repair":"scripts/frame_range_repair.py","winky_ledger":"scripts/winky_ledger.py","fail_closed":"scripts/v20_fail_closed.py","completion_schema":"semantic-delivery-manifest/v260928"}},
        "controller":{"root":controller,"interface":{"controller":"scripts/ffmpeg_controller.py"}},
        "executor":{"root":executor,"interface":{"orchestrator":"scripts/three_suite_ff.py"}},
        "packaging":{"root":packaging,"interface":{"packager":"scripts/package_video.py"}},
    }
def verify(suite):
    verifier=suite/"tools"/"verify_runtime.py"
    if not verifier.is_file(): raise RuntimeError("runtime verifier missing")
    result=run([sys.executable,str(verifier)])
    if json.loads(result.stdout).get("decision")!="pass": raise RuntimeError("runtime verification failed")
    c=components(suite)
    required=[suite/"skill/video-montage/SKILL.md",c["semantic"]["root"]/c["semantic"]["interface"]["gate_runtime"],c["semantic"]["root"]/c["semantic"]["interface"]["frame_plan_gate"],c["semantic"]["root"]/c["semantic"]["interface"]["portable_renderer"],c["semantic"]["root"]/c["semantic"]["interface"]["frame_range_repair"],c["semantic"]["root"]/c["semantic"]["interface"]["winky_ledger"],c["controller"]["root"]/c["controller"]["interface"]["controller"],c["packaging"]["root"]/c["packaging"]["interface"]["packager"]]
    missing=[str(x) for x in required if not x.is_file()]
    if missing: raise RuntimeError(f"missing components: {missing}")
    return c
def require_reference(item,label):
    if not isinstance(item,dict) or not item.get("path") or not item.get("sha256"): raise RuntimeError(f"{label} reference missing")
    path=Path(item["path"]).resolve()
    if not path.is_file() or sha(path)!=item["sha256"]: raise RuntimeError(f"{label} reference changed")
    return path
def require_argument(path,item,label):
    actual=Path(path).resolve(); expected=require_reference(item,label)
    if actual!=expected: raise RuntimeError(f"{label} does not match the previous stage")
    return expected
def clear_after(value,stage,job):
    keys={
        "semantic":("semantic_prelock_recheck","semantic_completion","controller_preflight","delivery_manifest","controller_validation","packaging_draft","packaging_delivery","packaging_validation","completion"),
        "preflight":("delivery_manifest","controller_validation","packaging_draft","packaging_delivery","packaging_validation","completion"),
        "finalize":("controller_validation","packaging_draft","packaging_delivery","packaging_validation","completion"),
        "validate":("packaging_draft","packaging_delivery","packaging_validation","completion"),
        "packaging_draft":("packaging_delivery","packaging_validation","completion"),
        "packaging_finalize":("packaging_validation","completion"),
        "packaging_validate":("completion",),
    }
    if value.get("completion"):
        (job/"video_montage_completion.json").unlink(missing_ok=True)
    for key in keys[stage]: value.pop(key,None)
    if stage=="semantic": value["controller_invocations"]=[]
    if stage=="preflight": value["controller_invocations"]=[]
def state(job): return read(job/"three_suite_ff_state.json")
def save(job,value,phase,summary): value["phase"]=phase; value.setdefault("events",[]).append({"at":now(),"phase":phase,"summary":summary}); atomic(job/"three_suite_ff_state.json",value)


def main():
    p=argparse.ArgumentParser(); p.add_argument("--suite-root"); sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("preflight")
    i=sub.add_parser("init"); i.add_argument("--job-dir",type=Path,required=True); i.add_argument("--authorization",required=True); i.add_argument("--source-root",type=Path,required=True); i.add_argument("--output-root",type=Path,required=True); i.add_argument("--indexes",required=True)
    s=sub.add_parser("semantic-run"); s.add_argument("--job-dir",type=Path,required=True); s.add_argument("--config",type=Path,required=True); s.add_argument("--semantic-state",type=Path,required=True)
    d=sub.add_parser("semantic-complete"); d.add_argument("--job-dir",type=Path,required=True); d.add_argument("--manifest",type=Path,required=True)
    cp=sub.add_parser("controller-preflight"); cp.add_argument("--job-dir",type=Path,required=True); cp.add_argument("--report",type=Path,required=True)
    cf=sub.add_parser("controller-finalize"); cf.add_argument("--job-dir",type=Path,required=True); cf.add_argument("--premaster-manifest",type=Path,required=True); cf.add_argument("--semantic-release",type=Path,required=True); cf.add_argument("--output-dir",type=Path,required=True); cf.add_argument("--manifest",type=Path,required=True)
    cv=sub.add_parser("controller-validate"); cv.add_argument("--job-dir",type=Path,required=True); cv.add_argument("--manifest",type=Path,required=True); cv.add_argument("--semantic-release",type=Path,required=True); cv.add_argument("--post-qc",type=Path,required=True); cv.add_argument("--opening-family-report",type=Path,required=True); cv.add_argument("--report",type=Path,required=True)
    pd=sub.add_parser("packaging-draft"); pd.add_argument("--job-dir",type=Path,required=True); pd.add_argument("--output-dir",type=Path,required=True)
    pf=sub.add_parser("packaging-finalize"); pf.add_argument("--job-dir",type=Path,required=True); pf.add_argument("--config",type=Path,required=True); pf.add_argument("--output-dir",type=Path,required=True); pf.add_argument("--manifest",type=Path,required=True)
    pr=sub.add_parser("packaging-reburn"); pr.add_argument("--job-dir",type=Path,required=True); pr.add_argument("--plan-id",required=True); pr.add_argument("--subtitle-txt","--subtitle-srt",dest="subtitle_txt",type=Path,required=True); pr.add_argument("--output-dir",type=Path,required=True); pr.add_argument("--manifest",type=Path,required=True)
    pv=sub.add_parser("packaging-validate"); pv.add_argument("--job-dir",type=Path,required=True); pv.add_argument("--manifest",type=Path,required=True); pv.add_argument("--review",type=Path,required=True); pv.add_argument("--review-authority",type=Path,required=True); pv.add_argument("--report",type=Path,required=True)
    co=sub.add_parser("complete"); co.add_argument("--job-dir",type=Path,required=True)
    st=sub.add_parser("status"); st.add_argument("--job-dir",type=Path,required=True)
    a=p.parse_args(); suite=root(a.suite_root)
    portable_python=suite/"dependencies"/"python"/"python.exe"
    if not portable_python.is_file(): raise RuntimeError(f"bundled Python missing: {portable_python}")
    if Path(sys.executable).resolve()!=portable_python.resolve():
        raise SystemExit(subprocess.call([str(portable_python),str(Path(__file__).resolve()),*sys.argv[1:]]))
    c=verify(suite)
    if a.command=="preflight": print(json.dumps({"schema":"video-montage-preflight/v260928","decision":"pass","render_mode":"source_frame_ranges/v1","seconds_only_fallback":False,"components":list(c)},ensure_ascii=False)); return
    if a.command=="init":
        job=a.job_dir.resolve();
        if job.exists() and any(job.iterdir()): raise RuntimeError("refusing non-empty job")
        job.mkdir(parents=True,exist_ok=True); indexes=[]
        for part in a.indexes.split(","):
            if "-" in part: x,y=map(int,part.split("-")); indexes.extend(range(x,y+1))
            else: indexes.append(int(part))
        value={"schema":"video-montage-state/v260928","created_at":now(),"authorization":a.authorization,"source_root":str(a.source_root.resolve()),"output_root":str(a.output_root.resolve()),"expected_indexes":sorted(set(indexes)),"components":{k:{**{x:y for x,y in v.items() if x!="root"},"root":str(v["root"])} for k,v in c.items()},"semantic_invocations":[],"controller_invocations":[],"events":[]}; save(job,value,"initialized","bound video-montage components"); print(job/"three_suite_ff_state.json"); return
    job=a.job_dir.resolve(); value=state(job)
    if a.command=="semantic-run":
        semantic=c["semantic"]; command=[sys.executable,str(semantic["root"]/semantic["interface"]["orchestrator"]),semantic["interface"]["run_command"],"--config",str(a.config.resolve()),"--state",str(a.semantic_state.resolve())]; result=run(command,{0,20,22}); clear_after(value,"semantic",job); value["semantic_invocations"].append({"at":now(),"command":command,"returncode":result.returncode}); save(job,value,"semantic_invoked",f"semantic returned {result.returncode}"); print(result.stdout); raise SystemExit(result.returncode)
    if a.command=="semantic-complete":
        if not value.get("semantic_invocations") or value["semantic_invocations"][-1]["returncode"]!=0: raise RuntimeError("successful semantic run required")
        manifest=read(a.manifest); expected=c["semantic"]["interface"]["completion_schema"]
        if manifest.get("schema")!=expected: raise RuntimeError("semantic completion schema mismatch")
        authorized=manifest.get("authorized_outputs") if isinstance(manifest.get("authorized_outputs"),list) else []
        if manifest.get("decision")!="pass" or manifest.get("package_version")!="v260928" or int(manifest.get("requested_outputs",0) or 0)!=len(value.get("expected_indexes",[])) or len(authorized)!=len(value.get("expected_indexes",[])) or len(set(authorized))!=len(authorized): raise RuntimeError("complete semantic authorization set required")
        evidence=manifest.get("evidence") if isinstance(manifest.get("evidence"),dict) else {}
        required=("prelock_audit","candidate_inventory","batch_lock_request","work_order")
        bound={}
        for name in required:
            item=evidence.get(name) if isinstance(evidence.get(name),dict) else {}; path=Path(str(item.get("path","")))
            if not path.is_file() or sha(path)!=str(item.get("sha256","")).lower(): raise RuntimeError(f"semantic completion missing bound {name}")
            bound[name]=path
        audit=read(bound["prelock_audit"]); packaged_registry=c["semantic"]["root"]/"references"/"wuzimu-v20-invalid-intervals.json"
        if audit.get("schema")!="video-montage-fail-closed-prelock-report/v260928" or audit.get("decision")!="pass" or audit.get("registry_sha256")!=sha(packaged_registry) or audit.get("request_sha256")!=sha(bound["batch_lock_request"]) or int(audit.get("declared_plan_count",0) or 0)!=len(authorized) or int(audit.get("requested_outputs",0) or 0)!=len(authorized): raise RuntimeError("current packaged exhaustive prelock audit required")
        request_value=read(bound["batch_lock_request"]); request_ids=[str(plan.get("plan_id") or "") for plan in request_value.get("plans",[])]; work_value=read(bound["work_order"])
        if set(request_ids)!=set(authorized) or len(request_ids)!=len(authorized) or int(work_value.get("requested_outputs",0) or 0)!=len(authorized): raise RuntimeError("semantic authorized outputs must equal complete request scope")
        recheck=job/"v20_prelock_recheck.json"; fail_closed_script=c["semantic"]["root"]/c["semantic"]["interface"]["fail_closed"]
        run([sys.executable,str(fail_closed_script),"audit-request","--request",str(bound["batch_lock_request"]),"--registry",str(packaged_registry),"--output",str(recheck)])
        recheck_value=read(recheck)
        if recheck_value.get("schema")!="video-montage-fail-closed-prelock-report/v260928" or recheck_value.get("decision")!="pass": raise RuntimeError("independent packaged prelock recheck failed")
        clear_after(value,"semantic",job)
        value["semantic_prelock_recheck"]={"path":str(recheck.resolve()),"sha256":sha(recheck)}
        value["semantic_completion"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}; save(job,value,"semantic_completed","V20 semantic receipt verified"); return
    controller=c["controller"]; script=controller["root"]/controller["interface"]["controller"]
    if a.command=="controller-preflight":
        require_reference(value.get("semantic_completion"),"semantic completion")
        run([sys.executable,str(script),"preflight","--output",str(a.report.resolve())]); clear_after(value,"preflight",job); value["controller_preflight"]={"path":str(a.report.resolve()),"sha256":sha(a.report)}; save(job,value,"controller_preflight","FF controller preflight passed"); return
    if a.command=="controller-finalize":
        require_reference(value.get("controller_preflight"),"controller preflight")
        require_argument(a.semantic_release,value.get("semantic_completion"),"semantic release")
        command=[sys.executable,str(script),"finalize","--premaster-manifest",str(a.premaster_manifest.resolve()),"--semantic-release",str(a.semantic_release.resolve()),"--output-dir",str(a.output_dir.resolve()),"--manifest",str(a.manifest.resolve())]; run(command); clear_after(value,"finalize",job); value["controller_invocations"].append({"at":now(),"command":command}); value["delivery_manifest"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}; save(job,value,"controller_outputs","FF exact-60 outputs completed"); return
    if a.command=="controller-validate":
        delivery_path=require_argument(a.manifest,value.get("delivery_manifest"),"delivery manifest")
        semantic_path=require_argument(a.semantic_release,value.get("semantic_completion"),"semantic release")
        require_reference(value.get("controller_preflight"),"controller preflight")
        delivery=read(delivery_path)
        if Path(delivery.get("semantic_release_path","")).resolve()!=semantic_path or delivery.get("semantic_release_sha256")!=value["semantic_completion"]["sha256"]: raise RuntimeError("delivery manifest semantic release mismatch")
        command=[sys.executable,str(script),"validate","--manifest",str(a.manifest.resolve()),"--semantic-release",str(a.semantic_release.resolve()),"--post-qc",str(a.post_qc.resolve()),"--opening-family-report",str(a.opening_family_report.resolve()),"--report",str(a.report.resolve())]; run(command); report=read(a.report)
        if report.get("decision")!="pass" or Path(report.get("manifest_path","")).resolve()!=delivery_path or report.get("manifest_sha256")!=value["delivery_manifest"]["sha256"]: raise RuntimeError("controller validation rejected or unbound")
        clear_after(value,"validate",job)
        value["controller_validation"]={"path":str(a.report.resolve()),"sha256":sha(a.report),"post_qc":str(a.post_qc.resolve()),"post_qc_sha256":sha(a.post_qc),"opening_family_report":str(a.opening_family_report.resolve()),"opening_family_report_sha256":sha(a.opening_family_report)}; save(job,value,"controller_validated","post-encode, opening-family and technical QC passed"); return
    packager=c["packaging"]["root"]/c["packaging"]["interface"]["packager"]
    if a.command=="packaging-draft":
        require_reference(value.get("controller_validation"),"controller validation")
        delivery=require_reference(value.get("delivery_manifest"),"clean delivery")
        run([sys.executable,str(packager),"draft-batch","--delivery-manifest",str(delivery),"--output-dir",str(a.output_dir.resolve())])
        draft=a.output_dir.resolve()/"临时文件"/"报告"/"subtitle_draft.json"
        clear_after(value,"packaging_draft",job)
        value["packaging_draft"]={"path":str(draft),"sha256":sha(draft)}; save(job,value,"packaging_drafted","editable subtitles generated"); return
    if a.command=="packaging-finalize":
        validation=require_reference(value.get("controller_validation"),"controller validation")
        require_reference(value.get("packaging_draft"),"subtitle draft")
        delivery=require_reference(value.get("delivery_manifest"),"clean delivery")
        run([sys.executable,str(packager),"render","--config",str(a.config.resolve()),"--output-dir",str(a.output_dir.resolve()),"--manifest",str(a.manifest.resolve()),"--delivery-manifest",str(delivery),"--controller-validation",str(validation)])
        clear_after(value,"packaging_finalize",job)
        value["packaging_delivery"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}; save(job,value,"packaging_outputs","packaged outputs completed"); return
    if a.command=="packaging-reburn":
        require_reference(value.get("controller_validation"),"controller validation")
        previous=require_reference(value.get("packaging_delivery"),"previous packaging delivery")
        run([sys.executable,str(packager),"reburn","--previous-manifest",str(previous),"--plan-id",a.plan_id,
             "--subtitle-txt",str(a.subtitle_txt.resolve()),"--output-dir",str(a.output_dir.resolve()),"--manifest",str(a.manifest.resolve())])
        clear_after(value,"packaging_finalize",job)
        value["packaging_delivery"]={"path":str(a.manifest.resolve()),"sha256":sha(a.manifest)}
        save(job,value,"packaging_outputs","edited subtitle TXT burned into new packaged outputs"); return
    if a.command=="packaging-validate":
        manifest=require_argument(a.manifest,value.get("packaging_delivery"),"packaging delivery")
        run([sys.executable,str(packager),"validate","--manifest",str(manifest),"--review",str(a.review.resolve()),"--review-authority",str(a.review_authority.resolve()),"--report",str(a.report.resolve())])
        report=read(a.report)
        if report.get("decision")!="pass" or report.get("manifest_sha256")!=value["packaging_delivery"]["sha256"]: raise RuntimeError("packaging validation failed")
        clear_after(value,"packaging_validate",job)
        value["packaging_validation"]={"path":str(a.report.resolve()),"sha256":sha(a.report),"review_path":str(a.review.resolve()),"review_sha256":sha(a.review),"authority_path":str(a.review_authority.resolve()),"authority_sha256":sha(a.review_authority)}; save(job,value,"packaging_validated","packaging review and technical QC passed"); return
    if a.command=="complete":
        if not value.get("controller_invocations"): raise RuntimeError("controller invocation missing")
        for key in ("semantic_prelock_recheck","semantic_completion","controller_preflight","delivery_manifest","controller_validation"):
            require_reference(value.get(key),key)
        delivery=read(value["delivery_manifest"]["path"]); validation=read(value["controller_validation"]["path"]); semantic=read(value["semantic_completion"]["path"])
        if delivery.get("schema")!="ffmpeg-controller-delivery/v260928" or Path(delivery.get("semantic_release_path","")).resolve()!=Path(value["semantic_completion"]["path"]).resolve() or delivery.get("semantic_release_sha256")!=value["semantic_completion"]["sha256"]: raise RuntimeError("delivery is not bound to semantic completion")
        if validation.get("schema")!="ffmpeg-controller-validation/v260928" or validation.get("decision")!="pass" or Path(validation.get("manifest_path","")).resolve()!=Path(value["delivery_manifest"]["path"]).resolve() or validation.get("manifest_sha256")!=value["delivery_manifest"]["sha256"]: raise RuntimeError("validation is not bound to delivery")
        for name in ("post_qc","opening_family_report"):
            path=Path(value["controller_validation"].get(name,""))
            if not path.is_file() or sha(path)!=value["controller_validation"].get(name+"_sha256"): raise RuntimeError(f"{name} changed")
            if read(path).get("delivery_manifest_sha256")!=value["delivery_manifest"]["sha256"]: raise RuntimeError(f"{name} delivery binding mismatch")
        if Path(validation.get("opening_visual_family_report_path","")).resolve()!=Path(value["controller_validation"]["opening_family_report"]).resolve() or validation.get("opening_visual_family_report_sha256")!=value["controller_validation"]["opening_family_report_sha256"]: raise RuntimeError("opening family report mismatch")
        for name,item in semantic.get("evidence",{}).items():
            require_reference(item,f"semantic evidence {name}")
        results=delivery.get("results",[]); authorized=semantic.get("authorized_outputs",[])
        if len(results)!=len(value.get("expected_indexes",[])) or sorted(item.get("plan_id") for item in results)!=sorted(authorized): raise RuntimeError("delivery scope mismatch")
        for item in results:
            path=Path(item.get("output_path", ""))
            if not path.is_file() or sha(path)!=item.get("output_sha256"): raise RuntimeError(f"delivered output changed: {item.get('plan_id')}")
        packaged={}
        if value.get("packaging_draft"):
            package_path=require_reference(value.get("packaging_delivery"),"packaging delivery")
            report_path=require_reference(value.get("packaging_validation"),"packaging validation")
            review_path=require_reference({"path":value["packaging_validation"].get("review_path"),"sha256":value["packaging_validation"].get("review_sha256")},"packaging review")
            authority_path=require_reference({"path":value["packaging_validation"].get("authority_path"),"sha256":value["packaging_validation"].get("authority_sha256")},"packaging review authority")
            package,report=read(package_path),read(report_path)
            if package.get("clean_delivery_sha256")!=value["delivery_manifest"]["sha256"] or report.get("decision")!="pass" or report.get("manifest_sha256")!=sha(package_path) or report.get("review_sha256")!=sha(review_path) or report.get("review_authority_sha256")!=sha(authority_path): raise RuntimeError("packaging validation binding mismatch")
            for item in package.get("results",[]):
                path=Path(item.get("output_path",""))
                if not path.is_file() or sha(path)!=item.get("output_sha256"): raise RuntimeError("packaged output changed")
            recheck=job/"packaging_completion_recheck.json"
            run([sys.executable,str(packager),"validate","--manifest",str(package_path),"--review",str(review_path),"--review-authority",str(authority_path),"--report",str(recheck)])
            if read(recheck).get("decision")!="pass" or read(recheck).get("manifest_sha256")!=sha(package_path): raise RuntimeError("packaging completion recheck failed")
            packaged={"packaging_delivery":value["packaging_delivery"],"packaging_validation":value["packaging_validation"],"packaging_completion_recheck":{"path":str(recheck),"sha256":sha(recheck)}}
        receipt={"schema":"video-montage-completion/v260928","completed_at":now(),"semantic":value["semantic_completion"],"controller_preflight":value["controller_preflight"],"delivery_manifest":value["delivery_manifest"],"controller_validation":value["controller_validation"],**packaged,"components":value["components"]}; atomic(job/"video_montage_completion.json",receipt); value["completion"]={"path":str(job/"video_montage_completion.json"),"sha256":sha(job/"video_montage_completion.json")}; save(job,value,"complete","video-montage release complete"); print(value["completion"]["path"]); return
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
