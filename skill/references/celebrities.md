# Selected celebrities and first-appearance nameplates (`selected-celebrities/v3`)

Every job binds this policy. Each output selects one or more registered celebrities. Supporting actors may appear beside them, but a selected celebrity must
always be the visual subject. Other people may speak offscreen when it serves the meaning. Identity
comes from actual frames, never folder names. There is no single/multi cast ratio: use the material that gives the most coherent edits ([batch review](batch-review.md)).

## Subject rule

The agent checks the whole interval: the selected celebrity is the centre of composition, performance and
narrative attention and clearly visible. Supporting actors may react or interact beside them, but may
not obscure them beyond recognition, take over the subject or hold the frame alone; no supporting-only
reverse shots. There is no area threshold: state the actual position, shot size, action and subject
relation. Avoid or reselect intervals where the celebrity is not the subject; a declaration alone never
passes.

## Registry and plan fields

The work order lists `celebrities` (required), e.g.
`{"person_id": "person:wu-yue", "name": "吴樾", "nameplate": {"path": "<absolute PNG path>", "sha256": "<sha256>"}}`.
The registry is bound into the work order, state, source index, plan, evidence and every plan/final
review (repeat `cast_policy` and the exact `celebrities` list at their top level).

- Output: `cast_policy`, non-empty unique `protagonist_ids`, and optional
  `supporting_person_ids` with stable `person:` IDs observed in frames. Supporting IDs never overlap the
  selected or any registered celebrity; an unselected celebrity cannot be relabelled as supporting.
- Segments, candidates and shots: `visible_person_ids` lists exactly who is visible, only selected
  celebrities and declared supporting actors, and always at least one selected celebrity. Every
  declared person must actually appear. With supporting actors visible, set non-empty
  `primary_person_ids` (selected celebrities clearly visible in that interval); without them the
  visible selected celebrities are the subject by default. A shot's subjects must belong to its
  segment's subjects. Candidates and segments agree on visible people, subjects and `speaker_id`.
- `speaker_id` is separate from visibility: a speaking supporting actor does not become the subject, and
  offscreen speech needs no appearance or nameplate. Complete `spoken_turns` also record the speaker.

When the speaker changes, the transition adds
`content_evidence.speaker_change: {"from_person_id", "to_person_id", "subject_relation", "reason"}` with
`subject_relation` ∈ `question_answer`, `parallel_experience`, `shared_topic`. Premise and bridge rules
still apply. Two people's "I", accounts, counts or experiences cannot form one person's cause and
result: when both segments are first-person (我, 本人, 这个账号…), use `parallel_experience` with
`speaker_experience`, `contrast`, `counterpoint`, `explanation`, `claim_support` or `proof`.

## Review fields

- Segment, shot and output findings: `selected_cast_pass: true`, `protagonist_primary_pass: true` and a
  concrete `protagonist_primary_reason`.
- Segment and shot findings: exact `visible_person_ids` and `primary_person_ids` (the visible selected
  celebrities when the plan omits it); segments also `continuous_visual_sha256` and `cast_reason`.
- Output findings: exact `protagonist_ids`, `supporting_person_ids` (`[]` when none) and `cast_reason`.
- Transitions with a speaker change: `speaker_change_pass: true` and `speaker_change_reason`.
- Final review outputs (packaged): `nameplates_pass: true` and `nameplate_reason`, after checking the
  whole output's subject relations, frames and transitions again.

## Nameplates

Each selected celebrity shows a nameplate once, from the first frame of their first appearance:
192 clean frames by default, continuing across consecutive solo shots of the same celebrity and ending
before they leave or another selected celebrity appears. Supporting actors neither end a plate nor get
one. The first-appearance shot must show only one selected celebrity (supporting actors allowed);
choose separate introduction shots instead of simultaneous first appearances. Between two different
celebrities' plates leave at least 18 completely plate-free final frames (0.3 s at 60 fps): the earlier
plate ends sooner, the later one still starts at the first appearance. Each plate must show at least
48 final frames (0.8 s); otherwise reselect the first-appearance footage (never freeze or stretch).

The windows are deterministic: copy them from the plan evidence `outputs[].nameplate_windows` into the
packaging config as `nameplates: [{"person_id", "path", "sha256", "start_frame", "end_frame_exclusive"}]`
(unaccelerated 60 fps frames; path and hash from the registry). Packaging, final evidence and reburn
reject missing, wrong, repeated, overlapping or retimed plates; review the disappearance, the blank gap
and the next appearance in the actual encoded frames.
