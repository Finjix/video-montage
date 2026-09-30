# Semantic production contract

This contract applies to each current task. The work order and selected style profile define source scope, requested outputs, duration, permitted speakers, and any explicit batch limits. No previous task's count, length, or approval is a default.

## Source and identity

Bind original media to SHA-256 and processing stage. A source may yield several complete speech candidates; a registry item or a safe interval is not automatically a candidate. A canonical candidate binds original source hash, stage, integer frame range, native FPS, and normalized exact speech. Small timing changes or a new filename do not create a new candidate. Group equivalent propositions into semantic clusters and assign each visible person a stable canonical ID with hash-bound identity evidence.

Derived or cleaned clips must map their selected frame intervals to original-source frame coordinates. User-rejected ranges are immutable task data keyed by original content fingerprint and guarded frame interval. The prelock audit covers every referenced candidate and plan in the complete batch, including unchanged outputs during repair.

## Candidate boundaries

Source ASR and candidate proposals do not grant approval. Decode native in/mid/out frames and PTS, enumerate every frame in the selected boundary windows, inspect at least 0.5 seconds of PCM on both sides, and align the expected transcript to source speech. The first expected token must match and start within three native frames of the selected in-point. Require complete first and final syllables, no residual neighboring phoneme, transient, finger flash, short head/tail shot, or dependent utterance. A gap below 40 ms or zero-gap adjacency demands a clean recut or separately proven bounded cleanup.

The generator's boundary bundle remains `pending_review`. Machine signal scans, forced alignment, and an independent reviewer produce separate hash-bound evidence; a generator-written pass is invalid. A contact sheet helps navigation but cannot replace the original frame hashes. Every segment start also needs source action context before and after the selected frame so a silent tail of a prior gesture cannot masquerade as a complete opening.

## Semantic planning

Use complete spoken units with real answer, explanation, cause, progression, proof, benefit, or payoff relations. A product-name overlap is not a bridge. Preserve connector antecedents, action continuity, pronoun referents, narrative stage, exact dialogue, and visible meaning. Reject proposition resets, repeated mechanism without progress, filler segments, unsupported claims, and closing fragments without an earned payoff. Do not synthesize missing words or substitute a different utterance.

For celebrity live-action work, enforce the selected profile's screen-scope, canonical identity, product visibility, opening composition, CTA, and cast rules. Source-contained camera changes count as separate visual shots when native evidence proves them. A spoken CTA may be used only when it is exact source speech, visually eligible, and advances the proposition; gameplay, end cards, and visual-only CTA are not eligible when the profile excludes them.

Prove inventory coverage and capacity before locking plans. Count approved complete candidates, not source files or registry items. Apply the work order's candidate-use, speaker-share, semantic-cluster, connector, and style-role limits to the whole batch. Canonical ordered-plan and semantic-route signatures enforce output diversity; renamed IDs or boundary jitter do not make duplicate plans unique. Opening visual families are determined by actual first-shot setting, camera, costume, framing, and continuous source shot, not filenames or transcript labels.

One changed or rejected candidate invalidates every dependent plan, adjacent transition, capacity witness, lock, render, and review. Repairs create new task-local rounds. A lock must bind the passing complete-batch gate report, source hashes, candidate fingerprints, style profile, and exact plan hashes.

## Frame rendering and completion

Render only from integer `source_in_frame`, `speech_end_frame`, `source_out_frame_exclusive`, `source_fps_num`, and `source_fps_den` with the bundled frame renderer. Seconds may describe ASR but cannot drive FFmpeg. Adjacent touching or overlapping forward intervals from one source and FPS render as one continuous interval. Never fill time with frozen or interpolated frames, slow speech to satisfy a duration target, or delete valid speech to hide a visual defect.

An internal silence deletion requires forced word/phoneme alignment and explicit protected spoken ranges. Preserve complete neighboring tokens and their natural tails. Uniform gain repair is allowed only for true PCM clipping and cannot change source-frame lineage.

Independent review must watch the selected picture and listen to actual rendered audio at normal speed, recording real review timing and findings. Final encoded outputs need fresh ASR, exact-cut dense frame and PCM evidence, a separate post-encode review, full-batch plan-ID equality, and a passing opening-visual-family release gate. Technical decode, hashes, or a model assertion alone never authorizes delivery.
