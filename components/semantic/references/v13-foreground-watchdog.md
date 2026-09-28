# V13 foreground and watchdog contract

Normal production is `scripts/v13_orchestrator.py run-continuous --config ... --state ...` with no positive budget. A positive normal budget is rejected with `V13_BOUNDED_FOREGROUND_FORBIDDEN_USE_RECOVERY_MODE`. The foreground process writes and renews `.v13/foreground_lease.json` after every durable slice and releases the lease on model handoff, completion or failure.

The heartbeat runs `watchdog`, not production by default. A live, fresh lease returns `foreground_active`; recent state progress without a live lease returns `recent_progress_no_recovery`; both are no-op results. Only a stale state without a fresh foreground owner returns `recovery_required`. Scheduled recovery must use `run-continuous --recovery`, which is bounded by `execution.recovery_budget_seconds`.

The foreground executor writes `.v13/front_status.json` at startup and after every durable slice. A front-end can display its `started_at` as a continuously advancing elapsed timer and show phase/progress from the same record. The status reader is never an executor: it must not start recovery, emit a task message, change the foreground lease, or cancel an in-flight process. Codex desktop UI rendering itself remains a host capability; this contract supplies the host-readable state without pretending that a package can modify the desktop UI.

Initialization requires a `job_contract`, an explicit profile file whose internal profile ID matches `style_profile_id`, and an explicit source manifest whose declared and actual source counts equal `source_scope_total`. Witness and exhaustive inventory modes must use their matching source-scope modes. This prevents a celebrity live-action job from silently selecting the historical generic profile or treating a full 184-source library as an undeclared default.

Source ASR chunks default to 24 items to amortize local model loading. Rendering and output evidence remain rolling groups of five. The foreground loop immediately starts every next chunk; chunk size is not a scheduling interval.
