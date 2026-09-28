# V8 autonomous orchestration

V8 adds orchestration above the V8 gate runtime. It does not replace candidate evidence, independent Terra/Sol decisions, the stable renderer, or release authorization.

## One production job

1. Copy `pipeline-config.template.json` into the task directory and replace every required absolute-path variable.
2. Create a Codex thread heartbeat for the job. Save an active `semantic-heartbeat-registration/v8` JSON containing `task_id`, `automation_id`, and `status: active`; the configured registration path must exist before `init`.
3. Run `v8_orchestrator.py init`, then `resume` once.
4. The heartbeat calls `status` and `resume` on later wakes. `BACKGROUND_RUNNING` is normal and stays quiet while progress changes.
5. When status is `WAITING_MODEL`, read `.v8/pending_model_action.json`, perform only that phase with the named local Codex role, write the exact required outputs, create `semantic-model-phase-response/v8`, run `supply-model`, then `resume` again.
6. Continue until `COMPLETE`, `FAILED`, or a genuine authority/capacity conflict. Do not end the heartbeat merely because a local worker was launched.

## Status meanings

- `READY`: launch or continue the next phase.
- `BACKGROUND_RUNNING` (CLI exit 21): a local worker is alive; monitor on the next heartbeat.
- `WAITING_MODEL` (CLI exit 20): the heartbeat must complete the requested Terra/Sol phase and supply its artifact.
- `FAILED` (CLI exit 2): retry budget is exhausted or a hard gate rejected. Ordinary candidate/plan failures may be repaired inside the authorized work order; an authority or true exhausted-capacity conflict requires the user.
- `COMPLETE` (CLI exit 0): every configured phase, including release authorization, completed.

## Required phase chain

`SOURCE_ASR -> CANDIDATE_BUILD -> IDENTITY_AUDIT -> SEMANTIC_REVIEW -> GLOBAL_SOLVE -> PRELOCK_GATE -> CUT_SMOKE_BUILD -> CUT_SMOKE_REVIEW -> BATCH_RENDER -> OUTPUT_EVIDENCE -> SOL_FINAL_QC -> RELEASE_AUTH`

Local commands run without a shell. Each phase has fixed output paths. A phase completes only when the command exits 0 and every declared output exists; artifacts are then hashed into task-local state. A config edit invalidates the state through `CONFIG_HASH_MISMATCH`.

Model phases are not external Winky consultations. Terra performs intermediate decisions and Sol performs only rendered-output final QC in the Codex task. GLM or another external model remains forbidden without a separate current-user request.

## Heartbeat behavior

The heartbeat should remain quiet while a worker is healthy and progress changes. It should notify only on completion, exhausted retries, a genuine user-authority decision, or a stalled worker that cannot be recovered. It must preserve passed artifacts and never copy rejected exports into delivery.

## ASR recovery

`v8_source_asr.py` loads faster-whisper once, writes one hash-bound JSON per source, updates a progress file after each source, resumes completed matching sources, and falls back from CUDA to CPU after a CUDA/memory failure. Do not launch one Whisper process per source.

## Renderer integration

The V8 gate emits renderer-compatible locked plans containing the full source path, hashes, canonical times, natural tail, canonical boundary identities, actual-ASR bindings and work order. `v8_batch_render.py` verifies each exact plan against the gate and locked index before invoking the stable portable renderer. It resumes only outputs whose render evidence still matches the plan/export hashes.


