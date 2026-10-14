"""Speech-bound subtitle proposals, measured at the calibrated display sizes.

Punctuation and pauses propose boundaries; they are not displayed or allowed to
change spoken text. The agent reviews the resulting phrases before packaging.
"""
from __future__ import annotations

import functools
import re

from PIL import ImageFont

from montage import text_library
from montage.media.mix import FINAL_SPEED
from montage.packaging import fonts as subtitle_fonts
MIN_READ_MS = 800
MAX_LINE_CHARACTERS = 10
TARGET_READ_MS = (1200, 2500)
POLICY = "semantic-reading/v1"
GAME_NAMES = ("无尽冬日", "无尽动日", "无尽冬至")
PHRASES = (
    "幸存者", "熔炉", "发展建设", "三分钟一局", "三分钟", "中年人", "解压神器",
    "庇护所", "冰雪末日", "沉浸感", "明星玩家", "明信玩家", "吴岳", "吴樾",
    "升级", "温度", "砍树", "打猎", "挖矿", "资源", "厨房", "快乐", "巨爽",
    "巨解压", "聚爽", "聚解压", "然后", "不断", "获取", "建造", "建设",
    "收集", "收留", "几个", "更多", "各种各样", "全国", "开局", "入坑", "拿来", "把你",
    "的确", "了解", "了不起", "得到", "得意", "得益", "过程", "过去", "过来",
)
CLAUSE_HEADS = (
    "你得", "只要", "只有", "不断", "提升温度", "那战力", "这才能", "现在玩",
    "因为", "温度高了", "我是", "我在", "然后", "这是", "才入坑", "全部",
    "几个", "更多", "都在", "可是", "就得", "给你", "所以", "把你",
)
DEPENDENT_END = re.compile(r"(?:的|地|得|把|被|让|给|和|与|在|将|才|就|才能|这是|所有)$")
BAD_HEAD = re.compile(r"^(?:的(?!确)|了(?!解|不起)|着|过(?!程|去|来)|们|地|得(?!到|意|益))")
TOKEN = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")
QUANTITY = re.compile(r"(?:[0-9]+|[一二三四五六七八九十百千万亿两]+)(?:分钟|小时|秒|个|人|局|次|年|天|亿|万)?")


@functools.lru_cache(maxsize=16)
def _font(path: str, pixels: int):
    return ImageFont.truetype(path, pixels)


@functools.lru_cache(maxsize=8192)
def fits(text: str, names: tuple[str, ...] = GAME_NAMES, font: str | None = None,
         max_width: float = .9) -> bool:
    """Use the renderer's advances, flower gaps and outline/glow reservation."""
    if character_count(text) > MAX_LINE_CHARACTERS:
        return False
    pattern = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    games = list(re.finditer(pattern, text)) if pattern else []
    pieces, cursor = [], 0
    for game in games:
        if game.start() > cursor:
            pieces.append((text[cursor:game.start()], 8))
        pieces.append((game[0], 9))
        cursor = game.end()
    if cursor < len(text):
        pieces.append((text[cursor:], 8))
    for key in ([font] if font else subtitle_fonts.FONTS):
        largest = max((subtitle_fonts.pixels(key, size) for _, size in pieces), default=108)
        gap = max(10, round(largest * .09))
        width = sum(_font(subtitle_fonts.FONTS[key]["path"], subtitle_fonts.pixels(key, size)).getlength(part)
                    for part, size in pieces)
        width += gap * max(0, len(pieces) - 1)
        if width > 1440 * max_width - round(largest * .4):
            return False
    return True


def character_count(text: str) -> int:
    """Count displayed characters, including digits and punctuation, sans spaces."""
    return sum(not c.isspace() for c in text)


def require_line_limit(text: str) -> None:
    if character_count(text) > MAX_LINE_CHARACTERS:
        raise ValueError("subtitle line exceeds 10 characters; split into sequential semantic captions")


def _shot_stream(shot: dict):
    offset = shot.get("output_in_frame", 0) / 60
    letters, times, spaces = [], [], set()
    punctuation, word_edges = {}, set()
    for segment in shot["asr"]["segments"]:
        words = segment.get("words") or [{"word": segment["text"], "start": segment["start"], "end": segment["end"]}]
        for word in words:
            raw = word["word"]
            matches = list(TOKEN.finditer(raw))
            if not matches:
                if letters and re.search(r"[。！？!?]", raw):
                    punctuation[len(letters)] = "strong"
                continue
            # Whisper often stores a leading space on English words.
            if letters and raw[:matches[0].start()].isspace() and letters[-1].isascii() and matches[0][0].isascii():
                spaces.add(len(letters))
            for index, match in enumerate(matches):
                if index and raw[matches[index - 1].end():match.start()].isspace():
                    spaces.add(len(letters))
                letters.append(match[0])
                start = word["start"] + (word["end"] - word["start"]) * index / len(matches)
                end = word["start"] + (word["end"] - word["start"]) * (index + 1) / len(matches)
                times.append((start + offset, end + offset))
                tail = raw[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(raw)]
                if re.search(r"[。！？!?]", tail):
                    punctuation[len(letters)] = "strong"
                elif re.search(r"[,，;；:：、]", tail):
                    punctuation[len(letters)] = "weak"
            word_edges.add(len(letters))
    return "".join(letters), times, spaces, punctuation, word_edges


def split_shot(shot: dict, *, names: tuple[str, ...] = GAME_NAMES, font: str | None = None,
               protected_terms: tuple[str, ...] = ()) -> list[dict]:
    text, times, spaces, punctuation, word_edges = _shot_stream(shot)
    if not text:
        return []
    forbidden = set()
    for phrase in (*names, *PHRASES):
        for match in re.finditer(re.escape(phrase), text):
            forbidden.update(range(match.start() + 1, match.end()))
    for match in re.finditer(r"[0-9A-Za-z]+", text):
        forbidden.update(i for i in range(match.start() + 1, match.end()) if i not in spaces)
    for match in QUANTITY.finditer(text):
        forbidden.update(range(match.start() + 1, match.end()))
    # Genuine punctuation remains available even when a phrase dictionary match
    # accidentally spans two utterances.
    forbidden.difference_update(punctuation)
    for phrase in protected_terms:
        for match in re.finditer(re.escape(phrase), text):
            forbidden.update(range(match.start() + 1, match.end()))
    heads = {m.start() for phrase in CLAUSE_HEADS for m in re.finditer(re.escape(phrase), text)}
    strong = sorted({0, len(text), *(i for i, kind in punctuation.items() if kind == "strong")})

    def caption(start, end):
        return "".join((" " if i in spaces and i > start else "") + text[i] for i in range(start, end))

    rows = []
    for left, right in zip(strong, strong[1:]):
        clause_edges = sorted({left, right, *(i for i in punctuation if left < i < right)})
        costs = {right: (0., [])}
        for start in range(right - 1, left - 1, -1):
            choices = []
            for end in range(start + 1, right + 1):
                candidate = caption(start, end)
                if not fits(candidate, names, font):
                    break
                if end not in costs or end in forbidden or (end < right and BAD_HEAD.match(text[end:])):
                    continue
                crossed = [i for i in clause_edges if start < i < end]
                # Complete adjacent clauses may share one cue. A clause tail
                # may not be attached to only the beginning of the next clause.
                if crossed and (start not in clause_edges or end not in clause_edges):
                    continue
                duration = (times[end - 1][1] - times[start][0]) * 1000 / FINAL_SPEED
                penalty = 8. + max(0, MIN_READ_MS - duration) / 15
                penalty += max(0, TARGET_READ_MS[0] - duration) / 450
                penalty += max(0, duration - TARGET_READ_MS[1]) / 800
                if end < right:
                    pause = max(0, times[end][0] - times[end - 1][1])
                    penalty += (0 if end in punctuation else 2 if end in heads else 5 if end in word_edges else 9)
                    penalty -= min(4., pause * 12)
                    if DEPENDENT_END.search(text[start:end]):
                        penalty += 22
                suffix, bounds = costs[end]
                choices.append((penalty + suffix, [(start, end), *bounds]))
            if choices:
                costs[start] = min(choices, key=lambda choice: (choice[0], len(choice[1])))
        if left not in costs:
            raise ValueError("no readable single-line subtitle segmentation; submit reviewed subtitle TXT")
        for start, end in costs[left][1]:
            rows.append({"start": times[start][0], "end": times[end - 1][1], "text": caption(start, end)})
    return rows


def caption_rows(asr: dict, *, names: tuple[str, ...] = GAME_NAMES, font: str | None = None) -> list[dict]:
    protected = text_library.protected_terms(asr.get("text_library"))
    observations = asr.get("shot_observations") or [{"output_in_frame": 0, "asr": asr}]
    if asr.get("review_basis") == "agent-speech-review/v1":
        if any(not isinstance(shot.get("reviewed_asr"), dict) for shot in observations):
            raise ValueError("reviewed caption timing requires every shot's reviewed speech")
        # Keep original recognition in QC; captions use the approved text/times.
        observations = [{**shot, "asr": shot["reviewed_asr"]} for shot in observations]
    result = [row for shot in observations for row in split_shot(shot, names=names, font=font, protected_terms=protected)]
    text_library.require_complete_terms(result, protected, lambda text: "".join(TOKEN.findall(text)))
    return result


def reading_issues(cues: list[dict], *, speed: float = FINAL_SPEED) -> list[dict]:
    issues = []
    for index, cue in enumerate(cues, 1):
        if character_count(cue["text"]) > MAX_LINE_CHARACTERS:
            issues.append({"index": index, "code": "line_character_limit", "text": cue["text"]})
        duration = (cue["end_ms"] - cue["start_ms"]) / speed
        if duration < MIN_READ_MS:
            issues.append({"index": index, "code": "short_reading", "final_duration_ms": round(duration)})
        if BAD_HEAD.match(cue["text"]):
            issues.append({"index": index, "code": "dependent_head", "text": cue["text"]})
    return issues
