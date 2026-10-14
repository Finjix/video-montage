# Executor workflow

This contract applies to the legacy executor route selected by `SKILL.md`.

Run `assets/dependencies/python/python.exe scripts/executor/scripts/three_suite_ff.py preflight` from the suite root, then use the same executor for `init`, `semantic-run`, `semantic-complete`, `controller-preflight`, `controller-finalize`, `controller-validate`, and `complete` in order. Resolve every component from the bundled suite paths.

Semantic completion requires authorization for the entire declared batch, an exhaustive invalid-range audit against the current packaged registry, independent candidate review, immutable plan hashes, and a passing semantic delivery manifest. The controller delivery must contain exactly the locked plan IDs.

Repairs follow the same state machine. A rejected candidate invalidates all dependent outputs, which must be re-authorized, re-rendered, and re-reviewed. Task-local plan mutation, outside renderers, partial post-encode QC, or direct delivery-file replacement cannot produce a release receipt.

Reject seconds-only plans, missing original-source frame lineage, unresolved placeholders, stale approvals, generator-written passes, or a premaster without `render_mode: source_frame_ranges/v1`. Completion requires passing controller validation, independent post-encode QC, fresh output ASR, and the final encoded opening-family gate. Technical file validation alone is insufficient.
