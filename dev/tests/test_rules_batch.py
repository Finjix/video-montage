"""Editorial text rules and whole-batch reuse limits."""
import unittest

from montage import batch, rules

REASON = "the next clip gives a concrete consequence of the previous one"


def clip(text, duration=3.0, speaker=None):
    return {"text": text, "duration": duration, "first_speaker": speaker, "last_speaker": speaker}


def link(relation):
    return {"relation": relation, "reason": REASON}


class RuleTests(unittest.TestCase):
    def errors(self, texts, relations, games=("无尽冬日",)):
        return rules.output_errors([clip(t) for t in texts], [link(r) for r in relations], list(games))

    def test_clean_output(self):
        self.assertEqual(self.errors(["玩无尽冬日要先砍树", "砍树能点燃熔炉", "温度高了才好玩"], ["explanation", "cause_effect"]), [])

    def test_connectors_need_matching_relations(self):
        self.assertTrue(self.errors(["玩无尽冬日", "但是很多人不会玩"], ["progression"]))
        self.assertFalse(self.errors(["玩无尽冬日很简单", "但是很多人不会玩"], ["contrast"]))
        self.assertTrue(self.errors(["玩无尽冬日很简单", "所以大家都来玩"], ["progression"]))
        self.assertTrue(self.errors(["因为温度很低", "玩无尽冬日要砍树"], ["explanation"]))

    def test_game_name_required(self):
        self.assertTrue(any("game name" in e for e in self.errors(["先砍树", "再升级熔炉"], ["progression"])))

    def test_closing_rules(self):
        self.assertTrue(rules.output_errors([clip("玩无尽冬日"), clip("我收了30个幸存者", 1.0)], [link("payoff")], ["无尽冬日"]))
        self.assertTrue(any("海量福利" in e for e in self.errors(["玩无尽冬日送海量福利", "快来玩"], ["progression"])))

    def test_reasons_must_be_specific(self):
        errors = rules.output_errors([clip("玩无尽冬日"), clip("真好玩呀")], [{"relation": "payoff", "reason": "coherent"}], ["无尽冬日"])
        self.assertTrue(any("concretely" in e for e in errors))

    def test_discovery_needs_gameplay_bridge(self):
        self.assertTrue(self.errors(["全球三亿人都在玩无尽冬日", "砍树获取资源"], ["explanation"]))
        self.assertFalse(self.errors(["全球三亿人都在玩无尽冬日", "开局先砍树获取资源"], ["explanation"]))

    def test_first_person_accounts_of_two_speakers(self):
        clips = [clip("我玩无尽冬日熔炉满级了", speaker="person:a"), clip("我的战力也很高呀", speaker="person:b")]
        self.assertTrue(rules.output_errors(clips, [link("cause_effect")], ["无尽冬日"]))
        self.assertFalse(rules.output_errors(clips, [link("speaker_experience")], ["无尽冬日"]))


def beat_clip(role, kind="talk", frames=300):
    return {"beats": [{"id": "B01", "role": role, "kind": kind, "gist": "x"}], "kind": kind, "frames": frames}


class StructureTests(unittest.TestCase):
    def test_talk_first_narrative_passes(self):
        clips = [beat_clip("hook"), beat_clip("body"), beat_clip("body"), beat_clip("cta")]
        self.assertEqual(rules.structure_errors(clips), [])
        self.assertEqual(rules.structure_errors([beat_clip("body"), beat_clip("body"), beat_clip("payoff")]), [])

    def test_order_and_ending(self):
        self.assertTrue(rules.structure_errors([beat_clip("body"), beat_clip("hook"), beat_clip("cta")]))
        self.assertTrue(any("cta" in e for e in rules.structure_errors([beat_clip("hook"), beat_clip("cta"), beat_clip("body")])))
        self.assertTrue(any("needs at least one body" in e for e in rules.structure_errors([beat_clip("hook"), beat_clip("cta")])))
        self.assertTrue(any("must end" in e for e in rules.structure_errors([beat_clip("hook"), beat_clip("body")])))
        self.assertTrue(any("more than one cta" in e for e in rules.structure_errors([beat_clip("body"), beat_clip("cta"), beat_clip("cta")])))

    def test_scene_beats_are_refused(self):
        base = [beat_clip("hook"), beat_clip("body"), beat_clip("body"), beat_clip("payoff")]
        self.assertTrue(any("acted scene" in e for e in rules.structure_errors(base[:1] + [beat_clip("body", "scene")] + base[2:])))

    def test_clip_limit(self):
        clips = [beat_clip("hook")] + [beat_clip("body")] * 4 + [beat_clip("payoff")]
        self.assertTrue(any("at most 5" in e for e in rules.structure_errors(clips)))


def output(pid, texts, sources=("a", "b", "c"), families=None):
    clips = [{"sha256": sources[i % len(sources)], "unit_texts": [t], "text": t} for i, t in enumerate(texts)]
    shots = [{"family": f} for f in (families or [f"{pid}:{i}" for i in range(len(texts))])]
    return {"plan_id": pid, "clips": clips, "shots": shots}


class BatchTests(unittest.TestCase):
    def test_identical_outputs_rejected(self):
        texts = ["开头一句话", "中间第二句", "中间第三句", "结尾一句话"]
        errors, _ = batch.audit([output("P1", texts), output("P2", texts)], {})
        self.assertTrue(any("identical" in e for e in errors), errors)

    def test_opening_limit(self):
        def openings(count):
            return [output(f"P{i}", ["同一个开头", f"中间{i}甲乙丙丁", f"中间{i}戊己庚辛", f"结尾{i}壬癸子丑"]) for i in range(count)]
        errors, _ = batch.audit(openings(8), {})
        self.assertFalse(any("opening words" in e for e in errors), errors)
        errors, _ = batch.audit(openings(9), {})
        self.assertTrue(any("opening words" in e for e in errors), errors)

    def test_head_tail_only_variant(self):
        middle = ["相同的中间一", "相同的中间二"]
        variants = [output(f"P{i}", [f"开头{c}", *middle, f"结尾{c}"]) for i, c in enumerate("甲乙丙丁")]
        errors, _ = batch.audit(variants, {})
        self.assertTrue(any("middle" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
