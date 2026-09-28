# V12 foreground and watchdog contract

Normal production is `scripts/v12_orchestrator.py run-continuous --config ... --state ...` with no positive budget. A positive normal budget is rejected with `V12_BOUNDED_FOREGROUND_FORBIDDEN_USE_RECOVERY_MODE`. The foreground process writes and renews `.v12/foreground_lease.json` after every durable slice and releases the lease on model handoff, completion or failure.

The heartbeat runs `watchdog`, not production by default. A live, fresh lease returns `foreground_active`; recent state progress without a live lease returns `recent_progress_no_recovery`; both are no-op results. Only a stale state without a fresh foreground owner returns `recovery_required`. Scheduled recovery must use `run-continuous --recovery`, which is bounded by `execution.recovery_budget_seconds`.

Initialization requires a `job_contract`, an explicit profile file whose internal profile ID matches `style_profile_id`, and an explicit source manifest whose declared and actual source counts equal `source_scope_total`. Witness and exhaustive inventory modes must use their matching source-scope modes. This prevents a celebrity live-action job from silently selecting the historical generic profile or treating a full 184-source library as an undeclared default.

Source ASR chunks default to 24 items to amortize local model loading. Rendering and output evidence remain rolling groups of five. The foreground loop immediately starts every next chunk; chunk size is not a scheduling interval.
