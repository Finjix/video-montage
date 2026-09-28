from __future__ import annotations

import argparse, hashlib, json, os
from importlib.metadata import distributions
from datetime import datetime
from pathlib import Path


ROOT=Path(__file__).resolve().parent.parent
COMPONENTS={
 "controller":{"relative_path":"components/ffmpeg-montage-controller","required":["SKILL.md","scripts/ffmpeg_controller.py","scripts/post_encode_evidence.py","scripts/boundary_signal_scan.py","scripts/review_post_encode.py","scripts/runtime_paths.py","tests/test_frame_native_manifest.py","tests/test_boundary_signal_scan.py","tests/test_post_encode_review.py","tests/test_post_encode_scope.py","tests/test_controller_opening_family_gate.py"],"interface":{"controller":"scripts/ffmpeg_controller.py","preflight":"preflight","finalize":"finalize","validate":"validate","runtime_paths":"scripts/runtime_paths.py","post_encode_evidence":"scripts/post_encode_evidence.py","boundary_signal_scan":"scripts/boundary_signal_scan.py","review_post_encode":"scripts/review_post_encode.py","required_render_mode":"source_frame_ranges/v1"}},
 "semantic":{"relative_path":"components/semantic-analysis-training-backup-v20","required":["SKILL.md","capability.json","scripts/v15_orchestrator.py","scripts/v9_gate_runtime.py","scripts/v20_fail_closed.py","scripts/v20_dense_boundary_evidence.py","scripts/v20_boundary_signal_scan.py","scripts/v20_review_boundary.py","scripts/v20_frame_plan_gate.py","scripts/v20_opening_visual_family_gate.py","scripts/portable_frame_renderer.py","scripts/frame_range_repair.py","scripts/winky_ledger.py","references/frame-native-rendering-v20.2.md","references/v20-internal-silence-compaction.md","references/wuzimu-v20-folder-regression-gate.md","references/wuzimu-v20-invalid-intervals.json","references/wuzimu-v19-feedback-contract.json","references/frame-plan.example.json","tests/test_v20_frame_native.py","tests/test_v20_boundary_signal_scan.py","tests/test_v20_fail_closed.py","tests/test_v20_known_regressions.py","tests/test_v20_opening_visual_diversity.py","tests/test_v20_internal_silence_and_opening_release.py","scripts/v9_deliver.py","scripts/validate_package.py"],"interface":{"orchestrator":"scripts/v15_orchestrator.py","run_command":"run-continuous","gate_runtime":"scripts/v9_gate_runtime.py","frame_plan_gate":"scripts/v20_frame_plan_gate.py","opening_visual_family_gate":"scripts/v20_opening_visual_family_gate.py","portable_renderer":"scripts/portable_frame_renderer.py","frame_range_repair":"scripts/frame_range_repair.py","winky_ledger":"scripts/winky_ledger.py","fail_closed":"scripts/v20_fail_closed.py","dense_boundary_evidence":"scripts/v20_dense_boundary_evidence.py","boundary_signal_scan":"scripts/v20_boundary_signal_scan.py","independent_boundary_review":"scripts/v20_review_boundary.py","deliver":"scripts/v9_deliver.py","validate":"scripts/validate_package.py","completion_schema":"semantic-delivery-manifest/v20"},"package_id":"semantic-analysis-training-backup-v20","version":"20.2.5"},
 "executor":{"relative_path":"components/montage-three-part-orchestrator-ff","required":["SKILL.md","scripts/three_suite_ff.py"],"interface":{"orchestrator":"scripts/three_suite_ff.py"}},
}


def sha(path):
 digest=hashlib.sha256()
 with Path(path).open("rb") as stream:
  for block in iter(lambda:stream.read(8*1024*1024),b""):
   digest.update(block)
 return digest.hexdigest()
def atomic(path,value):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); os.replace(tmp,path)
def members(root):
 return [{"path":str(p.relative_to(root)).replace("\\","/"),"sha256":sha(p),"size":p.stat().st_size} for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc"]
def tree_hash(rows): return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def build():
 manifests={}; now=datetime.now().astimezone().isoformat(timespec="seconds")
 dependencies_root=ROOT/"dependencies"; python_root=dependencies_root/"python"
 required_python=("python.exe","python313.dll","python313.zip","python313._pth","msvcp140.dll","msvcp140_1.dll","Lib/site-packages/faster_whisper/__init__.py","Lib/site-packages/onnxruntime/__init__.py","Lib/site-packages/sitecustomize.py")
 missing_python=[name for name in required_python if not (python_root/name).is_file()]
 if missing_python: raise RuntimeError(f"portable Python missing {missing_python}")
 required_dependencies=("ffmpeg/bin/ffmpeg.exe","ffmpeg/bin/ffprobe.exe","models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf/model.bin","cuda/bin/cublas64_12.dll","cuda/bin/cublasLt64_12.dll","cuda/bin/cudart64_12.dll")
 missing_dependencies=[name for name in required_dependencies if not (dependencies_root/name).is_file()]
 if missing_dependencies: raise RuntimeError(f"portable dependencies missing {missing_dependencies}")
 dependency_rows=members(dependencies_root)
 package_rows={dist.metadata["Name"]:dist.version for dist in distributions(path=[str(python_root/"Lib"/"site-packages")]) if dist.metadata.get("Name")}
 dependency_manifest={"schema":"ff-suite-dependencies-manifest/v1","python_version":"3.13.15","python_packages":dict(sorted(package_rows.items(),key=lambda item:item[0].casefold())),"root":str(dependencies_root.resolve()),"file_count":len(dependency_rows),"tree_sha256":tree_hash(dependency_rows),"files":dependency_rows}
 dependency_manifest_path=ROOT/".manifests"/"dependencies.json"; atomic(dependency_manifest_path,dependency_manifest)
 for name,spec in COMPONENTS.items():
  root=ROOT/spec["relative_path"]; missing=[x for x in spec["required"] if not (root/x).is_file()]
  if missing: raise RuntimeError(f"{name} missing {missing}")
  rows=members(root); value={"schema":"three-suite-component-manifest/v20","component":name,"root":str(root.resolve()),"file_count":len(rows),"tree_sha256":tree_hash(rows),"required_files":spec["required"],"files":rows}; path=ROOT/".manifests"/f"{name}.json"; atomic(path,value); manifests[name]=(path,value)
 semantic=COMPONENTS["semantic"]; active={"schema":"three-suite-semantic-active/v20","package_id":semantic["package_id"],"version":semantic["version"],"relative_path":semantic["relative_path"],"tree_sha256":manifests["semantic"][1]["tree_sha256"],"interface":semantic["interface"],"activated_at":now}; atomic(ROOT/".manifests"/"semantic-active.json",active)
 runtime_files={
  "python":python_root/"python.exe",
  "python_dll":python_root/"python313.dll",
  "python_config":python_root/"python313._pth",
  "dependencies_manifest":dependency_manifest_path,
  "portable_renderer":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/portable_frame_renderer.py",
  "frame_range_repair":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/frame_range_repair.py",
  "frame_plan_gate":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/v20_frame_plan_gate.py",
  "winky_ledger":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/winky_ledger.py",
  "semantic_boundary_signal_scan":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/v20_boundary_signal_scan.py",
  "opening_visual_family_gate":ROOT/COMPONENTS["semantic"]["relative_path"] / "scripts/v20_opening_visual_family_gate.py",
  "controller_boundary_signal_scan":ROOT/COMPONENTS["controller"]["relative_path"] / "scripts/boundary_signal_scan.py",
  "ffmpeg":dependencies_root/"ffmpeg/bin/ffmpeg.exe",
  "ffprobe":dependencies_root/"ffmpeg/bin/ffprobe.exe",
  "model":dependencies_root/required_dependencies[2],
  "cuda_cublas":dependencies_root/"cuda/bin/cublas64_12.dll",
  "cuda_cublas_lt":dependencies_root/"cuda/bin/cublasLt64_12.dll",
  "cuda_runtime":dependencies_root/"cuda/bin/cudart64_12.dll",
 }
 runtime_lock={"schema":"ff-suite-portable-runtime-lock/v1","version":"20.2.5","created_at":now,"render_mode":"source_frame_ranges/v1","seconds_only_fallback":False,"files":{name:{"relative_path":str(path.relative_to(ROOT)).replace("\\","/"),"sha256":sha(path),"size":path.stat().st_size} for name,path in runtime_files.items()}}
 runtime_lock_path=ROOT/"runtime-lock.json"; atomic(runtime_lock_path,runtime_lock)
 registry={"schema":"montage-control-three-suite-ff/v20.2","version":"20.2.5","created_at":now,"suite_root":str(ROOT.resolve()),"runtime_lock_relative_path":"runtime-lock.json","runtime_lock_sha256":sha(runtime_lock_path),"dependencies":{"relative_path":"dependencies","python_version":"3.13.15","manifest_relative_path":".manifests/dependencies.json","manifest_sha256":sha(dependency_manifest_path),"tree_sha256":dependency_manifest["tree_sha256"]},"components":{},"mandatory_order":["complete_batch_dependency_closure","original_source_lineage","immutable_semantic_evidence","expected_first_token_alignment","internal_silence_compaction_evidence","semantic_boundary_signal_scan","separate_independent_candidate_review","frame_plan_gate","coalesce_adjacent_same_source","portable_frame_renderer","ffmpeg_controller","output_start_concat_end_evidence","fresh_output_asr","controller_boundary_signal_scan","complete_turn_post_alignment_v20_5","final_encoded_opening_visual_family_gate","separate_independent_post_review","executor_completion_gate"],"render_mode":"source_frame_ranges/v1","adjacent_same_source":"coalesce_monotonic_touching_or_overlapping_frame_ranges","seconds_only_fallback":False,"bypass_policy":"partial batch scope, stale invalid registry, renamed or derived rejected material without original lineage, missing current evidence, waveform-only speech start, first expected token preroll over 3 frames, undeclared or token-overlapping internal silence deletion, partial sentence, any boundary with fewer than 30 stable frames, rounded cut evidence, missing machine signal gate, missing final encoded opening-family gate, ad-hoc task-local rendering, or missing FF receipt is a hard failure","jianying_required":False}
 for name,spec in COMPONENTS.items():
  path,value=manifests[name]; entry={"relative_path":spec["relative_path"],"manifest_relative_path":str(path.relative_to(ROOT)).replace("\\","/"),"manifest_sha256":sha(path),"tree_sha256":value["tree_sha256"],"interface":spec["interface"]}
  if name=="semantic": entry.update({"package_id":spec["package_id"],"version":spec["version"]})
  registry["components"][name]=entry
 registry["project_name"]="codex-montage"
 atomic(ROOT/"suite_registry.json",registry); return registry


def verify():
 registry=json.loads((ROOT/"suite_registry.json").read_text(encoding="utf-8")); failures=[]
 dependencies=registry.get("dependencies",{})
 manifest_path=ROOT/dependencies.get("manifest_relative_path","")
 dependencies_root=ROOT/dependencies.get("relative_path","")
 if dependencies.get("python_version")!="3.13.15" or not manifest_path.is_file() or sha(manifest_path)!=dependencies.get("manifest_sha256"): failures.append("dependencies_manifest")
 else:
  manifest=json.loads(manifest_path.read_text(encoding="utf-8")); current=tree_hash(members(dependencies_root))
  if current!=dependencies.get("tree_sha256") or current!=manifest.get("tree_sha256"): failures.append("dependencies_tree")
 if registry.get("jianying_required") is not False: failures.append("jianying flag")
 if registry.get("render_mode")!="source_frame_ranges/v1" or registry.get("seconds_only_fallback") is not False: failures.append("frame_native_mode")
 runtime_lock_path=ROOT/registry.get("runtime_lock_relative_path","")
 if not runtime_lock_path.is_file() or sha(runtime_lock_path)!=registry.get("runtime_lock_sha256"): failures.append("runtime_lock")
 else:
  runtime_lock=json.loads(runtime_lock_path.read_text(encoding="utf-8"))
  for name,item in runtime_lock.get("files",{}).items():
   path=ROOT/item["relative_path"]
   if not path.is_file() or sha(path)!=item["sha256"]: failures.append(f"runtime:{name}")
 for name,entry in registry.get("components",{}).items():
  manifest_path=ROOT/entry["manifest_relative_path"]; component_root=ROOT/entry["relative_path"]
  if not manifest_path.is_file() or sha(manifest_path)!=entry["manifest_sha256"]: failures.append(f"manifest:{name}"); continue
  manifest=json.loads(manifest_path.read_text(encoding="utf-8")); current=tree_hash(members(component_root))
  if current!=entry["tree_sha256"] or current!=manifest["tree_sha256"]: failures.append(f"tree:{name}")
 return {"schema":"ff-three-suite-verification/v20","ok":not failures,"failures":failures,"suite_root":str(ROOT.resolve())}


if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("mode",choices=("build","verify")); a=p.parse_args(); result=build() if a.mode=="build" else verify(); print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if result.get("ok",True) else 1)
