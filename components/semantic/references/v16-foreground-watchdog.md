# V18 foreground and watchdog contract

Normal production is `scripts/v15_orchestrator.py run-continuous --config ... --state ...` with no positive budget. The retained entrypoint writes and renews `.v18/foreground_lease.json` after every durable slice and releases the lease on model handoff, completion or failure.

The heartbeat runs `watchdog`, not production by default. A live, fresh lease or recent progress is a no-op. Only stale state without a fresh foreground owner may start `run-continuous --recovery` with the configured bounded recovery budget.

The foreground executor writes `.v18/front_status.json`. Reading status never starts or interrupts production.

Initialization requires the V18 job contract, explicit style profile, V18 content-grounding contract and explicit source manifest. Rendering and evidence remain rolling groups of five; the foreground loop immediately continues local chunks and model handoffs until completion or a genuine blocker.
