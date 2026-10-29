import json
import tempfile
import unittest
from pathlib import Path

from montage import text_library

LIBRARY = {"entries": [{"id": "term:jin-zi-666", "category": "term", "text": "金子666"},
                       {"id": "term:jin-zi-888", "category": "term", "text": "金子888"}]}


def chars(text: str, start: float = 0.0) -> list[dict]:
    """One word per character, punctuation stuck to the character before it (as the recognizer returns them)."""
    words = []
    for ch in text:
        if ch in "，。？！" and words:
            words[-1]["word"] += ch
        else:
            words.append({"word": ch, "start": start + len(words) * 0.2, "end": start + len(words) * 0.2 + 0.18})
    return words


class Respell(unittest.TestCase):
    def test_spelled_out_digits_take_the_library_spelling(self):
        words = text_library.respell(chars("金子六六六，金子八八八，领取"), text_library.spellings(LIBRARY))
        self.assertEqual([w["word"] for w in words], ["金子666，", "金子888，", "领", "取"])
        self.assertEqual((words[0]["start"], words[0]["end"]), (0.0, 0.98))
        self.assertEqual(words[1]["start"], 1.0)

    def test_back_to_back_codes_and_ascii_digits(self):
        words = text_library.respell(chars("金子六六六金子888"), text_library.spellings(LIBRARY))
        self.assertEqual([w["word"] for w in words], ["金子666", "金子888"])

    def test_other_numbers_are_left_alone(self):
        words = text_library.respell(chars("金子六六，六六六"), text_library.spellings(LIBRARY))
        self.assertEqual("".join(w["word"] for w in words), "金子六六，六六六")

    def test_a_job_created_before_an_entry_still_gets_it(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "config").mkdir()
            own = {"schema": "video-montage-text-library/v1",
                   "entries": [{"id": "person:wu-yue", "category": "person", "text": "吴樾"}]}
            (job / "config/text_library.json").write_text(json.dumps(own, ensure_ascii=False), encoding="utf-8")
            texts = [e["text"] for e in text_library.load(job)["entries"]]
        self.assertEqual(texts.count("吴樾"), 1)
        self.assertIn("金子666", texts)
        self.assertEqual(len(texts), len(set(texts)))


if __name__ == "__main__":
    unittest.main()
