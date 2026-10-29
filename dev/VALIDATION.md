# Plugin 0.2.0 verification

Verified on Windows x64, 2026-10-21.

- 28 regression tests cover downloads, SHA-256 validation, source fallback, interrupted-file recovery, concurrent callers, offline reuse, immutable package boundaries, Unicode cache receipts, optional imports, GPU detection and inference fallback.
- Cold bootstrap passed with Python removed from PATH, Windows PowerShell 5.1, a plugin directory containing spaces and Chinese characters, and a separate empty cache. Chinese JSON on stdin, exit codes, subsequent offline runs and an unchanged plugin tree passed.
- CPU PyTorch 2.11.0 executed a matrix multiplication successfully. CUDA PyTorch 2.11.0+cu128 executed the same operation on the NVIDIA RTX 5060. CUDA libraries were reused from the migrated environment after RECORD hash verification; the cold CPU installation used the pinned upstream wheel.
- FFmpeg archive checksum and extracted executable checksums were verified. Pinned Hugging Face configuration/tokenizer files and model sizes matched. A real ModelScope fallback configuration matched its pinned hash. A real GitHub animation-table download and offline reuse passed.
- Ten existing local source videos produced three integration outputs: 21.4 s, 21.15 s and 19.05 s. Planning checks, clean-render QC, packaging and publication passed. Packaging a second time preserved the output hashes. The original packager reported several subtitle cues shorter than 800 ms; these are its existing non-blocking warnings. Generated annotations are test fixtures, not editorial approval of the content.
- Warm `prepare`, unchanged `annotate` and `check` passed with both networking and ASR model preparation explicitly blocked.
- The upload ZIP contains 63 files, approximately 2.72 MiB compressed / 6.76 MiB extracted, with one plugin root and no runtime binaries, model weights, animation tables or bytecode. Manifest fields, icon dimensions, resource references, archive limits and ZIP integrity passed.

The original `dev/` test suite and smoke fixtures were absent in the implementation checkout. The earlier 58-test baseline therefore could not be rerun; the tests above are the available verification for this change.

Reproduce:

```powershell
& ./plugin/scripts/video_montage.cmd runtime --feature packaging --install
# Use the private environment's Python so the optional inference fallback tests run.
& "$env:LOCALAPPDATA/video-montage/environment/Scripts/python.exe" -B dev/run_tests.py
python -B dev/test_bootstrap.py
python -B dev/build_plugin.py
# Requires the repository's local .素材 folder; creates outputs outside the repository.
& "$env:LOCALAPPDATA/video-montage/environment/Scripts/python.exe" -B dev/smoke/e2e.py
```

Uploading the ZIP, developer identity verification and market review remain portal operations; no market submission was performed.

## 2026-10-26 review pass

- 40 regression tests pass under the system Python 3.13 and the plugin's private Python, including the new `test_plan_routes.py` (plan robustness, `routes` clips/joins/draft on a synthetic catalog, incremental batch limits against `batch.audit`, PNG header check, aligner OOM fallback, tolerant token attachment).
- On the existing 10-source smoke catalog: `routes` found 90 cuttable clips and 2967 verified single-celebrity routes in about 6 s; `routes --joins` built a 3-output draft (and a 40-output draft in 1.8 s) that `check` accepted with no batch problems. `render` of the 3-output draft passed QC (-23.18 / -22.96 / -22.66 LUFS); `package` published all three (-23.1 / -22.86 / -22.88 LUFS) in 32 s, and an unchanged re-run took 0.8 s with identical output hashes.
- `init` through the `.cmd` launcher with Chinese JSON on stdin reported the asset pack (15 text pins, 1 disclaimer, 0 audio) and refused a two-source order for the default three-source rule.
- Not exercised here: `prepare` and `annotate` with text corrections (the ASR runtime and models are not cached on this machine), so the aligner-only load and batched alignment are covered by unit tests only.

## 2026-10-27 review pass

- Input: the two Codex runs of 2026-10-26 (batches 142413 and 195854). Both hit `routes` writing `reports/routes.md` before `reports/` existed; parallel `fetch-bgm` calls collided on one temporary file after waiting ~17 min on the dependency lock; every failure showed as exit 1 under `pwsh -Command`; both agents generated joins as a cross product of openings and endings with shared reasons; openings such as 再去砍树 / 为什么呢 passed `check`; a joins change renumbered the plan and re-rendered 36 of 40 videos; 29 animation tables downloaded one at a time inside `package`.
- 67 regression tests pass under the system Python 3.13 and the private Python of the Codex-installed plugin (new: merged detections stay unstable edges, annotation shape errors, CPU aligner OOM raises, subtitle fallback and number/idiom cases, quoted per-join reasons, leaning openings, restated joins, stable plan IDs and the cast line). `dev/test_bootstrap.py` passes again after its fixtures were updated to the current `init` checks (three sources, a PNG header).
- Real footage, `dev/smoke/e2e.py` with the installed runtime cache (ASR on the RTX 5060): init → prepare (10 sources, 238 units, 99 shots) → annotate → check → render (-23.15 / -22.75 / -22.65 LUFS) → package published all three; an unchanged second `package` kept identical hashes.
- `routes` on that catalog, 40 s limit: 39 344 verified routes, 1 563 joins, 73 openings (HEAD before this pass: 48 021 / 1 622 / 76; the difference is the new opening and restatement checks). `routes --joins` with 356 quoted joins built the 3-output draft and printed `cast: 吴樾 2, 林子聪 1, 许君聪 1`.
- `python dev/build_plugin.py` passes (67 files, 3.08 MB ZIP).

## 2026-10-29 nameplate placement

- Input: the 55-video batch in `D:\四明星50条`, where the fixed upper-left plate covered faces. On its 220 nameplate windows (YuNet faces on frames every 12 frames), the old spot touched a face in 51 windows (11 over 10% of the face). The new default picks 174 upper-left, 30 upper-right, 14 lower-left and 2 lower-right; 12 windows touch a face at every spot (at most 6.6%) and get a note: one opener whose speaker leans across the frame and extreme close-ups.
- 91 regression tests pass under the private Python (new `test_nameplates.py`: trimming at frame scale for 1440 and 1080 artwork, spot geometry, placement order, least-covered fallback, cached face checks, configured spots with notes).
- A scratch copy of that job with 6 of its videos: `package` found faces under 24 nameplates in 5.5 s, published all six (-23.0..-22.7 LUFS); the plates are cut to 285x908 and sit at their spots above the subtitles.
- `dev/smoke/e2e.py` on real footage: END_TO_END_PASS (one plate note on P01); `python dev/build_plugin.py` passes (70 files, 3.3 MB ZIP, the YuNet model adds 232 KB).
- Follow-up (user: a plate at the far frame edge sat too far from the speaker, P39): plates now stand beside the face
  on their side and move out only to clear faces and shoulders; one-frame detections are ignored (P39's game art
  had pushed its plate to x 1044, now 564). On the 220 windows: 103 upper-left, 98 upper-right, 17 lower-left,
  2 lower-right; the same 12 windows touch a face at every spot. 93 regression tests pass.
