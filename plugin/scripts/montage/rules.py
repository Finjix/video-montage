"""Machine-checked editorial rules over a resolved output (clip texts + transition relations).

They catch known failure patterns; the agent still judges coherence from the full transcript.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

RELATIONS = {
    "hook_response", "question_answer", "answer", "explanation", "problem_solution", "claim_support",
    "cause_effect", "condition_result", "progression", "proof", "benefit", "payoff", "contrast",
    "counterpoint", "escalation", "punchline", "speaker_experience", "story_resolution",
}
CONNECTORS = {
    "但是": {"contrast", "counterpoint"}, "不过": {"contrast", "counterpoint"}, "但": {"contrast", "counterpoint"},
    "所以": {"cause_effect", "condition_result", "problem_solution", "payoff"},
    "这下": {"cause_effect", "condition_result", "payoff"},
    "然后": {"progression", "escalation"}, "接着": {"progression", "escalation"}, "随后": {"progression", "escalation"},
}
CAUSAL = {"cause_effect", "condition_result", "problem_solution", "payoff"}
MIN_CLOSING_SECONDS = 2.0
NUMERIC_ENDINGS = [r"^(?:这周)?我收了(?:十|三十|[0-9]+)个幸存者$"]
METHOD_ENDINGS = [r"^把资源拿来升级熔炉$", r"^最要紧的是什么点燃熔炉$", r"^.*开局一定要先砍树.*提升温度$",
                  r"^.*要想富先砍树.*升级熔炉$", r"^.*发展建设$"]
REDUNDANT = ["易如反掌", "轻轻松松", "小意思", "so easy"]
PAYOFF_TAGS = ["so easy", "soeasy"]
RESULT_ANCHORS = ["幸存者一多", "建设末日城镇", "称霸冰原", "战力蹭蹭", "熔炉满级"]
ACQUISITION_BRIDGES = ["招收幸存者", "吸引更多的幸存者", "吸引更多幸存者", "幸存者才能活下来", "幸存者不会被冻死"]
FIRST_PERSON_RESULTS = [r"^我(?:收了|招收了).+幸存者"]
FIRST_PERSON_SUBJECTS = ["我", "吴樾", "玩家", "樾哥"]
GENERIC_BENEFITS = ["海量福利"]
CONCRETE_BENEFITS = ["礼包码", "福利码", "输入", "兑换", "666", "888"]
DISCOVERY = r"入坑|刷.{0,8}广告|全球.{0,12}(?:亿|都在玩)|(?:亿|千万|百万)人.{0,6}玩"
MECHANISM = r"砍树|获取资源|建造|升级熔炉|提升温度|吸引.{0,6}幸存者"
GAMEPLAY_BRIDGE = r"怎么(?:玩|上手|开始)|如何(?:玩|上手|开始)|玩法|开局|一上来|进去(?:以后|之后|一看)|首先|先(?:让|把|收集|采集|砍|升级|建)"
PERSONAL = r"我(?!们)|本人|(?:这个|该|这)(?:账号|帐号|个号)"
# Words that lean on a line the viewer has not heard: a video cannot open on them.
LEANING_OPENINGS = re.compile(r"^(?:然后|接着|随后|所以|因此|于是|但是|但(?!凡)|不过|可是|而且|并且|另外|其次|同时|还有(?!谁|人)"
                              r"|再(?!也)|就是|为什么呢|为啥呢|这下|那么"
                              r"|有啊|有呀|当然有|当然了|那当然|那可不|没错|错(?!过))")   # answers to an unheard line
RESTATED = 0.75   # similarity at which a clip's opening restates the previous clip's closing words
ROLE_RANK = {"hook": 0, "body": 1, "payoff": 2, "cta": 3}
GENERIC_REASONS = re.compile(r"(?:sametopic|samegame|coherent|smooth|natural|flows?|continues?|good|ok|fine|"
                             r"同一(?:个)?(?:游戏|人物|话题|主角)|语义连贯|逻辑连贯|衔接自然|增加(?:新的)?信息|接着介绍|继续介绍|通过审核)+")


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def person_id(value) -> bool:
    return (isinstance(value, str) and re.fullmatch(r"person:[a-z0-9][a-z0-9_-]*", value) is not None
            and value.split(":", 1)[1] not in {"unknown", "unidentified", "uncertain", "anonymous"})


def specific(reason) -> bool:
    text = normalize(reason)
    return len(text) >= 12 and not GENERIC_REASONS.fullmatch(text)


def contains(text: str, terms: list[str]) -> bool:
    value = normalize(text)
    return any(normalize(term) in value for term in terms)


def matches(text: str, patterns: list[str]) -> bool:
    value = normalize(text)
    return any(re.search(pattern, value) for pattern in patterns)


def structure_errors(clips: list[dict]) -> list[str]:
    """Talk-first and narrative order over the beats of an output (clips carry beats, kind and frames)."""
    errors = []
    scenes = [number for number, clip in enumerate(clips, 1) if clip["kind"] == "scene"]
    if scenes:
        errors.append(f"clips {', '.join(map(str, scenes))} contain acted scene beats; videos are talk to the viewer only")
    beats = [(number, beat) for number, clip in enumerate(clips, 1) for beat in clip["beats"]]
    previous = None
    for position, (number, beat) in enumerate(beats):
        if previous and ROLE_RANK[beat["role"]] < ROLE_RANK[previous["role"]]:
            errors.append(f"clip {number} {beat['id']} is a {beat['role']} beat after a {previous['role']} beat; "
                          "the order is hook, body, payoff, cta")
        if beat["role"] == "hook" and position:
            errors.append(f"clip {number} {beat['id']}: a hook beat can only open the video")
        previous = beat
    roles = [beat["role"] for _, beat in beats]
    if roles.count("cta") > 1:
        errors.append("more than one cta beat")
    if "body" not in roles:
        errors.append("needs at least one body beat between its opening and its ending")
    if roles and roles[-1] not in {"payoff", "cta"}:
        errors.append(f"the last beat is a {roles[-1]} beat; the video must end on a payoff or cta beat")
    return errors


def output_errors(clips: list[dict], transitions: list[dict], games: list[str]) -> list[str]:
    """Clips carry text, duration and first/last speaker; transitions carry relation and reason."""
    errors = []
    if len(transitions) != len(clips) - 1:
        return [f"needs {len(clips) - 1} transitions (one per adjacent clip pair), got {len(transitions)}"]
    seen = set()
    for index, clip in enumerate(clips):
        text = normalize(clip["text"])
        where = f"clip {index + 1}"
        if not text or text in seen:
            errors.append(f"{where}: empty or repeated speech")
        seen.add(text)
        relation = transitions[index - 1].get("relation") if index else None
        for connector, allowed in CONNECTORS.items():
            if text.startswith(connector):
                if relation not in allowed:
                    errors.append(f"{where} starts with {connector}; the transition into it must be one of {sorted(allowed)}")
                break
        if index == 0 and text.startswith("因为") and not any(term in text for term in ("所以", "因此")):
            errors.append("clip 1 opens with 因为 without its result")
        if index == 0 and (lean := LEANING_OPENINGS.match(text)):
            errors.append(f"clip 1 opens with {lean[0]}, which leans on an earlier line; open on words that stand alone")
        if text.startswith("只要") and not any(term in text for term in ("就", "便", "即可", "才能")):
            if index >= len(transitions) or transitions[index].get("relation") != "condition_result":
                errors.append(f"{where} opens with 只要; the next transition must be condition_result")
        if contains(clip["text"], PAYOFF_TAGS) and not contains(clip["text"], RESULT_ANCHORS):
            errors.append(f"{where}: 'so easy' needs a result phrase in the same clip")
        if contains(clip["text"], RESULT_ANCHORS):
            if index and relation not in CAUSAL:
                errors.append(f"{where} states a result; the transition into it must be causal {sorted(CAUSAL)}")
            prior = " ".join(c["text"] for c in clips[:index])
            if contains(prior, ["砍树"]) and contains(prior, ["熔炉"]) and not contains(prior, ACQUISITION_BRIDGES):
                errors.append(f"{where}: a result after 砍树/熔炉 needs a survivor-acquisition bridge (招收幸存者, 吸引更多幸存者...)")
        if matches(clip["text"], FIRST_PERSON_RESULTS) and index:
            if not contains(clips[index - 1]["text"], FIRST_PERSON_SUBJECTS) and relation != "speaker_experience":
                errors.append(f"{where}: a first-person result needs a spoken subject (我, 吴樾, 玩家...) in the previous clip or speaker_experience")
        if contains(clip["text"], GENERIC_BENEFITS):
            if index != len(clips) - 1:
                errors.append(f"{where}: 海量福利 only in the final clip")
            if not contains(clip["text"], CONCRETE_BENEFITS):
                errors.append(f"{where}: 海量福利 needs a concrete benefit (礼包码, 兑换...)")
    units = [(number, normalize(text)) for number, clip in enumerate(clips, 1) for text in clip.get("unit_texts") or [clip["text"]]]
    spoken = {}
    for number, text in units:
        if len(text) >= 4 and spoken.setdefault(text, number) != number:
            errors.append(f"clip {number} repeats a clause of clip {spoken[text]}: {text}")
    for index, transition in enumerate(transitions, 1):
        where = f"transition {index}"
        left, right = clips[index - 1], clips[index]
        tail = normalize((left.get("unit_texts") or [left["text"]])[-1])
        head = normalize((right.get("unit_texts") or [right["text"]])[0])
        if min(len(tail), len(head)) >= 4 and tail != head and SequenceMatcher(None, tail, head).ratio() >= RESTATED:
            errors.append(f"{where}: clip {index + 1} opens by restating the end of clip {index} ({tail} / {head})")
        if transition.get("relation") not in RELATIONS:
            errors.append(f"{where}: relation must be one of {sorted(RELATIONS)}")
        if not specific(transition.get("reason")):
            errors.append(f"{where}: reason must say concretely what links the two clips")
        if (re.search(DISCOVERY, normalize(left["text"])) and re.search(MECHANISM, normalize(right["text"]))
                and not re.search(GAMEPLAY_BRIDGE, normalize(left["text"] + right["text"]))):
            errors.append(f"{where}: a discovery hook cannot jump to a mechanic without a spoken gameplay bridge (怎么玩, 开局, 首先...)")
        if (left.get("last_speaker") and right.get("first_speaker") and left["last_speaker"] != right["first_speaker"]
                and re.search(PERSONAL, left["text"]) and re.search(PERSONAL, right["text"])
                and transition.get("relation") not in {"speaker_experience", "contrast", "counterpoint", "explanation",
                                                       "claim_support", "proof"}):
            errors.append(f"{where}: two speakers' first-person accounts cannot form one cause/result")
    last = clips[-1]
    if last["duration"] < MIN_CLOSING_SECONDS:
        errors.append(f"final clip lasts {last['duration']:.1f} s (< {MIN_CLOSING_SECONDS} s)")
    if matches(last["text"], NUMERIC_ENDINGS) or matches(last["text"], METHOD_ENDINGS):
        errors.append("final clip is a bare count or a method beat, not a payoff")
    if sum(contains(clip["text"], REDUNDANT) for clip in clips) > 1:
        errors.append("only one clip may use 易如反掌/轻轻松松/小意思/so easy")
    if games and not any(contains(clip["text"], [name]) for clip in clips for name in games):
        errors.append(f"no clip speaks a game name ({'/'.join(games)})")
    return errors
