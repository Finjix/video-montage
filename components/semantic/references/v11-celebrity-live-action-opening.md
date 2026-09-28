# V11 celebrity live-action opening contract

Use this contract only when the work order explicitly selects `celebrity_live_action_opening/v11`. It does not change generic spoken-montage jobs.

## Learned structure

The reference ads use the celebrity opening as a self-contained performance unit. A strong question, contradiction, challenge or reaction opens the piece; a celebrity answer or stance establishes trust; later clauses add a concrete experience, rule, benefit or punchline. Single-star openings may remain on the same person while changing scene, shot scale, angle, action, costume or performance state. Two-star openings may use an `A -> B -> A` return when the return is an answer, counterpoint, continued round, escalation or payoff.

The learned scope ends at the first frame where gameplay becomes the dominant full-screen visual. A handheld phone or game inset is allowed only while live action remains dominant. Full-screen gameplay, later gameplay narration, end cards and CTA are never candidates for this profile.

## Executable evidence

Every candidate must bind `live_action_scope_evidence_path` and `live_action_scope_evidence_sha256` to a JSON object with schema `celebrity-live-action-scope-evidence/v11`. It binds the candidate ID, source hash and exact interval; binds the candidate's in/mid/out frames; marks each frame as `live_action_primary` or `live_action_primary_with_game_inset`; names the dominant canonical person; explicitly records `fullscreen_game=false`, `end_card=false` and `visual_cta=false`; and records `first_fullscreen_game_time` when present.

Every candidate also binds a `candidate-cta-evidence/v11` object with `spoken_cta=false`, `visual_cta=false`, `end_card=false` and `decision=pass`. Identity evidence uses `canonical-person-identity-evidence/v11` and must match the candidate's primary and boundary person IDs.

Same-person adjacency is permitted only when the exact transition has a passing, hash-bound `visual_change` object, at least one declared visual-change type, a permitted semantic relation, non-empty information gain and different proposition IDs. This permission does not allow a repeated take, unchanged framing, a duplicated action or a no-progress paraphrase.

Complete reactions from 0.8 to 3.0 seconds may use the `short_reaction` role only with candidate type `complete_short_reaction`. They cannot replace the required performance anchor and no plan may use more than two.

## Witness-sufficient inventory

`witness_sufficient` means the approved, fully evidenced candidate pool proves every requested plan plus the declared replacement margin. Sources included in the witness scope may be `partial`, `witness_sufficient` or `exhausted`; every selected candidate still needs complete candidate-level ASR, identity, visual-scope, CTA and boundary evidence. Do not report unprocessed material as exhausted.
