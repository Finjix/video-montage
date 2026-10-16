# Semantic-first planning (`semantic-continuity/v2`)

Read each source's complete reviewed transcript ([speech review](speech-review.md)), people, scenes
and actions first; identify complete turns, propositions and what each depends on; only then compose
adjacent turns and the whole narrative. Never join material only because it shares a game name,
keyword or diversity goal. Logical, semantically continuous before-and-after flow is the first priority: when coherence conflicts
with variety, cast mix or any other target, coherence wins. Reasonable reuse of coherent material within the
[batch limits](batch-review.md) is allowed and pre-authorized; there is no diversify stage and no
`diversity_reason`.

## Duration, shots and people

- Select at natural speed. The clean render must be 1296–2160 frames at 60 fps (21.6–36 s); after the
  final 1.2x it must be 1080–1800 frames (18–30 s). Planning and rendering share the same rounding
  (`round(frames × fps_den / fps_num × 60)` per render segment); 21.5 s cannot reach 18 s and fails.
- At least four real visual shots, no maximum. A real camera, angle or shot-size change inside one
  source counts when native frames prove it; splitting one continuous shot, its text, IDs or range
  does not. Each shot lasts at least 48 clean frames (0.8 s); the longest and shortest differ by at
  most 180 frames (3 s). Shots cover the render timeline without gaps or overlap.
- People follow [selected celebrities](celebrities.md). Offscreen dialogue that serves the meaning is allowed;
  record speakers separately from visible people. Never infer identity from file names or costume.
- Too short: add only complete, semantically connected material. Too long: recompose. Never freeze,
  slow down, repeat source frames, cut mid-sentence or pad with unrelated content.

## Plan format

Schema `video-montage-autonomous-plan/v260929`. Root fields:

- `schema`, `planning_policy: "semantic-continuity/v2"`, `batch_review_policy`, `speech_review_policy`,
  `audio_cleanup_policy`, `cast_policy`, `celebrities` (exact registry), `text_library` (copy of the
  job reference), `batch_analysis` ([batch review](batch-review.md)), `outputs`;
  `batch_reuse_authorization` only when the work order has it; optional `audio_balance`.

Each `outputs[]` item: `plan_id` (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, unique ignoring case),
`segments`, `transitions`, `visual_shots`, `cast_policy`, `protagonist_ids`, optional
`supporting_person_ids` and optional `audio_balance`.

Each `segments[]` item:

- `source_path`, `source_sha256`, integer `source_in_frame` ≤ `speech_end_frame` <
  `source_out_frame_exclusive`, `source_fps_num`, `source_fps_den`, exact reviewed `text`, `speed: 1`;
- `visible_person_ids`, `speaker_id`, and `primary_person_ids` when supporting actors are visible;
- `semantic_cluster_id` ([batch review](batch-review.md)); `audio_cleanup_policy`
  ([audio cleanup](audio-cleanup.md)); optional `audio_gain_db` (-24..0), `candidate_id`, `scenario_bound`;
- `purpose_contract`: non-empty `function`, non-empty `new_claim_ids` (unique within the output),
  `non_redundant: true`, non-negative integer `narrative_stage`. The last segment adds
  `closing_payoff: true` and a `closing_quote` taken from its text, with a useful closing function
  (result, payoff, proof, benefit, punchline, story resolution or a relevant CTA).

A result-first `hook` that returns to the mechanism needs the first transition to be `hook_response`
with `content_evidence.how_why_quote` quoting later speech containing 怎么, 如何, 为什么, 因为 or 原因.
`scenario_bound: true` marks a scene-dependent performance; the transition into it explains the real
`content_evidence.scenario_continuity`.

`transitions` has exactly N−1 items in segment order:

```json
{"relation": "cause_effect", "information_gain": "温度提升吸引更多幸存者",
 "content_evidence": {"from_quote": "把取得的资源用于升级熔炉，提升营地温度",
   "to_quote": "温度提升以后才能吸引更多的幸存者",
   "new_information": "更高温度使营地能够吸引更多居民",
   "entity_flow": "前段的营地温度在后段继续作为幸存者到来的条件",
   "state_flow": "升级熔炉提升温度后，幸存者数量随之增加",
   "premise_refs": [{"segment_index": 1, "quote": "把取得的资源用于升级熔炉，提升营地温度"}]}}
```

- `relation` ∈ `hook_response, question_answer, answer, explanation, problem_solution, claim_support,
  cause_effect, condition_result, progression, proof, benefit, payoff, contrast, counterpoint,
  escalation, punchline, speaker_experience, story_resolution`. A label never proves coherence.
- `from_quote`/`to_quote` come from the two adjacent texts; `new_information` (≥ 4 characters) differs
  from `information_gain`; `entity_flow` (references and entities) and `state_flow` (actions,
  conditions, results) are specific and different from each other.
- `premise_refs` (non-empty): `{segment_index (1-based), quote}` items quoting at least 4 characters of
  retained speech at or before the left segment; never future speech, unused source text or the
  reviewer's own words.
- `bridge_ref` (same format, only the two adjacent segments) when the topic changes. Discovery,
  ad-feed or audience-size hooks (入坑, 刷到广告, 全球…都在玩, 亿人在玩) cannot jump to a concrete mechanic
  (砍树, 获取资源, 建造, 升级熔炉…) without a retained spoken bridge that introduces gameplay (e.g.
  怎么玩, 玩法, 开局, 首先, 先让…). A reviewer's paraphrase, another relation label or a subtitle cannot
  supply a missing premise: pick other footage, add the real bridging turn or recompose.
- Speaker changes add `content_evidence.speaker_change` ([selected celebrities](celebrities.md)).

`visual_shots` lists every real shot in order:

```json
{"render_segment_index": 1, "source_sha256": "<sha256>", "source_in_frame": 0, "source_out_frame_exclusive": 360,
 "source_visual_shot_id": "scene-a-camera-1", "visual_family_id": "scene-a-camera-1",
 "visible_person_ids": ["person:wu-yue"], "boundary_kind": "opening"}
```

`render_segment_index` is 1-based over render segments; audio cleanup keeps every segment its own
render segment, so it equals the segment index. `boundary_kind` is `opening` for the first shot, `edit_cut` at a new render segment and
`source_camera_change` inside one. Keep one stable `source_visual_shot_id` per continuous source shot.

## Machine-checked editorial rules

These fail at `plan-evidence`; satisfy them before submitting:

- no empty or repeated spoken unit, no repeated claim ID, `narrative_stage` never decreases (except the
  hook return above);
- a segment starting with 但/但是/不过 needs `contrast`/`counterpoint`; 所以 needs `cause_effect`,
  `condition_result`, `problem_solution` or `payoff`; 这下 needs `cause_effect`, `condition_result` or
  `payoff`; 然后/接着/随后 need `progression`/`escalation`; the first segment cannot open with 因为 unless
  it contains 所以/因此; 只要 without 就/便/即可/才能 needs a following `condition_result`;
- every output has at least one segment whose own text contains a canonical game name from the text library (category `game`), whoever speaks it; a name split across segments does not count; checked per output as `GAME_NAME_REQUIRED:<plan_id>`;
- the last segment lasts ≥ 2 s and is not a bare account number ("我收了N个幸存者") or a method beat
  ("把资源拿来升级熔炉", "…发展建设");
- only one segment may contain 易如反掌/轻轻松松/小意思/so easy, and "so easy" only beside a result phrase;
- result phrases (幸存者一多, 建设末日城镇, 称霸冰原, 战力蹭蹭, 熔炉满级) need a result-type function and a
  causal relation; after 砍树 and 熔炉 they need a survivor-acquisition bridge (招收幸存者, 吸引更多幸存者…);
- a first-person result ("我收了…幸存者") needs a spoken subject bridge (我, 吴樾, 玩家…) in the previous
  segment or `speaker_experience` with `content_evidence.subject_flow`;
- 海量福利 only in the final segment, with a concrete benefit (礼包码, 福利码, 兑换…) after a result;
- reasons (`entity_flow`, `state_flow`, review reasons) need at least 8 characters and cannot be generic
  ("同一游戏", "语义连贯", "衔接自然", "通过审核"…).

The program checks structure, quotes, frame timelines, signals and hashes only. The agent must analyse the
full context and the actual frames; mechanism-to-result bridges, first-person subject continuity,
deduplicated results, concrete benefits and the closing rules still need judgment.

## Evidence and review

Candidate `continuous_visual` evidence holds every selected native frame (960 px, hashed, half-open
range). Judge from the frames, not the 16-frame sheets; when unclear, do not approve. Plan review fields
are listed in the [workflow](workflow.md#plan-review-approve-plan). Final evidence covers every frame of
the accelerated output and maps shots onto the final timeline (`ceil(frame × 5 / 6)`); the final review
repeats the narrative, cast and shot findings and every transition with the output hash and speech
evidence digest. Plan approval never substitutes for the review of the actual output.

After completion, `manifests/editing_context.json` binds the validated clean inputs, plan and policies
so a subtitle reburn can reuse the edit approval; reburns still need new full-frame final evidence and
review. A full repair discards it and re-analyses.
