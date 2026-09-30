# Watchdog-only heartbeat prompt

Inspect the current task's foreground lease and durable progress. Stay quiet while ownership is active or progress is recent. Start a bounded recovery slice only when both are stale. At a model handoff, perform only the named phase, bind its response, and resume recovery. Preserve accepted outputs, never deliver rejected media, and notify only on completion or a genuine capacity, authority, or unrecoverable failure. Do not call external models without a separate current-user request.
