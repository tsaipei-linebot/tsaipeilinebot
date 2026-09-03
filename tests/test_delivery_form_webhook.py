import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.form_webhook import extract_answer, other_answers


class ExtractAnswerTests(unittest.TestCase):
    def test_finds_value_by_keyword_contained_in_title(self):
        answers = {"姓名": "王小明", "聯絡電話": "0912345678"}
        self.assertEqual(extract_answer(answers, "姓名"), "王小明")
        self.assertEqual(extract_answer(answers, "電話"), "0912345678")

    def test_missing_keyword_returns_empty_string(self):
        answers = {"姓名": "王小明"}
        self.assertEqual(extract_answer(answers, "電話"), "")

    def test_empty_answers_returns_empty_string(self):
        self.assertEqual(extract_answer({}, "姓名"), "")
        self.assertEqual(extract_answer(None, "姓名"), "")

    def test_strips_whitespace(self):
        answers = {"姓名": "  王小明  "}
        self.assertEqual(extract_answer(answers, "姓名"), "王小明")


class OtherAnswersTests(unittest.TestCase):
    def test_excludes_name_and_phone_keys(self):
        answers = {
            "姓名": "王小明",
            "聯絡電話": "0912345678",
            "可配合天數": "一周可配合5天(含)以上",
            "配送縣市選擇(則一)": "新竹",
        }
        result = other_answers(answers)
        self.assertEqual(
            result,
            {"可配合天數": "一周可配合5天(含)以上", "配送縣市選擇(則一)": "新竹"},
        )

    def test_empty_answers_returns_empty_dict(self):
        self.assertEqual(other_answers({}), {})
        self.assertEqual(other_answers(None), {})


if __name__ == "__main__":
    unittest.main()
