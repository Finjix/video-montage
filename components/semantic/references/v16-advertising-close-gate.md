# V18 advertising-close and review-provenance gate

Read this reference for every short-form celebrity product montage.

## Forward semantic spine

Prefer one recoverable line: product or substantive question, problem/premise, answer or mechanism, concrete result, advertising close. A later-state hook such as a high combat-power claim may return to mechanics only through an explicit question or explanation bridge. A numeric survivor count is evidence inside the story, not an advertising close.

Every segment declares an integer `purpose_contract.narrative_stage`. Stages must not decrease. A result used as a hook remains opening stage zero; its following question explains why the edit returns to mechanics.

## Closing contract

When `require_closing_payoff=true`, the final segment must:

- use an allowed closing function such as `payoff`, `benefit`, `proof`, `punchline`, `release_close` or `story_resolution`;
- set `closing_payoff=true` and quote its actual closing words in `closing_quote`;
- add a real advertising result or release close instead of a numeric anecdote or unfinished tutorial step.

For the Wu Yue source family, `我收了十个幸存者` and `我收了三十个幸存者` may appear only as intermediate evidence. A final line ending at chopping trees, lighting/upgrading the furnace, raising temperature or generic development is incomplete. Accepted close families include a demonstrated achievement, survival/town outcome, `轻松建起冰雪王国`, `这还不是轻轻松松`, `三分钟一局，巨爽巨解压`, or `这才是中年人的解压神器`, provided the preceding chain earns that close.

The sentence `我也不装了，我摊牌了，我熔炉满级了` is hard-excluded for this source family. `轻轻松松`, `so easy`, `易如反掌` and `小意思` are one equivalence group and may not be stacked in one edit.

## Sol review provenance

Every `semantic-sol-output-review/v18` records model provenance, review start/end, actual audio duration and normal-speed listened duration. The wall-clock review interval and listened duration must each cover the actual audio duration. A batch cannot claim full normal-speed listening when its total review interval is shorter than the total rendered audio.

Sol must report exact segment text and classify the last segment as a closing function. Boolean-only or template-identical pass forms are not release evidence.
