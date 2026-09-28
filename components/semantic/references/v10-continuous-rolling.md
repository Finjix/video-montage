# V10 continuous rolling production

Normal production runs `v10_orchestrator.py run-continuous` and immediately starts the next durable slice after exit 22. The Codex task remains active and handles pending Terra/Sol actions immediately. The scheduled heartbeat is recovery-only, not the normal five-minute clock.

All 50 plans are globally solved and witnessed before rendering. Rendering then uses rolling groups of five. Each group is rendered, evidenced and independently reviewed; passing indexes are frozen and may be delivered, while rejected indexes are quarantined and re-authored without blocking clean indexes or weakening global diversity. Completion still requires 50 authorized outputs and the final delivery manifest.

Use `historical_low_repeat_rolling/v10` for the accepted 25--30 second performance montage. Candidate inventory may be `witness_sufficient`: the approved pool must prove the requested plan count plus at least 20% valid replacement-plan capacity. Remaining source files need not be exhaustively re-ASR'd before planning. Matching hash-bound evidence is reusable; prior approval decisions are not automatically reused.
