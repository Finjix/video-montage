# Editing rules

## What a finished video is

One person (or two or three) talks to the viewer for 17-30 s and makes one point: an opening that stands on its
own, a body that explains it, a payoff or call to action. Reference shape (two videos the user holds up as the
standard): about four clips, each a run of whole sentences from one source, every clip a speaker on camera addressing
the viewer, the second clip continuing what the first said, the last clip a gift code and "无需下载，点击即玩".

```text
body    一上来就给你几个逃难的幸存者，你得收留他们，安排他们砍树、打猎、挖矿。      (source A)
body    就你们广告里刷到的，开局一个小熔炉，收集幸存者，建设城镇，那些玩法这儿全都有。  (source B, same flow)
cta     输入我的专属礼包码，大聪666，大聪888，快来上线领资源吧。                    (source C)
```

What the user rejected: videos stitched from four unrelated 3-second snippets, dialogue lines of skits (questions put
to an offstage partner, replies, gags), and joins such as "百分之九十九的冰雪末日生存文都写错了" followed by "我在无尽冬日等你"
(a teaser with no explanation, then a sign-off).

## How to compose

1. Read every source's whole transcript in the catalog and look at its sheet before annotating: who speaks, to whom,
   in which scene, which sentences depend on earlier ones.
2. **Cut every source into beats** (`beats` in `annotate`). A beat is the smallest run of whole units that is a
   self-contained thought; a clip is one or more whole consecutive beats, so what you leave outside a beat can never
   be planned. For each beat set:
   - `kind`: `talk` when a viewer who has not seen the rest hears the speaker informing, asserting, explaining or
     inviting, and the beat still makes sense with every other character's line deleted. That includes an answer to
     another character when it stands alone as a statement or explanation ("这个号很稳，三天就能升满" answering "怎么玩的？"):
     the question is not needed to follow it. `scene` only when the beat is acting: it needs the other line to make
     sense (a bare "对！", "不是我", "那怎么办"), is a reaction, a gag or banter, a question put to someone on screen, or
     role-play inside a story. When in doubt, choose `talk` if the words alone are a complete message to a viewer, and
     `scene` if they only work as a reply.
   - `role`: `hook` (opens a video by itself: a question, claim or provocation that does not need earlier context),
     `body` (explains, shows how, proves, adds a point), `payoff` (the result, benefit or punchline that closes a
     thought), `cta` (gift code, download or click, sign-off).
   - `gist`: one English sentence: what the beat says and what it does for a video.
   Cut beats where the thought ends: never separate a question from its answer, a "three things" setup from its list,
   a "why" from its "because", a teaser from its explanation, a "so/then/this/they" line from the line it refers to.
   Leave out units that are noise, greetings aimed at someone else, names of other people, and anything you would
   not want a viewer to hear alone.
3. Compose each video as a script from beats in this order: at most one `hook`, one or more `body`, at most one
   `payoff`, at most one `cta`; it ends on a payoff or a cta. Prefer long runs (two or three beats from one source) to many
   short clips: fewer joins, fewer chances to lose the thread. Build each video from one celebrity's beats; cross to a
   second celebrity only when a join genuinely needs the hand-over (rule 4) or that celebrity's own beats cannot
   complete the script — do not mix celebrities inside a video by default, vary the batch instead by which celebrity
   leads each output. A payoff built as a quick tally or parallel pair ("一分钟上手，三分钟上头" and the like) still
   carries forward in cadence even though the sentence is grammatically complete: it can close a video only when no
   other output in the batch uses that same beat purely as a lead-in to a `cta`; otherwise give it a `cta` to land on.
4. Join clips only when the second follows from the first (answer, cause, next step, proof, contrast...). A shared game
   name or keyword is not a link. Repeating the same claim, cutting a sentence in half or ending on a bare method
   step ("把资源拿来升级熔炉") is not acceptable. A clip opening with 然后/接着/随后/这样/这 continues a specific action
   or cause the previous clip actually named (a first step such as "先砍树", a stated cause); a general claim ("攻略都
   给你整理好了", "这游戏不一般") is not that step, so 然后 after it is a non sequitur even when both clips share a
   topic — pick a clip that states the concrete step, or drop the connective. Change the person talking only where the
   new speaker's first line stands on its own (a payoff, a call to action, or a hand-over such as "接下来听我说").
5. Plan whole sentences (`|` in the catalog) in whole beats. Use a clause-level start or end only when the beat
   boundary says so.
6. Plan the batch as a whole: vary openings, cameras, routes and closings across outputs within the limits below,
   but never trade a join's sense for variety. Reuse coherent material rather than forcing weak joins, and never
   cycle the same edit with tiny changes. The requested count is mandatory: [reaching the count](#reaching-the-count)
   says what to do when the first routes you find are fewer; never ask about reuse.

## Read-through (after every `check`)

Open `temp/reports/scripts.md` and read each video once as a viewer who has seen nothing else. For every join ask:
1. Does clip B's first sentence answer, continue, prove or pay off clip A's last sentence, with nothing missing?
2. Is anything left hanging at the end of clip A (a question, a "三件事", a teaser, "why") that B does not resolve?
3. Does any word in B refer to something only the source's earlier lines supply (这, 那, 他们, 所以, 但是, 还有)?
4. Is a sign-off, greeting, self-introduction or call to action sitting anywhere but its place?
5. Do the two speakers' claims fit together (a first-person result from one person following another's story is a break)?
6. Does the last clip's last line sound finished — a settled statement, a rhetorical question or a `cta` — rather than
   a brisk tally or parallel pair that still implies a next line, especially a beat the batch otherwise only ever uses
   as a lead-in to a `cta`?
Any "no" means changing the clip, not the `reason`. Write the `reason` after you have passed the read-through; it names
the words in A and B that connect them.

## Machine-checked (`check`)

Edit shape:
- 1020-1800 frames (17-30 s at 60 fps), natural speed, at least 4 camera shots, every shot at least 48 frames
  (0.8 s), longest minus shortest shot at most 480 frames (8 s). A very long single-camera clip (over 9 s) breaks
  the spread beside short-cut clips; pair it with clips of similar shot length or clips that contain camera changes.
- Two adjacent clips cut inside one camera shot are a jump cut, not a new shot.
- Every clip starts and ends in clean silence (`in`/`out` in the catalog) with no dissolve, flash or fast motion at the
  edge; the pause kept at a cut is at most 0.45 s before and 0.55 s after the speech (the catalog's `in`/`out` already
  include this). A plain camera cut near the edge is fine as long as the edge shot keeps its 48 frames. User-rejected
  intervals are refused.
- At least 3 different source files per video (unless the user decided otherwise).

Talk and structure:
- Clips are whole beats of one source; units outside every beat and sources without beats cannot be planned.
- At most 5 clips; every clip is talk: a clip containing a `scene` beat is refused.
- Beat roles run hook, body, payoff, cta in that order: a hook only first, at most one cta, at least one body, and the
  last beat is a payoff or a cta.

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

These checks catch known mistakes; passing them does not make an edit coherent. That judgment stays yours, and the
read-through above is how you exercise it.

## Transitions

`relation` is one of: `hook_response`, `question_answer`, `answer`, `explanation`, `problem_solution`,
`claim_support`, `cause_effect`, `condition_result`, `progression`, `proof`, `benefit`, `payoff`, `contrast`,
`counterpoint`, `escalation`, `punchline`, `speaker_experience`, `story_resolution`. The `reason` names what
actually links the two clips (at least 12 letters; "coherent", "same game" or "smooth" are rejected).

## Batch limits

Identity is the source file plus the normalized words; renaming or retiming never makes new content. The user has
allowed reuse up to these limits, so use them freely instead of forcing weak joins.

| Limit (per batch) | Max |
| --- | --- |
| identical clip sequence / identical speech | 1 |
| same unit, same words | 30 |
| same opening words, same opening camera | 8 |
| same first two cameras | 10 |
| same closing words | 16 |
| same middle clips (only head/tail changed) | 3 |
| same clip at the same position (pairwise) | 80% |
| whole-speech trigram similarity (pairwise) | 0.93 |

So N videos need at least N/8 different openings. If the routes you have found do not reach the requested count under
these limits, continue with [reaching the count](#reaching-the-count); do not ask about reuse.

## Reaching the count

The count the user asked for is the deliverable. A first pass that yields fewer coherent videos is a signal to dig,
not to stop, and not to ask. Work down this ladder, re-running `annotate` and `check` after each rung, until you have
every video:

1. **Annotate every source.** A source without people and beats contributes nothing. `annotate` lists `no beats yet`
   sources: finish them all before concluding anything about the footage. Fast talkers have few clean pauses, but most
   sources still have several cuttable runs (`in`/`out` marks in the catalog); look for them.
2. **Cut finer beats.** The smallest self-contained thought is often one sentence or one answer. More, shorter beats
   are more routes, and a clip may join two or three consecutive beats back together. Give each beat its honest `role`:
   a body beat that closes a thought is a `payoff`; a standalone opening claim is a `hook`.
3. **Recombine.** One beat serves many videos in different routes and different company (hook A + body B + cta C, then
   hook A + body D + cta C...), up to the batch limits. Recombine within one celebrity's own beats first; cross to a
   second celebrity only once that celebrity's beats are exhausted or a join needs the hand-over. Put each opening,
   body run and closing in as many coherent combinations as the limits allow before you decide a route is missing.
4. **Pair shots of like length.** `check` prints the possible shot lengths per clip: pair a 10 s single-shot beat with
   other long-shot beats, a fast-cut beat with fast-cut beats, and split a beat at a clean in/out pair only where both
   halves are complete thoughts.
5. **Weak routes, labelled.** Only when rungs 1-4 are exhausted, fill the remaining slots with the least-weak routes:
   every clip complete and on the same topic, the link no stronger than "also about this". A dangling question,
   teaser, pronoun without its referent or a misplaced sign-off is a break, never a weak route. Deliver them, and list
   each by `plan_id` as weaker in the final summary so the user can swap them when more footage arrives.

Never edit `requested_outputs` (a count mismatch blocks `render` on purpose) and never end with "only N fit".

## Visual review

On each cut sheet compare the last frame before a cut with the first frame after it: similar subject position and
size read as one flow; a large jump in framing of the same person, a half-finished gesture, a frame from another
shot, a flash or a transition effect means changing the clip choice. Check that the people you tagged are the ones
in the frames, and that every `talk` beat shows the speaker alone in frame, addressing the viewer.
