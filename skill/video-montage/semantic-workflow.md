# Semantic workflow

Execute this package through the `video-montage` skill, using the suite root's `dependencies/python/python.exe` and its `components/executor/scripts/three_suite_ff.py` executor. Do not invoke a system Python or install modules during a production run.

## v260928 internal-gap and final-opening release closure

Read [the internal silence compaction and final visual review contract](../../components/semantic/references/v20-internal-silence-compaction.md) for any mid-turn breath-gap removal, PCM clipping repair or opening-diversity release. A noncontiguous same-source continuation marked as silence removal must carry hash-bound `internal-silence-alignment-evidence/v260928`; the frame-plan gate rejects undeclared or token-overlapping deletions. Uniform gain repair is allowed only for true PCM clipping and must preserve exact source frames and lineage.

Final controller validation requires a passing `opening-visual-family-release-gate/v260928` bound to the exact delivery manifest and at least 60 encoded opening frames per output. Plan-time visual-family labels alone cannot authorize release.

## V20.2.4 complete-folder regression closure

For Wu Yue production or repair, read [the complete folder regression gate](../../components/semantic/references/wuzimu-v20-folder-regression-gate.md). It consolidates every failure observed in the 40-output folder: stale bad-candidate reuse, derived-file lineage bypass, microshots/frozen frames, residual phonemes and breaths, partial turns, semantic resets, cast violations, CTA placement, duration inheritance, route repetition and stale final review.

Every derived or cleaned source must carry current-task `source-lineage-evidence/v260928` binding its selected derived frames back to original-source SHA-256 and original frame coordinates. Candidate fingerprints that embed an original hash/range are parsed as lineage; renaming or moving the intermediate file cannot bypass a registered bad interval. The first aligned expected token must match the candidate text and begin no more than three native frames after the selected in-point. Final release requires `ffmpeg-post-encode-qc/v260928`, including complete-turn speech-start alignment, fresh output ASR and no residual or transient token.

All new production and repair runs must enter through `video-montage`. Task-local render scripts, manual plan mutation and direct delivery-folder replacement are diagnostic-only and cannot authorize release.

Opening repetition is measured by the actual first-shot visual family—setting, camera, costume, framing and continuous source shot—not by `candidate_id`, filename or transcript. For the Wu Yue contract, one opening visual family may appear at most three times in the complete batch; there is no fourth-use exception. The opening-plus-second visual-family pair may appear at most twice. A 40-output batch must prove at least fourteen eligible opening visual families before plan lock.

## V20.2.3 full-scope rejection hardening

Every selected source boundary and every encoded output start, end, and concat boundary must prove at least 30 consecutive stable frames, but 30 is only the technical floor. A 30–59 frame visual run is a microshot and fails closed unless a separate, hash-bound semantic review proves an independent spoken, reaction or action function; the default unreviewed stability requirement is 60 frames. Every planned segment start—not only the finished-video opening—must also bind original-source action-context evidence covering at least 1.0 second before the in-point and 1.5 seconds after it. Speech start comes from forced alignment of the expected transcript, never raw waveform energy; allow at most three native-frame preroll before the first aligned token. A candidate that begins inside an earlier source shot or retains a silent gesture/action tail is rejected regardless of whether that tail lasts 13, 36, 60 or more frames.

A user-rejected source range is append-only data keyed by source SHA-256/content fingerprint plus native frame/time interval. Prelock audit must cover every plan and every referenced candidate in the complete declared batch. A partial repair manifest, a renamed candidate, an unchanged-output exemption, or an inherited approval cannot bypass the registry. Semantic completion must hash-bind the packaged current registry and a passing exhaustive V20 prelock audit.

## V20.2.2 independent-evidence hardening

Generator output is immutable and must remain `pending_review`. Never change a generated boundary bundle to `pass`, add a reviewer to it, or embed a passing alignment inside it. Run `components/semantic/scripts/v20_boundary_signal_scan.py` on the exact-frame bundle, then run `components/semantic/scripts/v20_review_boundary.py` with a separate forced-alignment artifact and a separate independent-review authority. The candidate inventory must hash-bind the pending bundle, signal scan, alignment and `candidate-independent-boundary-review/v260928` receipt.

Clean gaps are calculated from `selected_start_sample`, `selected_end_sample_exclusive`, `first_voiced_sample` and `last_voiced_sample_exclusive`; a claimed decimal duration or copied `0.05` value has no authority. A gap below 40 ms, any non-speech transient before the first word, or any residual token rejects the candidate.

When the previous spoken token and the selected first token are zero-gap or overlap, never cut directly on raw ASR timestamps: the previous final phoneme can survive as a light syllable even without an impulse peak. Use a different complete source unit, or a bounded start cleanup of at most 40 ms only when forced alignment and isolated ASR prove the intended first token remains complete and no prior token survives.

The source visual gate rejects a camera/scene transition within 12 frames after an in-point or before an out-point. This covers a 2–12 frame short head/tail shot even when every frame is source-native. Post-encode QC applies the same multi-frame-run rule around each exact rendered cut. Compact contact sheets are navigation aids only; a pass binds every original evidence-frame hash.

## V20.2 frame-native rendering contract

Before rendering or repairing media, read [the frame-native rendering contract](../../components/semantic/references/frame-native-rendering-v20.2.md). Final editing is authorized only by decoded frame indexes. Every rendered segment must provide `source_in_frame`, `speech_end_frame`, `source_out_frame_exclusive`, `source_fps_num` and `source_fps_den`; a plan carrying only `source_in/source_out` seconds fails closed. Seconds may remain as ASR metadata but never drive FFmpeg.

Use the bundled `components/semantic/scripts/portable_frame_renderer.py`; never point `portable_renderer` at a task-local or historical script. It selects video with frame index expressions and derives audio sample boundaries from the same frame indexes and source FPS. For an already encoded exact-60 output, use `components/semantic/scripts/frame_range_repair.py` with inclusive frame ranges. Never use time-range deletion, `tpad`, cloned/frozen frames, interpolation, or a local hold to fill duration.

Output-frame deletion must bind a passing forced speech/phoneme alignment artifact and enumerate protected spoken frame ranges, including every complete final syllable and its validated natural tail. Reject any deletion that intersects a protected range. When a visual defect occurs during valid speech, recut a clean continuous source-frame interval; never delete the spoken frames and audio together merely to clean the picture.

Before building FFmpeg inputs, coalesce adjacent segments that use the same source, FPS and speed when their source-frame order moves forward and the ranges overlap or touch. Render the union as one original-source interval with one continuous audio trim. Never concat those candidates independently. Do not coalesce reverse-order selections, speed changes, or ranges separated by unselected source frames.

The portable checkpoint dependency is bundled as `components/semantic/scripts/winky_ledger.py` and writes only to the configured task-local ledger root. Missing bundled renderer/ledger files, a hash mismatch, or an unresolved placeholder is a hard preflight failure.

## Non-negotiable V20 boundary contract

Read [the V20 fail-closed contract](../../components/semantic/references/v20-fail-closed-boundaries.md) before building candidates. Candidate names and versions never override user feedback. Every user-rejected range is keyed by source content identity plus time interval and guard band in [the invalid interval registry](../../components/semantic/references/wuzimu-v20-invalid-intervals.json).

Old tasks may supply text or timing hints only. Evidence files, approval decisions, ASR bindings, identity records, boundary reviews and final-QC receipts must be generated inside the current task and bind the current source SHA-256/content fingerprint. Any legacy evidence path is a hard failure.

Evidence generators emit `pending_review`; they cannot emit `pass`. A V20 candidate becomes eligible only after independent, content-derived review proves all native frames in both ±0.4 second windows were enumerated, both ±0.5 second PCM windows were inspected, forced alignment found no isolated leading/trailing token, and the final syllable is complete. A zero-gap adjacency to another spoken token is high risk and cannot pass without a clean recut.

One failed candidate invalidates every dependent plan. After final 60fps encoding, repeat dense boundary and PCM review on the delivered files. Technical decode, dimensions, frame rate and hashes are necessary but never sufficient for release.

Run `components/semantic/scripts/v20_fail_closed.py audit-request` before the ordinary semantic lock. V20 release is valid only when the semantic lock, independent Sol review, FF controller report and post-encode V20 QC all pass.

## V19 user-feedback repair contract

For new Wu Yue work, read [the V19 feedback repair gate](../../components/semantic/references/v19-feedback-repair-gate.md) and bind [the V19 Wu Yue contract](../../components/semantic/references/wuzimu-v19-feedback-contract.json). These supersede the V18 Wu Yue defaults for new jobs without altering frozen V18 records.

Output count and duration are current-work-order parameters, never package-wide defaults. Enforce a duration range only when the current user or hash-bound work order explicitly declares one; otherwise preserve natural source duration and never slow, pad or add filler merely to hit a historical length. Planning must prove enough valid ordered combinations for the requested batch and enforce candidate, exact-text, opening, closing and route diversity across it. A user-passed prior output may be reused only after its exact source ranges, final audio and visual boundaries pass the current V19 rules.

Exact selected frame/audio windows, not isolated in/mid/out stills, decide boundary safety. One contaminated reused candidate invalidates every dependent plan until the candidate is recut and re-evidenced. Do not synthesize or substitute dialogue: plan text must match the isolated source speech. `so easy` is forbidden by default and never substitutes for a missing `简简单单` or `轻轻松松` take.

Visual meaning is part of semantic meaning. A supporting performer may never appear without the celebrity anywhere in the selected interval. Generic status questions such as `你们这周收了几个幸存者` are forbidden. A standalone `我是无尽冬日明星玩家吴樾` opening is forbidden; it may appear only inside an immediately connected first-person game-experience chain. Dependent opening words such as `但/但是` must be removed unless the selected antecedent truly supports the contrast.

Spoken CTA is no longer globally excluded. `视频下方进入`, `左下角点进去`, `无需下载点击即玩`, and source-grounded gift-code/benefit lines may serve in the middle or close when celebrity live action remains visible, the CTA is exact source speech, and it advances the current proposition. Full-screen gameplay, end cards, QR-only screens and visual CTA without the celebrity remain excluded.

Count same-source camera/angle/shot changes as separate visual shots when native-frame evidence proves the change. For each final edit, the longest and shortest visual shots may differ by at most about three seconds, and the final shot must be a complete, useful unit rather than a short slogan fragment.

## V18 advertising-close and truthful-review correction

The V18 advertising-close gate and Wu Yue contract remain lineage references only. New Wu Yue jobs bind the V19 feedback contract above; V18 rules apply only where V19 does not replace them.

Every segment must carry a nondecreasing `purpose_contract.narrative_stage`. A later-state achievement may be an opening hook only when an explicit how/why bridge precedes the return to mechanics. The last segment must carry an earned closing function, `closing_payoff=true`, and an exact `closing_quote`. Numeric anecdotes and method-only fragments never satisfy the closing gate.

Final Sol review uses `semantic-sol-output-review/v260928` and records model provenance, actual audio duration, normal-speed listened duration, and start/end timestamps. The wall-clock review interval and listened duration must each cover the audio duration. Template-identical booleans or an impossible review duration are hard failures.

## Scope and authority

This package is a frozen, append-only analysis and planning-gate baseline. It is read-only unless the current user explicitly authorizes a named source analysis, feedback item, correction, or training snapshot. A permitted update creates exactly one new dated, hash-bound record; it never changes a prior record.

Within a current-user-authorized work order, V18 may orchestrate local ASR, evidence extraction, Terra candidate/identity/semantic/planning phases, locked-plan rendering, Sol final QC, bounded repairs and copying release-authorized outputs into a delivery directory. It may not upload, publish, delete sources, bypass a gate, or call an external Winky model without separate current-user authorization.

## V15 content-grounded semantic correction retained by V18

Before planning or reviewing any celebrity product montage, read [the V15 content-grounded montage gate](../../components/semantic/references/v15-content-grounded-montage.md) and bind a `policy.content_grounding_contract`; use [the Wu Yue contract](../../components/semantic/references/wuzimu-v15-content-contract.json) for this source family. The executable checks live in `components/semantic/scripts/v15_content_gate.py` and are called by `v9_gate_runtime.py` before any plan is locked.

V15 rejects a plan when the product is absent or late, a supporting-cast-only opening lacks the narrow substantive-question exception, a short segment only echoes prior meaning, pronouns point to the wrong entity, narrative time regresses, a complaint skips its natural answer branch, a boundary carries unexplained filler, or one segment dominates a short edit. A relation label, cluster change, proposition ID, review-file hash or model-written `pass` never substitutes for quote-grounded semantic evidence.

Final Sol review must use `semantic-sol-output-review/v260928`, listen to the actual rendered audio at normal speed, provide segment findings, and pass every required content check.

## V18 foreground execution

Before production, read [the V18 foreground/watchdog contract](../../components/semantic/references/v16-foreground-watchdog.md) and use [the foreground start prompt](../../components/semantic/references/foreground-start-prompt.md). Normal production runs `components/semantic/scripts/v15_orchestrator.py run-continuous` with no positive budget. It stays in the active Codex turn and continues every exit-22 slice immediately until a model handoff, completion or genuine blocker. A command such as `--budget-seconds 55` is rejected in normal mode; bounded execution is available only with `--recovery`.

The foreground process owns `.v18/foreground_lease.json` and renews it after every durable slice. A scheduled task uses [the watchdog-only heartbeat prompt](../../components/semantic/references/heartbeat-prompt.md): it runs `watchdog`, stays quiet while the lease is live or progress is recent, and starts a bounded recovery slice only after both foreground ownership and progress are stale. The existence of a scheduled-task card is not production cadence.

The foreground executor also writes `.v18/front_status.json`. It provides `started_at`, computed elapsed time, phase and durable progress for a host UI to display while work is running. Reading this file is observational only: it must not send a new task message, start a second executor, revoke the lease or interrupt an active worker.

Initialization must bind `job_contract.style_profile_id` to the selected profile file and must bind an explicit witness or exhaustive source manifest whose declared and actual counts match `source_scope_total`. Do not silently select the historical V10 profile for a celebrity live-action job and do not use 184 sources merely because an older manifest contains 184. Source ASR uses 24-item local chunks by default to reduce repeated model-load overhead; the foreground loop starts the next chunk immediately. Rendering remains rolling groups of five.

For the learned celebrity真人开头, select [the V14 live-action profile](../../components/semantic/references/celebrity-live-action-opening-v14.json). It preserves V11 semantic and visual gates while adding the V14 execution contract.

## V14 native candidate-frame evidence

Read [the V14 source-frame evidence contract](../../components/semantic/references/v14-source-frame-evidence.md) before any candidate becomes eligible. `SOURCE_ASR` yields source-bound text and timings; `CANDIDATE_PROPOSAL` may nominate complete-utterance intervals but may not approve them. `SOURCE_FRAME_EVIDENCE` then uses `components/semantic/scripts/v14_source_frame_evidence.py` to produce hash-bound in/mid/out frames and decoded PTS from FFmpeg. Only `CANDIDATE_BUILD` may promote a proposal with completed evidence.

Never accept WPF, UI thumbnail, browser screenshot or synthetic image output as native frame evidence. Repeated frame pixels are not automatically a failure: a held source shot can be legitimate. Reject a frame only when decoding fails, the decoded PTS exceeds tolerance, its hash/path binding fails, or the visual gate rejects its actual content.

## V14 frozen candidate pool and incremental reuse

Use `components/semantic/scripts/v14_candidate_pool.py freeze` only after a candidate inventory is fully evidenced. It freezes only approved candidates whose ASR, identity, semantic and in/mid/out frame files still match their recorded hashes. The pool records each source SHA-256, processing stage, style-profile hash and audit coverage.

For a later batch, use `plan` with the current source manifest and style profile. Only sources absent from the pool, changed in SHA-256 or processing stage, lacking auditable coverage, or evaluated under a different profile hash enter its delta source manifest. Reusable candidates keep their hash-bound evidence; a rule or profile change never silently reuses them. The plan is an input to the foreground executor, not a second executor, and it cannot interrupt a live task.

## V11 celebrity live-action opening lineage retained by V14

V14 preserves V11's learned celebrity真人开头 semantics and evidence formats. The V11 references remain lineage evidence; new production selects `celebrity_live_action_opening/v260928` and the V14 foreground contract above.

This profile learns only the live-action celebrity opening. The first frame where gameplay becomes the dominant full-screen visual is a hard end boundary. Full-screen gameplay, later gameplay narration, end cards and CTA are outside scope. A handheld phone or game inset remains eligible only while live action is visually dominant.

The same canonical person may be adjacent when the exact transition proves both a meaningful visual-performance change and spoken information gain. A blanket adjacent-same-person rejection is wrong for this profile. Conversely, a changed file name, crop or subtitle is not a performance change. Single-star and two-star work orders do not use the generic 35 percent speaker-share ceiling; their diversity is enforced through exact candidates, text, semantic clusters, ordered plans and pairwise overlap.

Every selected candidate binds structured live-action-scope, CTA and canonical-identity evidence. Every transition uses a permitted relation, explicit information gain and proposition progress. `A -> B -> A` requires a structured answer, counterpoint, continued-round, escalation or payoff reason. Complete 0.8--3.0 second reactions are allowed only as `short_reaction`, never as a substitute for the performance anchor.

V15 keeps V9-shaped lock/runtime artifacts for renderer compatibility, but binds them to `semantic-analysis-training-backup-v15` and adds the V15 content-grounded gate before locking. `witness_sufficient` may use partial or witness-complete scoped sources when every selected candidate is fully evidenced and the global witness proves the requested plans plus replacement margin.

## V10 continuous rolling lineage retained by V14

V14 retains V10's immediate exit-22 continuation and five-item rolling behavior, but replaces V11's optional short foreground budget with the enforced unbounded foreground and lease-backed watchdog contract above.

Globally solve and witness all requested plans before rendering, then render and review rolling groups of five. Freeze clean numbered outputs, quarantine failed numbers, and re-author those numbers immediately without blocking later clean groups. Completion requires every requested index plus the final delivery manifest.

For the accepted historical 25--30 second style, use [the balanced low-repeat profile](../../components/semantic/references/historical-low-repeat-balanced-v10.json): witness-sufficient inventory with 20% replacement capacity, candidate/text cap 6, semantic-cluster cap 12, no fixed one-use semantic-route cap, ordered-plan uniqueness, pairwise candidate/text overlap <=1 and trigram similarity <=0.88. Canonical identity, complete actual speech, CTA, hard text, source/output boundaries, real semantic progress and Sol final QC remain hard.

Read [the V10 continuous rolling contract](../../components/semantic/references/v10-continuous-rolling.md). V9's heartbeat-slice behavior remains the recovery fallback, not the foreground production cadence.

## V9 heartbeat-slice correction

V9 supersedes V8's detached-background-worker mechanism. Production uses `components/semantic/scripts/v15_orchestrator.py`; long local phases are `chunked_local` commands that run synchronously for one bounded slice, atomically save progress, exit, and continue on the next heartbeat. No phase depends on a child process surviving the Codex turn.

Exit code 22 means a slice completed or made durable progress and is not a failure. A timeout or abnormal exit that increased the declared progress count is recoverable and does not consume the business retry budget. Source and output ASR use `components/semantic/scripts/v9_source_asr.py`, preflight CUDA libraries before loading, fall back to CPU inside the same slice, process at most the configured item count, and return 75 while more items remain.

Production configuration must include an active `semantic-heartbeat-registration/v260928` and a working Winky `checkpoint_command`; initialization rejects their absence. The active gate, evidence, render and delivery entrypoints are `v9_gate_runtime.py`, `v9_cut_smoke.py`, `v9_batch_render.py`, `v9_output_evidence.py`, and `v9_deliver.py`. Their V9 schema identifiers supersede V8 identifiers below.

Read [the V9 orchestration contract](../../components/semantic/references/v9-heartbeat-slice-orchestration.md) and [the V9 gate contract](../../components/semantic/references/v9-gate-runtime.md) before production. Only `semantic-delivery-manifest/v260928` is completion.

## Autonomous orchestration is mandatory

Before starting a production job, read [the V8 autonomous orchestration contract](../../components/semantic/references/v8-autonomous-orchestration.md), copy and bind [the pipeline template](../../components/semantic/references/pipeline-config.template.json), and create a task heartbeat using [the heartbeat prompt](../../components/semantic/references/heartbeat-prompt.md). V8 initialization must reject a production config whose required heartbeat registration is missing.

`components/semantic/scripts/v15_orchestrator.py` owns the full phase chain. Local work is bounded to one heartbeat slice; model phases create a hash-bound pending action for the Codex heartbeat. The heartbeat continues until `COMPLETE`, exhausted retries, a true capacity conflict, or required new authority.

The orchestrator supports `init`, `resume`, `status`, and `supply-model`. It binds the config hash, stores every artifact hash in task-local state, records progress, retries a local phase only within its declared budget, supports one-load ASR with CUDA-to-CPU fallback, and performs bounded repair rounds in new round directories.

## Executable runtime is mandatory

The executable gate contract is defined in [the V9 gate reference](../../components/semantic/references/v9-gate-runtime.md). V9 uses `components/semantic/scripts/v9_gate_runtime.py` and V9 schema identifiers. Prose or a model assertion cannot replace its exit code and output hashes.

- Run `gate` on one complete batch lock request. Exit code 0 creates a passing gate report, hash-bound locked plan files, and `locked_plan_index_v9.json`. Any failure creates only a rejection report and no locked index.
- A renderer must run `verify-render` immediately before every FFmpeg invocation and may accept only the exact plan named and hashed by the locked index. Direct rendering from an ad-hoc plan path is forbidden.
- Run `verify-output` on the complete independent output-QC report. Exit code 0 creates a release-authorization manifest; only exports listed in that manifest may enter a delivery directory.
- V8 orchestration state supersedes the standalone V7 gate state for production jobs; each phase transition still requires named hash-bound artifacts and writes a dated backup.

The gate and orchestrator use the Python standard library. ASR uses a configured local faster-whisper installation and loads one model instance per phase. Relational checks, hashes and nonzero failure exits remain mandatory even when a model has reviewed the same material.

## Source-analysis baseline

Bind every source to `SHA-256 + processing_stage`; a filename is not evidence. Preserve exact speech/timecodes, safe boundaries, proposition, editorial role, information gain, connector prerequisites, speaker and visible-person identity, visual/action contract, risk codes, and status. A candidate cannot be approved merely because its intended transcript looks complete: its isolated actual-ASR text, opening boundary and closing boundary must each attest the same independently intelligible utterance.

Use only real adjacent relations: answer, explanation, cause-effect, condition-result, progression, proof, benefit, or payoff. Product-name overlap alone is not a bridge. A completed mechanism that jumps to social proof or a new product introduction is `PROPOSITION_RESET` unless a spoken bridge is selected.

Mark material `scenario_bound` when its action, state, character, place, or timeline cannot be fragmented without losing meaning. Technical cleanliness does not make scenario-bound material modular.

Connectors require their spoken antecedents: `因为` explains a stated result; `所以/这下` consume a completed cause; `然后/接着/随后` continue one event state; `但/不过` reverse a stated expectation; and `只要` keeps its condition-result pair. CTA is never a bridge. Do not waive incomplete speech, residual phonemes, hard-text/person tails, CTA/end-card conflicts, stutter, invented frames, unfinished action, or unverified speaker identity.

## Per-edit speaker sequence gate

For fast mixed edits, record the visible speaker identity and source-family label for every candidate.

- A `performance_anchor` is an on-camera, source-contiguous complete turn of 7.0--14.0 seconds. It is approved when isolated actual ASR and native in/mid/out evidence prove an independently intelligible opening, a complete closing, one visible speaking person, and no residual neighbouring voice. It must not be split solely to raise cut count.
- A contiguous source interval over 14.0 seconds is `conditional`; split it only at complete speech units or use a non-fast workflow. A 7.0--14.0 second anchor is not an `OVERLONG_CONTIGUOUS_SOURCE` failure.
- A `response_or_detail` is normally 3.0--7.0 seconds. A shorter reaction is allowed only when it is a complete, directly responsive utterance and its in/out boundaries are attested; it cannot be used as a vague bridge or an independent narrative pillar.
- Adjacent intervals with the same visible speaker are rejected by the generic profile. The V11 celebrity live-action-opening profile allows them only with hash-bound visual-performance change evidence and semantic information gain.
- `A -> B -> A` is `conditional` until the handoff states a concrete story reason, such as an answer or explicit payoff. Keyword overlap and product repetition are not reasons.
- Each speaker switch must add named information. Otherwise use `SPEAKER_SWITCH_WITHOUT_INFORMATION_GAIN`.

Use the risk codes `OVERLONG_CONTIGUOUS_SOURCE`, `ADJACENT_SAME_SPEAKER`, `UNJUSTIFIED_SPEAKER_RETURN`, and `SPEAKER_SWITCH_WITHOUT_INFORMATION_GAIN` as hard planning filters.

## Performance-led mixed-edit gate

The planner must select a declared style profile. The `performance_led_mixed_edit/v260928` profile is the reusable default for spoken celebrity mixed edits whose intended feel is a natural multi-person montage rather than an explanatory slide chain.

- Its default work order is 25--30 seconds and 3--4 source segments. A different duration or shot count is allowed only when declared before planning; do not silently force every request into four short segments.
- Every edit needs at least one `performance_anchor`; for a 25--30 second edit, select one or two anchors and use the remaining segment(s) only as an answer, detail, consequence, or restrained payoff.
- Each segment must carry exactly one editorial role: `performance_anchor`, `response`, `mechanism`, `consequence`, or `payoff`. The ordered role route must be recorded. A plan without an anchor, or one made principally of `response`/`mechanism` fragments, fails `MISSING_PERFORMANCE_ANCHOR` or `FRAGMENT_CHAIN`.
- An adjacent relation must be recoverable from the *whole utterances* and role route. A shared noun such as “温度”, “资源”, “熔炉”, or the product name is only a review cue, never proof of cause, answer, or payoff. Use `KEYWORD_ONLY_BRIDGE`, `PROPOSITION_RESET`, or `REPEATED_MECHANISM_WITHOUT_PROGRESS` when no spoken information advances.
- A candidate beginning with a dependent continuation (for example “来提高…”, “甚至可以说…”, “然后…”) or ending on an unfinished continuation (for example “还…”, “甚至可以说…”) is rejected as `DEPENDENT_OPENING` or `OPEN_TAIL`. It cannot be promoted by calling it a bridge.
- Planning must preserve a speaker’s natural performance rhythm inside an anchor. Speaker changes occur only between approved source turns; no adjacent identical visible speaker is allowed.

The selectable `performance_led_dialogue_montage/v260928` profile preserves the user-authorized historical structure: 24.5--30 seconds, exactly three source beats, one intact 8--16 second performance or source-contained dialogue anchor, and two independently complete 3--8 second supports. Source-contained internal person/shot changes require an internal-sequence evidence file and remain one indivisible candidate. They may not be split, reordered or used as filler.

## V6 output-boundary handoff

Before render, each planned candidate must supply its isolated actual-ASR text/hash and measured first/last voiced timestamps relative to its canonical visual boundaries. Approval requires: actual text agrees with the declared complete utterance; no new voice begins before the visual in; and no unfinished/residual voice survives beyond the visual out plus the declared natural tail. A candidate failing any test is rejected before plan lock with `ACTUAL_ASR_MISMATCH`, `BOUNDARY_CROSSING_SPEECH`, `DEPENDENT_OPENING`, or `OPEN_TAIL`.

The rendered-output gate must recheck the same conditions at every cut, together with black/freeze/duplicate-frame and hard-text conflicts. A plan may pass source semantics yet still be rejected after render; a render rejection never retroactively validates a broken source candidate.

## Source, registry, candidate, and semantic-cluster identities

Do not use these terms interchangeably:

- A `source` is one original media asset, bound to its SHA-256 and processing stage. One source may yield many different valid speech units. Source-file reuse has no numeric limit.
- A `registry_item` is an audited safe region or observation. It is not automatically a usable candidate and it is not evidence that the source has been fully mined.
- A `candidate` is one canonical, independently intelligible, source-bound complete speech unit. Its identity includes source SHA-256, processing stage, canonical source in/out boundaries, and normalized exact speech text. Trivial boundary jitter or a renamed ID does not create a new candidate.
- A `semantic_cluster` groups candidates that make the same proposition, even when their text, timing, or source differs.

Candidate-use limits apply only to one canonical `candidate_id`; they never limit how many different complete candidates may be selected from a source. The exact ceiling comes only from the current hash-bound work order. The historical low-repeat profile may declare six uses in a fifty-output batch only when it also enforces pairwise candidate overlap <=1, pairwise exact-text overlap <=1, trigram similarity <=0.88 and unique ordered plans. Source reuse must still respect speaker-share, semantic-cluster, visual-boundary, and narrative constraints.

## Candidate-inventory gate

Before any numeric capacity conclusion, the planner must declare a source inventory scope and distinguish registry-item count from complete-candidate count. For every source in scope, record its SHA-256, processing stage, extraction evidence reference, count of audited registry items, count of complete candidates, and `extraction_status`.

Only `exhausted` means that every independently reusable, complete speech unit found within the declared source and stage has been recorded as approved, conditional, or rejected with its reason. `partial` and `not_started` are not exhaustion. A safe-interval registry, a filename list, or a list of preliminary clips does not prove exhaustion.

In `exhaustive` inventory mode, any scoped source that is `partial` or `not_started` returns `CANDIDATE_INVENTORY_INCOMPLETE`. In V11 `witness_sufficient` mode, the selected witness scope may contain `partial`, `witness_sufficient` or `exhausted` sources, but every counted candidate must have complete candidate-level evidence and the global witness must prove all requested plans plus the replacement margin. Unprocessed sources do not count toward capacity and must not be mislabeled exhausted.

## V8 batch diversity gate

Apply this gate once to the complete declared batch after candidate selection and before any plan is locked. It applies to source-backed planned segments across the batch, not to one finished video in isolation. It must not be bypassed by renaming a candidate or changing a filename.

The V9 gate runtime must produce a `semantic-batch-gate-report/v260928` that names this package, the analysis snapshot, candidate-inventory status, style contract, current work-order hash, and every counted segment's immutable candidate fingerprint. A missing identity, cluster, source binding, inventory field, style field, actual-ASR evidence, canonical person identity or traceability field is a rejection, not a warning.

Use these baseline hard limits unless the current hash-bound work order explicitly supplies another authorized batch profile:

- `speaker_shots / total_counted_segments <= 0.35` for every visible speaker.
- One `candidate_id` may occur at most 2 times in the batch.
- One `semantic_cluster_id` may occur at most 3 times in the batch. A cluster represents the same message/proposition, even when wording or source files differ.

After the candidate-inventory gate passes, calculate capacity using only approved candidates: (1) candidate capacity under the two-use limit, (2) capacity by visible speaker under the share limit, and (3) semantic-cluster capacity under the three-use limit. Source-file count and registry-item count must never be operands in these calculations. If any exhausted-inventory capacity is below requested source-backed shots, return `CANDIDATE_POOL_SHORTAGE`, `SPEAKER_CAPACITY_SHORTAGE`, or `SEMANTIC_CAPACITY_SHORTAGE`. Do not solve failure by silently reusing a candidate, relaxing a limit, or promoting a conditional/rejected item.

After planning, recompute the three limits from the final segment table. A failed report blocks plan locking. A passing report must be bound in the locked plan with `analysis_snapshot_id`, `analysis_record_sha256`, `package_id`, `package_skill_sha256`, and `batch_gate_report_sha256`.

Connector exclusion is a batch-policy choice, not an inferred universal rule. Always tag connector prerequisites. When the current user supplies `hard_excluded_connectors`, reject every candidate containing one of those connectors before capacity and plan checks; do not repair it by deleting words from the source utterance.

## V8 global plan-uniqueness gate

Candidate reuse limits do not make finished plans different. For every complete planned video, construct two signatures after segment order and editorial role are final, then compare them across the entire declared batch before plan lock.

- `ordered_plan_signature` is the SHA-256 of the canonical ordered sequence of every segment's immutable candidate fingerprint and editorial role. A candidate fingerprint is `source_sha256 + processing_stage + canonical_source_in_out + normalized_exact_text_sha256`. Do not include plan IDs, file names, output indexes, or mutable labels in this signature.
- `semantic_route_signature` is the SHA-256 of the canonical ordered sequence of every segment's `semantic_cluster_id` and editorial role. It detects a repeated narrative route even when individual candidates are different.

For a batch explicitly requested as different finished videos, the default policy is one use per `ordered_plan_signature` and one use per `semantic_route_signature`. A different current-user batch policy may set another semantic-route ceiling, but it may not silently waive exact ordered-plan uniqueness.

If an ordered-plan signature repeats, return `DUPLICATE_ORDERED_PLAN_SEQUENCE` with every colliding plan ID and reject all colliding plans before rendering. If the semantic-route ceiling is exceeded, return `DUPLICATE_SEMANTIC_ROUTE`. A rename, changed output number, source filename, or tiny boundary jitter cannot make a duplicate plan unique.

Before plan lock, the planner must state the number of valid unique plan signatures it can construct from the exhausted approved inventory under all existing candidate, speaker, semantic, connector, and sequence constraints. If that number is below the requested plan count, return `UNIQUE_PLAN_CAPACITY_SHORTAGE`; do not render duplicate plans and do not claim a greedy failed search proves shortage without retained search evidence.

The `semantic-batch-gate-report/v260928` report must contain the two signatures, style role route, pairwise comparisons and usage tables for every plan. `plan_lock` requires all V9 evidence, diversity, performance-led and uniqueness gates to pass.

## Automatic repair and completion

Sol never repairs. When Sol returns a structured `repair_required` response, the orchestrator may rewind only to a phase explicitly listed in the pipeline config, normally `SEMANTIC_REVIEW` or `GLOBAL_SOLVE`. Each repair increments `repair_round`; all plans, gates, renders, evidence and QC live under that round and cannot overwrite earlier accepted artifacts. Exceeding the declared repair-round limit is a hard stop.

The V20 pipeline is complete only after `v9_gate_runtime.py verify-output` produces a passing V20-bound `semantic-release-authorization/v260928`, post-encode V20 QC passes, and the packaged `v9_deliver.py` writes `semantic-delivery-manifest/v260928`. A completed slice, a passed plan gate, completed rendering, or an ordinary review pass is not completion.

## Canonical cross-source identity and repair invalidation

Every visible person must use a canonical ID matching `person:<stable-id>` and carry hash-bound identity evidence. Descriptions such as “car male speaker”, “same performer”, “observed adult male” or a costume/setting name are not identities and fail `UNKNOWN_CANONICAL_PERSON`. Adjacent cuts compare the outgoing boundary person of one candidate with the incoming boundary person of the next.

Every transition review binds the exact two candidate fingerprints and the current `plan_revision`. Replacing, re-timing or re-transcribing either candidate invalidates both adjacent transitions, duration, ordered signature, semantic-route signature, global completion witness and locked index. A stale repair fails before rendering.

## Task evidence

Bind new evidence to its source paths, SHA-256 values, processing stages, and current task directory. Keep approved, conditional, and rejected evidence separate. Do not create package-level records or manifest members.

## Never do

- Do not reuse prior task approvals as current evidence.
- Do not turn an observed batch failure into source-level approval or rejection without a source-bound analysis.
- Do not render or copy media outside the current executor, immutable gate index, stable renderer, independent review and release authorization. Do not upload, publish, delete sources, overwrite earlier rounds or claim approval without the delivery manifest.
- Do not call DeepSeek, GLM, or any other external model without separate current-user authorization.
