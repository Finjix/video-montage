# V15 content-grounded celebrity montage gate

Read this contract for every celebrity product montage. It addresses the V14 failure where hash-valid review files and allowed relation labels could certify a semantically false chain.

## Required plan contract

Every applicable work order supplies `policy.content_grounding_contract` with canonical product terms, ASR aliases, celebrity IDs, product-mention deadline, substantive-question exception limits, redundancy groups, boundary filler tokens, pacing limits and domain response terms. A missing contract is a hard failure.

Every selected segment carries:

- `opening_frame_visible_person_ids` from source-bound opening-frame evidence;
- `spoken_product_mentions` with canonical term, verbatim spoken span, local time and hash-bound evidence when it contains the required product identity;
- `purpose_contract` with one concrete function, new claim IDs and `non_redundant=true`;
- `boundary_cleanliness` with normal-speed listening, no unexplained leading/trailing tokens and a passing decision.

Every transition carries `content_evidence` whose from/to quotes occur in the actual spoken texts and whose new information, entity flow and narrative-state flow are explicit. The templated sentence “从 cluster:X 推进到 cluster:Y 并增加下一步信息” is not evidence. `round_continuation` is not a default semantic relation.

## Hard failures learned from the 2026-09-11 Wu Yue review

- Every finished edit must audibly identify `无尽冬日` by the configured early deadline. “三亿人” alone does not identify the product. An intact roughly eight-second product/social-proof sentence is allowed and preferred over a truncated ambiguous hook.
- The opening frame contains the celebrity, or the celebrity and supporting cast together. A supporting-cast-only opening is rejected except for a short, independently intelligible direct question with a named topic, target celebrity, immediate answer candidate and `question_answer` transition. “那你呢” is not substantive enough.
- Equivalent payoffs may not be stacked. “这不是易如反掌吗” followed by “so easy” is repetition, not progress; a short echo/reaction cannot be kept merely to raise shot count.
- `他们` in a mechanic sentence must resolve to previously introduced survivors/residents, never silently to “三亿玩家”.
- A later-state statement such as “都玩一个月了” cannot jump backward to an unexplained opening mechanic such as arranging survivors to chop trees.
- A complaint such as “你们游戏太难了” must first receive its natural response branch (“你玩盗版了/你玩的有问题”) before gameplay mechanics.
- Isolated or unrecognized boundary/information tokens such as `氪` or `停` require a new clean cut. ASR similarity cannot waive normal-speed listening.
- For 15–18 second, three-or-more-shot edits, one segment may not exceed 10 seconds or 62% of the plan. Short supporting segments under two seconds need a unique, non-redundant semantic function or the substantive-question exception.

## Output release contract

Sol must review the actual rendered audio at normal speed and emit `semantic-sol-output-review/v15`. Its `content_checks` must explicitly pass product timing, opening composition, non-repetition, segment purpose, pronoun resolution, narrative state, complaint/answer branching, filler-token cleanliness, pacing after declared speed and full audio playback. Generic “semantic continuity=true” is insufficient.

The better 28-item Wu Yue repair batch is retained as positive comparative evidence because it used a single opening, forward mechanic chain, forced-hook policy, explicit repeat checks and direct cut/residual-audio review. It is evidence for structure, not permission to copy its files or hidden cut points into unrelated batches.
