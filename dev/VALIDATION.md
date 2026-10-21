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
