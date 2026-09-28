# FFmpeg controller workflow

v260928 validation also requires a hash-bound `opening-visual-family-release-gate/v260928` for the exact delivery manifest. It rechecks at least 60 encoded opening frames per output, the hard family-use ceiling and the opening-plus-second-family ceiling; plan labels alone are insufficient.

V20.2.4 final review requires `ffmpeg-post-encode-qc/v260928`. At every output start and concat cut, the alignment artifact must bind fresh ASR for the exact encoded file, match the expected and observed first token, and prove no more than three native frames of preroll before that token. It must reject waveform-only speech starts, residual fillers/phonemes, non-speech transients, incomplete previous final tokens, partial turns and mid-word tails. Technical impulse and frame scans cannot waive these speech checks.

V20.2.4 requires complete-batch post-encode evidence at every output start, concat cut, and output end. Thirty stable frames are the technical floor; unreviewed 30–59 frame microshots fail closed, so the default editorial stability requirement is 60 frames. Extract at least 72 frames on each side of every cut. A delivery subset whose plan IDs do not exactly equal the locked-index plan IDs fails before evidence generation.

The premaster manifest must declare `render_mode: source_frame_ranges/v1` and `seconds_only_fallback: false`, and every result must carry the same render mode. Reject legacy premaster files produced by second-based source trimming. The bundled semantic component owns `portable_frame_renderer.py` and `frame_range_repair.py`; neither `finalize` nor validation may substitute another renderer.

This controller replaces Jianying only inside the FF three-component suite. It requires a passing V20 semantic release authorization before finalization.

Run `components/controller/scripts/ffmpeg_controller.py preflight`, then `finalize`. Every premaster item must hash-bind its renderer evidence. Build exact-frame final-file evidence with `components/controller/scripts/post_encode_evidence.py`, run `components/controller/scripts/boundary_signal_scan.py`, then require a separate hash-bound independent review through `components/controller/scripts/review_post_encode.py`. Run `validate` last.

Never add BGM, captions, overlays or extra audio unless the current user explicitly asks. Default output is H.264/AAC, 1440×2560, exact 60/1 CFR. Refuse overwrites and stale partial files.

Technical decode is not editorial QC. A final receipt requires every exact output frame ±0.4s and frame-derived PCM ±0.5s at every renderer-declared cut, a passing machine signal report, and a hash-bound independent review of the original frame set. The machine gate rejects any extra visual transition within 12 frames of the intended cut, including a 2–12 frame short insert. It scans an audio window around the cut rather than one nominal PCM sample. Compact sheets cannot authorize release.

Use the suite root's `dependencies/ffmpeg/bin`, `dependencies/python/python.exe` with its local modules, and the complete faster-whisper model under `dependencies/models`. Run `components/controller/scripts/runtime_paths.py` with that Python to resolve them. A preflight must not fall back to system FFmpeg or Python.
