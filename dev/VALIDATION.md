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
