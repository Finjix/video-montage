# FFmpeg controller workflow

Use the bundled Python, FFmpeg, FFprobe, and Whisper model. Run controller `preflight`, then `finalize`, create exact-cut evidence, obtain separate independent post-encode review, and run `validate` last. A passing full-batch semantic release authorization is required before finalization; every premaster item must hash-bind its renderer evidence.

The premaster manifest and every result must declare `render_mode: source_frame_ranges/v1` and `seconds_only_fallback: false`. The controller accepts only the bundled portable frame renderer and frame repair tools. Refuse overwrites, stale partial files, incomplete plan sets, and delivery subsets whose plan IDs differ from the locked index.

At every output start, concat cut, and output end, bind dense decoded frames, frame-derived PCM, machine signal findings, and an independent review of the original evidence. Inspect at least 72 frames on both sides of each cut. Thirty consecutive stable frames are a technical floor; a 30-59-frame microshot needs separate semantic justification, while an unreviewed shot needs at least 60 stable frames. Reject extra transitions within 12 frames of an intended cut.

Fresh ASR of each encoded file must match the expected first token, prove no more than three native frames of natural preroll, and reject residual phonemes, non-speech transients, incomplete turns, or mid-word tails. Waveform energy alone is not speech-start evidence. The opening-visual-family gate must inspect at least 60 encoded opening frames per output and bind the exact delivery manifest; plan labels do not authorize it.

Default output is H.264/AAC, 1440x2560, exact 60/1 CFR. Do not add music, captions, overlays, or extra audio unless the current user asks. Technical decode and output hashes are necessary but never replace editorial QC.
