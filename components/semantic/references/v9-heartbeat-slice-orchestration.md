# V9 heartbeat-slice orchestration

V9 never relies on a long-lived detached production worker. Every heartbeat reads the hash-bound config/state and runs at most one bounded local slice or one requested Terra/Sol phase.

- Exit 22 / `CHUNK_PENDING`: durable progress exists; call `resume` on the next heartbeat without consuming retry.
- Exit 20 / `WAITING_MODEL`: perform the exact pending Terra or Sol phase, write the required response and call `supply-model`.
- Exit 2 / `FAILED`: retries or repair rounds are exhausted, or a hard contract failed.
- Exit 0 / `COMPLETE`: the final V9 delivery manifest exists and is hash-bound.

Production config requires an active heartbeat registration and Winky checkpoint command. Config hash drift stops execution.

`SOURCE_ASR` and `OUTPUT_ASR` process a small configured item count, write each result atomically, update progress, and return 75 until complete. CUDA dependency absence or CUDA-memory failure switches to CPU within the same slice and is not a business retry.

Rendering processes a bounded plan count per invocation and reuses only outputs whose plan/export evidence hashes remain valid. Cut-smoke and output-evidence stages may be time-sliced; an interrupted slice with increased progress is resumed rather than rejected.

The heartbeat handles candidate build, cross-source identity, semantic review, global solve and cut-smoke review with Terra. Sol performs only rendered-output final QC. A structured `repair_required` response may rewind only to configured earlier phases and creates a new repair-round directory. Passed older artifacts remain immutable.

The final chain is gate -> cut smoke -> render -> output evidence -> output ASR -> Sol -> release authorization -> delivery. Background launch, partial ASR, completed rendering or release authorization alone is not completion.
