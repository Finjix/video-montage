# V11 gate behavior with V9-shaped artifact compatibility

V11 keeps the established V9-shaped work-order, lock, report and authorization schemas so the stable renderer wrappers remain compatible. `scripts/v9_gate_runtime.py` is packaged with V11 behavior and binds every artifact to `semantic-analysis-training-backup-v11` and the current V11 `SKILL.md` hash.

The gate retains complete evidence requirements: source hashes, actual-ASR files/hashes, first/last voiced times, canonical `person:<id>` identities, native frames, semantic-cluster review, independent transition bindings, global completion witness, duration/role constraints, pairwise diversity and unique plan/route signatures.

A passing gate emits renderer-compatible locked plans. Every render verifies the exact plan/index/report hashes. Output authorization verifies every plan, export, actual output ASR, cut evidence and Sol review. The packaged `v9_deliver.py` accepts only a passing authorization and creates `semantic-delivery-manifest/v11`.
