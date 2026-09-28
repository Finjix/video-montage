# Foreground production prompt

Start or resume the current production task in the active Codex turn through the bundled executor. Validate the work order, style profile, source manifest, and content-grounding contract before initialization. Continue each durable local slice immediately until model handoff, completion, or a genuine blocker; an exit code of 22 means progress, not failure.

At a model handoff, perform only the named semantic or independent-review phase, supply the exact hash-bound response, and resume foreground execution. Do not stop because a local chunk completed or a watchdog exists. The watchdog is recovery-only and cannot become the normal production clock.
