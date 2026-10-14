#!/usr/bin/env python3
"""Freeze verified candidates and produce a hash-bound incremental source plan."""
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

POOL_SCHEMA = "semantic-verified-candidate-pool/v260928"
PLAN_SCHEMA = "semantic-candidate-pool-reuse-plan/v260928"

def now(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
def load(p: Path) -> dict:
    x=json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(x,dict): raise ValueError("JSON object required")
    return x
def sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()
def write(p: Path, x: object) -> None:
    p.parent.mkdir(parents=True,exist_ok=True); t=p.with_name(p.name+".partial")
    t.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8");t.replace(p)
def source_map(manifest: dict) -> dict[str,dict]:
    rows=manifest.get("sources")
    if not isinstance(rows,list): raise ValueError("source manifest requires sources")
    result={}
    for row in rows:
        if not isinstance(row,dict) or not row.get("source_id") or not row.get("source_sha256") or not row.get("processing_stage"): raise ValueError("source binding fields required")
        if row["source_id"] in result: raise ValueError("duplicate source id")
        result[row["source_id"]]=row
    return result
def refs_valid(c: dict) -> bool:
    pairs=[("actual_asr_path","actual_asr_sha256"),("identity_evidence_path","identity_evidence_sha256"),("semantic_cluster_review_path","semantic_cluster_review_sha256")]
    for path_key,hash_key in pairs:
        p=c.get(path_key); h=c.get(hash_key)
        if not isinstance(p,str) or not isinstance(h,str) or not Path(p).is_file() or sha(Path(p)).lower()!=h.lower(): return False
    frames=c.get("frames")
    if not isinstance(frames,dict) or not {"in","mid","out"}.issubset(frames): return False
    for key in ("in","mid","out"):
        row=frames[key]; p=row.get("path") if isinstance(row,dict) else None; h=row.get("sha256") if isinstance(row,dict) else None
        if not isinstance(p,str) or not isinstance(h,str) or not Path(p).is_file() or sha(Path(p)).lower()!=h.lower(): return False
    return True
def freeze(args) -> int:
    manifest=load(args.source_manifest); inventory=load(args.inventory); profile=load(args.style_profile); sources=source_map(manifest)
    candidates=inventory.get("candidates")
    coverage=inventory.get("source_coverage",{})
    if not isinstance(candidates,list) or not isinstance(coverage,dict): raise ValueError("inventory requires candidates and source_coverage")
    frozen=[]
    for c in candidates:
        if not isinstance(c,dict) or c.get("candidate_status")!="approved" or c.get("capacity_input_eligible") is not True: continue
        s=sources.get(c.get("source_id"))
        if not s or c.get("source_sha256","").lower()!=s["source_sha256"].lower() or c.get("processing_stage")!=s["processing_stage"] or not refs_valid(c): continue
        frozen.append(c)
    source_coverage={}
    for sid,s in sources.items():
        state=coverage.get(sid,{})
        source_coverage[sid]={"source_sha256":s["source_sha256"],"processing_stage":s["processing_stage"],"status":state.get("status","not_proven") if isinstance(state,dict) else "not_proven","candidate_count":sum(1 for c in frozen if c.get("source_id")==sid)}
    pool={"schema":POOL_SCHEMA,"created_at":now(),"source_manifest_sha256":sha(args.source_manifest),"style_profile_sha256":sha(args.style_profile),"style_profile_id":profile.get("profile"),"inventory_sha256":sha(args.inventory),"sources":source_coverage,"candidates":frozen}
    write(args.output,pool); return 0
def plan(args) -> int:
    manifest=load(args.source_manifest); profile=load(args.style_profile); sources=source_map(manifest); reusable=[]; rebuild=[]; reason={}
    pool=load(args.pool) if args.pool else None
    compatible=bool(pool and pool.get("schema")==POOL_SCHEMA and pool.get("style_profile_sha256")==sha(args.style_profile))
    for sid,s in sources.items():
        prior=(pool.get("sources",{}).get(sid) if compatible else None)
        unchanged=bool(prior and prior.get("source_sha256","").lower()==s["source_sha256"].lower() and prior.get("processing_stage")==s["processing_stage"] and prior.get("status") in {"audited","no_candidate"})
        if not unchanged: rebuild.append(s);reason[sid]="pool_missing_or_source_or_rule_changed";continue
        valid=[c for c in pool.get("candidates",[]) if c.get("source_id")==sid and refs_valid(c)]
        reusable.extend(valid)
    delta={"schema":"semantic-source-manifest/v260928","batch_id":manifest.get("batch_id"),"source_count":len(rebuild),"sources":rebuild,"created_at":now(),"purpose":"only new_or_changed_sources"}
    write(args.delta_source_manifest,delta)
    out={"schema":PLAN_SCHEMA,"created_at":now(),"style_profile_id":profile.get("profile"),"style_profile_sha256":sha(args.style_profile),"source_manifest_sha256":sha(args.source_manifest),"pool_path":str(args.pool.resolve()) if args.pool else None,"pool_sha256":sha(args.pool) if args.pool else None,"reusable_candidates":reusable,"reusable_candidate_count":len(reusable),"rebuild_source_ids":[s["source_id"] for s in rebuild],"rebuild_reasons":reason,"delta_source_manifest_path":str(args.delta_source_manifest.resolve()),"delta_source_manifest_sha256":sha(args.delta_source_manifest)}
    write(args.output,out);return 0
def main(argv=None):
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="cmd",required=True)
    f=sub.add_parser("freeze");f.add_argument("--source-manifest",type=Path,required=True);f.add_argument("--inventory",type=Path,required=True);f.add_argument("--style-profile",type=Path,required=True);f.add_argument("--output",type=Path,required=True)
    q=sub.add_parser("plan");q.add_argument("--source-manifest",type=Path,required=True);q.add_argument("--style-profile",type=Path,required=True);q.add_argument("--pool",type=Path);q.add_argument("--delta-source-manifest",type=Path,required=True);q.add_argument("--output",type=Path,required=True)
    a=p.parse_args(argv);return freeze(a) if a.cmd=="freeze" else plan(a)
if __name__=="__main__": raise SystemExit(main())
