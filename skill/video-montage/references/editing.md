# Editing

## The product

A 17-30 s spoken argument to the viewer: an opening that stands on its own, a body that explains it, a payoff or
call to action. Every clip is a speaker on camera addressing the viewer, each clip continues the last, the final
clip a gift code and "无需下载，点击即玩". The user's first reference videos had about four long clips; the user
then found four-clip batches repetitive (2026-10-28) and asked for finer cuts with no clip limit: a video is now
many short pieces, one to three whole sentences each, from many source files (5-8 is typical), so two videos rarely
share a piece and never share more than a quarter of their positions.

```text
body    一上来就给你几个逃难的幸存者，你得收留他们，安排他们砍树、打猎、挖矿。      (source A)
body    就你们广告里刷到的，开局一个小熔炉，收集幸存者，建设城镇，那些玩法这儿全都有。  (source B, same flow)
cta     输入我的专属礼包码，大聪666，大聪888，快来上线领资源吧。                    (source C)
```

The user rejected: four unrelated 3-second snippets stitched together (unrelated is the fault, not short: short
pieces are right when each continues the last); skit dialogue (questions to an offstage
partner, replies, gags); joins such as "百分之九十九的冰雪末日生存文都写错了" followed by "我在无尽冬日等你" (a teaser
with no explanation, then a sign-off); a tally payoff ("一分钟上手，三分钟上头") left as the last line when the batch
otherwise uses it only before a `cta`.

## Beats

Read every source's whole transcript and its sheet first: who speaks, to whom, in which scene, which sentences lean
on earlier ones. Then cut every source into beats. A beat is the smallest run of whole units that is a self-contained
thought, usually one sentence or clause (`开局一定要先砍树。` / `把获得的资源拿来升级熔炉，` / `那战力就蹭蹭往上涨。`
are three beats, not one); clips are made of whole beats, so what stays outside a beat is never used. Split wherever
the catalog has a clean `out` after one unit and `in` before the next and both sides stand on their own.

- `kind`: `talk` when a viewer who has seen nothing else hears the speaker inform, assert, explain or invite, and the
  beat still makes sense with every other character's line deleted. An answer that stands alone as a statement
  ("这个号很稳，三天就能升满" after "怎么玩的？") is `talk`. `scene` is acting: it needs the other line (a bare "对！",
  "不是我", "那怎么办"), a reaction, gag, banter, a question to someone on screen, role-play inside a story. In doubt:
  `talk` if the words alone are a complete message to a viewer.
- `role`: `hook` opens a video by itself (a question, claim or provocation needing no context) and appears nowhere
  else; `body` explains, shows how, proves or adds a point; `payoff` is the result, benefit or punchline that closes
  a thought; `cta` is the gift code, download or click, sign-off. Openings are not reserved for hooks: any body or
  payoff that stands alone may open a video too (`冰雪末日零下七十度，有木材当然可以为所欲为。`,
  `一分钟上手，三分钟上头。`, `只有砍树才能点燃熔炉，`), so the batch need not open on the sources' own intros.
- `gist`: one English sentence: what the beat says and what it does for a video.
- `topic` (optional slug): the point the beat makes. Beats of different sources or celebrities that make the same
  point in their own words share a topic (`chop-trees-first`: 吴樾 `开局一定要先砍树`, 许君聪 `开局就得给我砍树`...),
  so each is a stand-in for the others at that place in a script: a topic is one slot of a video's structure, its
  beats the material that fills it. Name topics by the step they cover in the argument, and give a beat that covers
  two steps (`先砍树，把资源拿来升级熔炉`) a topic of its own. Leave out the topic of a beat that only reads after
  one particular line.
- `also` (optional list of topic slugs): further points the beat makes (`三分钟一局，巨爽巨解压` is
  `super-relaxing` and also `quick-rounds`; `木头攒够了生熔炉，熔炉一升温度就上来了` also `furnace` and `heat`).
  A video makes each point once, so a beat that contradicts a point (`开局啊先升级熔炉` against `开局一定要先砍树`)
  lists it too.
- `opening: false`: a body or payoff beat that leans on an earlier line (`妥妥的…`, `这温度一高…`, `那战力…`,
  `照我说的…`, `…也能…`, a fragment with no subject such as `轻松建起冰雪王国。`) never opens a video. Judge every
  body and payoff beat: it opens unless marked. `closing: false`: a beat that leads into what follows (a tally `一分钟上手，三分钟上头`, a clause
  ending on a comma, `不用肝不用氪，`) never ends one.
- Cut where the thought ends. Never separate a question from its answer, a "three things" setup from its list, a
  "why" from its "because", a teaser from its explanation, or a 所以/然后/这/他们 line from what it refers to. Leave
  out noise, greetings aimed at someone else, other people's names and anything a viewer should not hear alone.

## Composing

- Order: the opening clip is any clip that stands alone (a hook, or a body or payoff not marked `opening: false`;
  never a cta); from the second clip on, one or more `body`, then payoffs, at most two `cta` beats (a gift code and
  "点击即玩" may come from two speakers); end on a payoff or cta. A payoff that opens needs joins into the body that
  explains it (`有木材当然可以为所欲为` -> `开局一定要先砍树`).
- Openings vary: `routes --joins` opens each video on the point and source file that have opened fewest videos.
- One celebrity per video by default (their own sources take turns: a b a c); vary the batch by who leads each video.
  Cross to a second celebrity only where the hand-over reads naturally (the new speaker's first line stands alone: a
  payoff, a cta, "接下来听我说") or the first celebrity's beats cannot finish the script. When the user asks for several
  celebrities in every video, the pieces relay between them line by line.
- Many short pieces, not long runs: `routes --joins` prefers videos of six or more clips, single beats over runs, and
  a source file the video has not used yet. There is no clip limit; the 17-30 s window and the shot rules bound it.
- Each point once per video (topic and `also`): never two beats saying "chop trees first" or "three minutes a
  round", never two that contradict each other.
- A tally or parallel payoff ("一分钟上手，三分钟上头") carries its cadence forward: mark it `closing: false` so it
  always leads into a `cta`.
- Vary openings, cameras, routes and closings across the batch, but never trade a join's sense for variety, and
  never cycle the same edit with tiny changes.

## Judging a join

Accept a join only when the second clip follows from the first: it answers, explains, continues a step, proves,
contrasts or pays off. A shared game name or keyword is not a link. Ask:

1. Does B's first sentence answer, continue, prove or pay off A's last sentence, with nothing missing in between?
2. Does A leave something hanging (a question, "三件事", a teaser, a "why") that B does not resolve?
3. Does a word in B lean on something only the source's earlier lines supply (这, 那, 他们, 所以, 但是, 还有)?
   A clip opening with 然后/接着/随后/这样/这 must continue a concrete step or cause A actually named ("先砍树"), not a
   general claim ("攻略都给你整理好了").
4. Is a sign-off, greeting, self-introduction or call to action anywhere but its place?
5. Do two speakers' claims fit (one person's first-person result after another's story is a break)?
6. Does the last clip sound finished: a settled statement, a rhetorical question or a `cta`?

Any "no" rejects the join. Write the `reason` after the join passes: it quotes the words in A and B that connect
them, as spoken.

A topic join (`topic:A` to `topic:B`) accepts every clip ending on topic A followed by every clip opening with topic
B. Accept it only when the point-to-point step reads for every pair: then a batch can fill both slots with any
speaker, which is where the variety comes from. A pair that does not read (a speaker continuing "大聪手把手教你" in
someone else's voice, a cta that only fits its own source) gets a clip join instead or a rejection
(`"reject": true`). A clip-to-clip join overrides a topic join for its pair.

After `check`, read `reports/scripts.md` once per video as a viewer and apply the same questions to the whole video:
a point made twice, two steps that contradict, the same phrase in two mouths. Fix them in the annotations (`also`,
`opening`/`closing`, a topic) or the joins, never by editing the draft by hand.

## Transitions

`relation` is one of `hook_response`, `question_answer`, `answer`, `explanation`, `problem_solution`, `claim_support`,
`cause_effect`, `condition_result`, `progression`, `proof`, `benefit`, `payoff`, `contrast`, `counterpoint`,
`escalation`, `punchline`, `speaker_experience`, `story_resolution`. A reason of fewer than 12 letters or a generic
one ("coherent", "same game", "smooth") is refused.

## Machine checks

`check` refuses a plan that breaks any of these; `routes` only lists routes that pass them all.

- Shape: 1020-1800 frames (17-30 s at 60 fps, natural speed), at least 4 shots where adjacent shots come from different
  source files (so at least 4 clips, no two neighbours from one source; a source may return later, e.g. a b a c
  with 3 sources; camera cuts inside a clip are allowed but do not count), every shot at least 60 frames,
  longest minus shortest shot at most 300 frames. A long single-camera clip (over 6 s) needs partners of similar
  shot length.
- Edges: every clip starts and ends in clean silence (at most 0.45 s before and 0.55 s after the speech) with no
  dissolve, flash or fast motion; user-rejected intervals are refused.
- Sources: at least 3 different source files per video (unless decided otherwise).
- Beats: clips are whole talk beats; the first clip any standalone beat but a cta, then roles in order body,
  payoff, cta; a hook only first.
- Cast: every shot shows a registered celebrity as the visual subject (check the sheets); each celebrity's first
  appearance is a solo shot long enough for the nameplate (48-192 frames, 18 frames between plates).
- Text: a game name from the text library (`无尽冬日`) in at least one clip. A clip opening with 但/但是/不过 needs
  `contrast`/`counterpoint`; 所以 needs `cause_effect`, `condition_result`, `problem_solution` or `payoff`; 这下 needs
  `cause_effect`, `condition_result` or `payoff`; 然后/接着/随后 need `progression`/`escalation`. The first clip cannot
  open with 因为 (unless it holds 所以/因此) or with words that lean on an earlier line (然后, 所以, 但是, 不过, 而且,
  另外, 再, 就是, 为什么呢, 那么...); 只要 without 就/便/即可/才能 needs a following `condition_result`.
- Repeats: no clip opens by restating the previous clip's last clause ("开局一定要先砍树。" then "开局一定要砍树、");
  no clause of four or more characters is spoken twice in a video, nor a near copy of one, nor a phrase of five or
  more characters outside the game name ("温度蹭蹭往上涨" and "战力就蹭蹭往上涨"); each topic (and `also` point) once.
- Flags: the first beat is not `opening: false`, the last beat not `closing: false`; at most two cta beats.
- Result phrases (幸存者一多, 建设末日城镇, 称霸冰原, 战力蹭蹭, 熔炉满级) need a causal transition into them, and after
  砍树 and 熔炉 a survivor bridge (招收幸存者, 吸引更多幸存者...). "so easy" only beside a result phrase; only one clip
  may use 易如反掌/轻轻松松/小意思/so easy. 海量福利 only in the final clip, with a concrete benefit (礼包码, 兑换...).
- Endings: the final clip lasts at least 2 s and is not a bare count ("我收了30个幸存者") or a method step.
- A discovery hook (入坑, 刷到广告, 全球三亿人都在玩...) cannot jump to a mechanic (砍树, 建造, 升级熔炉...) without a
  spoken bridge (怎么玩, 开局, 首先...). A first-person result ("我收了...幸存者") needs a spoken subject (我, 吴樾,
  玩家...) in the previous clip or `speaker_experience`; two speakers' first-person accounts never form one cause and
  result.

Passing the checks does not make a video coherent; that judgment is yours.

## Batch limits

Identity is the source file plus the normalized words; renaming or retiming never makes new content. Reuse up to
these limits needs no permission.

| Limit (per batch) | Max |
| --- | --- |
| identical clip sequence / identical speech | 1 |
| same unit, same words | 30 |
| same opening words, same opening camera | 8 |
| same first two cameras | 10 |
| same closing words | 16 |
| same middle clips (only head/tail changed) | 3 |
| same material at the same position, two videos (share of the shorter video's positions) | 25% |
| same material at one position across the batch | 25% of the videos (at least 2) |
| whole-speech trigram similarity (pairwise) | 0.93 |

The two position limits are the user's control rate (2026-10-28: 25% 同位置结构素材). Positions count from the
opening for the first half of a video and from the ending for the second half, so two endings compare with each
other whatever the lengths; material is the same when the two clips share a unit of one source file. With six-clip
videos two videos may share one piece at one position; N videos need at least N/8 different openings.

## Reaching the count

Fewer coherent videos than requested, or a registered celebrity in no video (`routes --joins` prints the cast
split), means dig deeper, never stop or ask. After each rung re-run `annotate` (when beats changed), `routes` and
`routes --joins`:

1. **Annotate every source.** A source without people and beats contributes nothing; `annotate` lists the ones
   still missing.
2. **Fix unusable beats.** `routes.md` lists beats that cannot be cut alone and why: join them to a neighbour, or
   end the beat a unit earlier or later where the thought still closes.
3. **Cut finer beats.** The smallest self-contained thought is often one sentence. More, shorter beats are more
   routes, and `routes` still joins up to three consecutive beats into one clip. Give each its honest role.
4. **Share topics.** Give every beat that makes a point some other beat makes the same topic, so a topic join opens
   that slot to every speaker. The position limits need several stand-ins per slot.
5. **Accept every coherent join.** Most shortfalls are joins not yet judged. Re-read the joins of each opening and
   closing clip and each topic's followers; add `--mix` for natural cross-celebrity hand-overs.
6. **Weak joins, labelled.** Only then accept the least-weak remaining joins with `"weak": true`: both clips
   complete and on the same topic, the link no stronger than "also about this". A dangling question, teaser,
   pronoun without its referent or a misplaced sign-off is a break, never a weak join. List every weak video by
   `plan_id` in the final summary.
7. **Fallback.** If videos still lack three sources, record the fallback decision from SKILL.md and plan the rest
   with two.

## Visual review

On each cut sheet compare the last frame before a cut with the first frame after it: similar subject position and
size read as one flow; a large jump in framing of the same person, a half-finished gesture, a frame from another
shot, a flash or a transition effect means another clip choice; record a transition the catalog missed in the
annotations ([workflow](workflow.md#after-render)) so no other video uses it. Check that the tagged people are the
ones in the frames and that every talk beat shows the speaker addressing the viewer, alone in frame.
