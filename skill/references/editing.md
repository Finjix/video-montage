# Editing rules

## How to compose

1. Read every source's whole transcript in the catalog and look at its sheet before planning: who speaks, to whom,
   in which scene, and which sentences depend on earlier context.
2. Build each video as a short argument: an opening that stands on its own (a question, a claim, a hook), then
   steps that each add something new, then a payoff (a result, benefit, punchline or call to action of at least 2 s).
3. Join clips only when the second follows from the first (answer, cause, next step, contrast...). A shared game
   name or keyword is not a link. Repeating the same claim, cutting a sentence in half or ending on a bare method
   step ("把资源拿来升级熔炉") is not acceptable.
4. Plan whole sentences (`|` in the catalog). Use a clause-level start or end only when the clause is complete
   on its own.
5. Plan the batch as a whole: vary openings, cameras, routes and closings across outputs within the limits below.
   Reuse coherent material rather than forcing weak joins, but never cycle the same edit with tiny changes.

## Machine-checked (`check`)

Edit shape:
- 1020-1800 frames (17-30 s at 60 fps), natural speed, at least 4 camera shots, every shot at least 48 frames
  (0.8 s), longest minus shortest shot at most 180 frames (3 s). Long single-camera clips break the 3 s spread;
  keep clips of similar length or pick clips that contain camera changes.
- Two adjacent clips cut inside one camera shot are a jump cut, not a new shot.
- Every clip starts and ends in clean silence on a stable picture (`in`/`out` in the catalog); the pause kept at a
  cut is at most 0.45 s before and 0.55 s after the speech (the catalog's `in`/`out` already include this). User-rejected intervals are refused.
- At least 3 different source files per video (unless the user decided otherwise).

Cast:
- Every shot shows at least one registered celebrity; supporting actors may appear beside them, and offscreen
  speakers are fine. Make sure (from the sheets) that the celebrity is the visual subject, not an extra.
- Each celebrity's first appearance in a video must be a solo shot (no other registered celebrity); the nameplate
  shows there for up to 192 frames, at least 48, with 18 plate-free frames before the next plate.

Text (per video):
- At least one clip speaks a game name from the text library (`无尽冬日`).
- A clip starting with 但/但是/不过 needs `contrast`/`counterpoint`; 所以 needs `cause_effect`, `condition_result`,
  `problem_solution` or `payoff`; 这下 needs `cause_effect`, `condition_result` or `payoff`; 然后/接着/随后 need
  `progression`/`escalation`. The first clip cannot open with 因为 (unless it contains 所以/因此); 只要 without
  就/便/即可/才能 needs a following `condition_result`.
- Result phrases (幸存者一多, 建设末日城镇, 称霸冰原, 战力蹭蹭, 熔炉满级) need a causal transition into them, and
  after 砍树 and 熔炉 a survivor bridge (招收幸存者, 吸引更多幸存者...). "so easy" only beside a result phrase.
- Only one clip may use 易如反掌/轻轻松松/小意思/so easy. 海量福利 only in the final clip with a concrete benefit
  (礼包码, 兑换...). The final clip lasts at least 2 s and is not a bare count ("我收了30个幸存者") or a method beat.
- A discovery hook (入坑, 刷到广告, 全球三亿人都在玩...) cannot jump to a mechanic (砍树, 建造, 升级熔炉...) without a
  spoken gameplay bridge (怎么玩, 开局, 首先...).
- A first-person result ("我收了...幸存者") needs a spoken subject (我, 吴樾, 玩家...) in the previous clip or
  `speaker_experience`. Two different speakers' first-person accounts cannot form one cause and result: use
  `speaker_experience`, `contrast`, `counterpoint`, `explanation`, `claim_support` or `proof`.

These checks catch known mistakes; passing them does not make an edit coherent. That judgment stays yours.

## Transitions

`relation` is one of: `hook_response`, `question_answer`, `answer`, `explanation`, `problem_solution`,
`claim_support`, `cause_effect`, `condition_result`, `progression`, `proof`, `benefit`, `payoff`, `contrast`,
`counterpoint`, `escalation`, `punchline`, `speaker_experience`, `story_resolution`. The `reason` names what
actually links the two clips (at least 12 letters; "coherent", "same game" or "smooth" are rejected).

## Batch limits

Identity is the source file plus the normalized words; renaming or retiming never makes new content.

| Limit (per batch) | Default | `reuse: reasonable` | `reuse: extended` |
| --- | --- | --- | --- |
| identical clip sequence / identical speech | 1 | 1 | 1 |
| same unit, same words | 6 | 16 | 30 |
| same opening words, same opening camera | 4 | 4 | 8 |
| same first two cameras | 2 | 5 | 10 |
| same closing words | 4 | 10 | 16 |
| same middle clips (only head/tail changed) | 1 | 3 | 3 |
| same clip at the same position (pairwise) | 40% | 60% | 80% |
| whole-speech trigram similarity (pairwise) | 0.88 | 0.88 | 0.93 |

So N videos need at least N/4 different openings. If the inventory cannot reach the requested count under these
limits (or the 3-source minimum), ask the user with the numbers whether to allow reuse or fewer sources.

## Visual review

On each cut sheet compare the last frame before a cut with the first frame after it: similar subject position and
size read as one flow; a large jump in framing of the same person, a half-finished gesture, a frame from another
shot, a flash or a transition effect means changing the clip choice. Check that the people you tagged are the ones
in the frames.
