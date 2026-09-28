from __future__ import annotations
import hashlib, importlib.util, json, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("pool",ROOT/"scripts"/"v14_candidate_pool.py")
pool=importlib.util.module_from_spec(SPEC);assert SPEC.loader;SPEC.loader.exec_module(pool)
def write(path,x): path.write_text(json.dumps(x),encoding="utf-8")
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
class CandidatePoolTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.d=Path(self.tmp.name)
  self.source=self.d/"s.mp4";self.source.write_bytes(b"s")
  self.files=[]
  for n in ("asr","identity","cluster","in","mid","out"):
   p=self.d/(n+".json");p.write_bytes(n.encode());self.files.append(p)
  self.manifest=self.d/"sources.json";write(self.manifest,{"sources":[{"source_id":"s1","source_sha256":digest(self.source),"processing_stage":"clean","source_path":str(self.source)}]})
  self.profile=self.d/"profile.json";write(self.profile,{"profile":"p/v260928"})
  self.inventory=self.d/"inventory.json";c={"candidate_id":"c1","candidate_status":"approved","capacity_input_eligible":True,"source_id":"s1","source_sha256":digest(self.source),"processing_stage":"clean","actual_asr_path":str(self.files[0]),"actual_asr_sha256":digest(self.files[0]),"identity_evidence_path":str(self.files[1]),"identity_evidence_sha256":digest(self.files[1]),"semantic_cluster_review_path":str(self.files[2]),"semantic_cluster_review_sha256":digest(self.files[2]),"frames":{n:{"path":str(self.files[i+3]),"sha256":digest(self.files[i+3])} for i,n in enumerate(("in","mid","out"))}};write(self.inventory,{"candidates":[c],"source_coverage":{"s1":{"status":"audited"}}});self.pool=self.d/"pool.json"
 def tearDown(self): self.tmp.cleanup()
 def test_freeze_then_reuse_unchanged_source(self):
  self.assertEqual(0,pool.main(["freeze","--source-manifest",str(self.manifest),"--inventory",str(self.inventory),"--style-profile",str(self.profile),"--output",str(self.pool)]));plan=self.d/"plan.json";delta=self.d/"delta.json";self.assertEqual(0,pool.main(["plan","--source-manifest",str(self.manifest),"--style-profile",str(self.profile),"--pool",str(self.pool),"--delta-source-manifest",str(delta),"--output",str(plan)]));self.assertEqual(1,pool.load(plan)["reusable_candidate_count"]);self.assertEqual(0,pool.load(delta)["source_count"])
 def test_changed_source_requires_incremental_rebuild(self):
  pool.main(["freeze","--source-manifest",str(self.manifest),"--inventory",str(self.inventory),"--style-profile",str(self.profile),"--output",str(self.pool)]);self.source.write_bytes(b"changed");m=pool.load(self.manifest);m["sources"][0]["source_sha256"]=digest(self.source);write(self.manifest,m);plan=self.d/"plan.json";delta=self.d/"delta.json";pool.main(["plan","--source-manifest",str(self.manifest),"--style-profile",str(self.profile),"--pool",str(self.pool),"--delta-source-manifest",str(delta),"--output",str(plan)]);self.assertEqual(["s1"],pool.load(plan)["rebuild_source_ids"])
