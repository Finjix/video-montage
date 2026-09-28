# Foreground production prompt for V18

Start or resume the V18 production task in the current active Codex turn. Validate the job contract, explicit style profile, V18 content-grounding contract and explicit witness/exhaustive source manifest before initialization. Run `scripts/v15_orchestrator.py run-continuous` without a positive `--budget-seconds`; normal foreground mode is unbounded and must continue until model handoff, `COMPLETE`, or a genuine blocker.

When the command returns `WAITING_MODEL`, perform the exact pending Terra or Sol phase, write `semantic-model-phase-response/v18`, call `supply-model`, and immediately return to unbounded `run-continuous`. Do not send a final response because a local chunk completed, because exit 22 was returned, or because a watchdog automation exists. Exit 22 in the foreground means immediately call the command again in the same turn.

The scheduled heartbeat is watchdog-only. It must not become the normal production clock. Keep the task active until the delivery manifest exists or a genuine authority/capacity/failure stop is reached.
