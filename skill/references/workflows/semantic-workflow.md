# Semantic workflow

This contract applies to the legacy executor route selected by `SKILL.md`.

Run every production or repair task through the `video-montage` skill and the bundled executor with `assets/dependencies/python/python.exe`. Follow the executor state machine; do not substitute system Python, task-local render scripts, manually edited plans, or direct delivery-folder replacements.

Read the [semantic contract](../semantic/semantic-contract.md) and the selected style profile before planning. The current work order determines output count, duration, source scope, and style. Do not inherit a fixed duration, source count, or approval from another task.

## Source and candidate evidence

- Bind each original source to SHA-256, processing stage, native frame rate, exact speech, visible-person identity, and audited source scope. Derived media must map selected frames back to the original source hash and frame coordinates.
- Source ASR and candidate proposals are provisional. Promote a candidate only after hash-bound native decoded frames, decoded PTS, exact-source speech, frame/audio boundary windows, identity, and semantic evidence are complete. A UI screenshot or synthetic image is not native frame evidence.
- Every production segment needs integer `source_in_frame`, `speech_end_frame`, `source_out_frame_exclusive`, `source_fps_num`, and `source_fps_den`. Seconds are metadata, never render authority.
- A source used again after intervening footage must never replay frames already used in that output. The cut-smoke review flags every such source return for a visual check against the earlier shot, including scene, costume, pose, and action continuity. Adjacent forward intervals from the same source may still coalesce.
- User-rejected source ranges are append-only, keyed by original content identity and frame interval. Audit the complete declared batch before plan lock. Renaming a file or candidate, using a derived clip, or replacing only part of a batch cannot bypass a rejection.
- Generated boundary evidence remains `pending_review`. A separate reviewer must bind the exact evidence, forced alignment, dense video frames, and PCM windows. One failed candidate invalidates every dependent plan.

## Planning and rendering

Plan only complete, independently intelligible speech units. Preserve connector prerequisites, real cause or answer relations, narrative progression, visible action, and source-contained speaker turns. A shared keyword, repeated product name, or model-written `pass` is not semantic proof.

Use a declared style profile and prove candidate capacity, speaker/cluster limits, ordered-plan uniqueness, route diversity, and opening/closing diversity for the entire requested batch. Lock plans only after the exhaustive invalid-range audit and independent candidate review pass. Plan changes invalidate adjacent transitions, capacity witnesses, hashes, and downstream approvals.

Render through the bundled portable frame renderer. Coalesce touching or overlapping forward intervals from the same source and frame rate into one continuous source interval. Never use seconds-only trimming, frozen-frame padding, interpolation, or deletion of valid spoken frames to conceal a visual defect. Internal silence removal requires word/phoneme alignment proving the deleted frames contain no speech and preserves complete neighboring syllables.

## Release

Review the actual rendered audio at normal speed and the selected picture at every boundary. The independent review records real playback duration, start/end times, segment findings, and source-bound hashes. A failed output is repaired in a new round; previous approvals do not transfer to changed media.

The final encoded files require fresh ASR, first-token alignment, dense cut-frame and PCM evidence, independent post-encode review, and a passing opening-visual-family gate. Plan-time labels or technical decode alone cannot authorize release. Completion requires the full-batch semantic delivery manifest, controller validation, and post-encode QC receipts.

Task evidence belongs in the task directory. Never upload or publish, delete original sources, call an external model, or bypass a gate without separate current-user authority.
