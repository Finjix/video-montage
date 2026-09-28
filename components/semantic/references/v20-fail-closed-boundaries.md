# V20 fail-closed boundary contract

V20 treats user feedback as executable data, not model memory.

## Candidate identity and invalidation

Every candidate binds `source_sha256`, `source_content_fingerprint`, `source_in` and `source_out`. The invalid-range registry matches content identity, filename family and guarded time overlap; renaming a candidate never bypasses a rejection.

An old job may provide a proposal, never an approval. `actual_asr`, identity, opening-cast, boundary, dense-frame and audio-alignment evidence must live under the current task root and bind the current task, source and interval.

## Evidence state machine

`immutable_generated_pending_review + separate_independent_review -> eligible | rejected`

The generator must write `generator_decision: pending_review`, `decision: pending_review`, no reviewer and no passing alignment. These fields may never be changed. A pass exists only in a separate `candidate-independent-boundary-review/v20.3` artifact that hash-binds the pending bundle, independent-review authority, exact-frame signal scan and forced alignment. A script that mutates the bundle or merely assigns clean booleans is rejected.

For both candidate entrance and exit, enumerate enough exact native frames to prove a 30-frame stable run. Extract PCM by frame-derived sample indexes and bind forced word/phoneme alignment. Recompute clean gaps from sample indexes; never trust a supplied duration. Zero-gap adjacency, a gap below 40 ms, isolated tokens, non-speech transients, incomplete final syllables, missing frames, or an undeclared visual transition before 30 stable frames reject the candidate.

Raw ASR timestamps that place the previous token end and next token start at the same sample do not prove a clean cut. The previous final phoneme may remain audible as a light syllable while impulse detection still passes. Such a raw interval is ineligible. Prefer a different complete utterance; a bounded fade or suppression of at most 40 ms is allowed only with separate forced first-token alignment and isolated-ASR evidence proving the intended first token is complete.

A short alternate shot is not limited to one frame. Any different camera/scene run shorter than 30 frames at the head or tail is a hard failure even when FFmpeg selected the requested source frames correctly. Runs from 30 through 59 frames are microshots: reject them by default unless a separate hash-bound semantic review proves an independent spoken, reaction or action purpose. Evidence generation inspects at least 72 frames on each side so a 36-frame tail cannot hide at the edge of the window. Do not raise a single threshold one incident at a time; preserve the distinction between the 30-frame technical floor and the 60-frame default editorial floor.

Thirty stable frames are not proof that a segment start is editorially complete. For every selected segment, inspect original-source context for at least 1.0 second before its in-point and 1.5 seconds after it, and review approximately the first 1.5 seconds after every encoded concat boundary. Find the first spoken token by forced alignment of the candidate's expected transcript; background music, room noise, impacts and generic waveform energy are not speech-start evidence. Keep at most three native frames of natural mouth-onset preroll. If the candidate starts inside an existing source shot or the leading run is merely a silent tail of a prior gesture, person, action or camera beat, reject it regardless of length. The evidence must use `segment-start-action-context-evidence/v1`, bind the original source SHA-256, reviewed original-frame set and first aligned token/sample, and explicitly attest complete start action and semantic function. A clean intermediate file or renamed candidate never replaces original-source coordinates.

## Dependency and release

One rejected candidate revokes every dependent plan in the complete declared batch, including outputs not otherwise changed in a repair. Every referenced candidate must exist in the current inventory and carry current-task evidence. Repeat the same exact-frame, 30-frame stable-run and PCM checks after final 60fps encoding at the output start, every concat cut, and the output end. Post evidence derives cut frames from renderer evidence, never rounded seconds. Technical decode, dimensions, fps, codec, compact sheets and model-written booleans cannot substitute for the machine signal gate plus hash-bound independent review.
