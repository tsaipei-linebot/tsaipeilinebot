import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)

from services import notion_service as n


class CleanTextForSearchTests(unittest.TestCase):
    def test_normalizes_traditional_variant_and_case(self):
        # 「台」跟「臺」要視為同一個字，英文要不分大小寫
        self.assertEqual(n.clean_text_for_search("台北MOMO"), n.clean_text_for_search("臺北momo"))

    def test_strips_punctuation_and_whitespace(self):
        self.assertEqual(n.clean_text_for_search("美光(桃園) - 作業員"), "美光桃園作業員")

    def test_empty_input(self):
        self.assertEqual(n.clean_text_for_search(""), "")
        self.assertEqual(n.clean_text_for_search(None), "")


class SanitizeUriTests(unittest.TestCase):
    def test_valid_https_url_passthrough(self):
        self.assertEqual(n.sanitize_uri("https://example.com/apply"), "https://example.com/apply")

    def test_invalid_scheme_falls_back(self):
        self.assertEqual(n.sanitize_uri("javascript:alert(1)"), "https://tsaipei.netlify.app/#jobs")

    def test_empty_or_none_falls_back(self):
        self.assertEqual(n.sanitize_uri(""), "https://tsaipei.netlify.app/#jobs")
        self.assertEqual(n.sanitize_uri(None), "https://tsaipei.netlify.app/#jobs")


class ParseNotionPropertyTests(unittest.TestCase):
    def test_title_property(self):
        prop = {"type": "title", "title": [{"plain_text": "美光作業員"}]}
        self.assertEqual(n.parse_notion_property(prop), "美光作業員")

    def test_rich_text_property(self):
        prop = {"type": "rich_text", "rich_text": [{"plain_text": "月薪 3.2 萬起"}]}
        self.assertEqual(n.parse_notion_property(prop), "月薪 3.2 萬起")

    def test_select_property_with_none(self):
        prop = {"type": "select", "select": None}
        self.assertEqual(n.parse_notion_property(prop), "")

    def test_non_dict_input(self):
        self.assertEqual(n.parse_notion_property("已經是純文字"), "已經是純文字")


class DuplicateFaqQuestionTests(unittest.TestCase):
    def setUp(self):
        self.existing_titles = ["發薪日是什麼時候", "特休怎麼算", "薪水"]

    def test_exact_and_containment_matches_are_duplicates(self):
        self.assertTrue(n._is_duplicate_faq_question("發薪日是什麼時候", self.existing_titles))
        self.assertTrue(n._is_duplicate_faq_question("請問發薪日是什麼時候呢", self.existing_titles))

    def test_unrelated_question_is_not_duplicate(self):
        self.assertFalse(n._is_duplicate_faq_question("加班費怎麼算", self.existing_titles))

    def test_short_existing_title_ignored_to_avoid_false_positive(self):
        # 「薪水」只有 2 個字，不該讓任何提到「薪」的問題都被判成重複
        self.assertFalse(n._is_duplicate_faq_question("薪水多少", self.existing_titles))

    def test_empty_question_is_not_duplicate(self):
        self.assertFalse(n._is_duplicate_faq_question("", self.existing_titles))


def _faq_page(question: str, answer: str = "", status: str = "") -> dict:
    return {
        "properties": {
            "問題/關鍵字": {"type": "title", "title": [{"plain_text": question}]},
            "標準回覆內容": {"type": "rich_text", "rich_text": [{"plain_text": answer}] if answer else []},
            "啟用狀態": {"type": "multi_select", "multi_select": [{"name": status}] if status else []},
        }
    }


class FetchPendingFaqCandidatesTests(unittest.TestCase):
    def test_blank_answer_and_blank_status_is_pending(self):
        pages = [_faq_page("加班費怎麼計算？")]
        with patch("services.notion_service.query_notion_database_direct", return_value=pages):
            self.assertEqual(n.fetch_pending_faq_candidates(), ["加班費怎麼計算？"])

    def test_answered_question_is_not_pending(self):
        pages = [_faq_page("發薪日是哪天", answer="每月5號")]
        with patch("services.notion_service.query_notion_database_direct", return_value=pages):
            self.assertEqual(n.fetch_pending_faq_candidates(), [])

    def test_manually_marked_disabled_is_not_pending_even_without_answer(self):
        # 同仁審核後決定不採用，即使還沒填答案也手動設「停用」，不該再出現在候選清單
        pages = [_faq_page("照片", status="停用")]
        with patch("services.notion_service.query_notion_database_direct", return_value=pages):
            self.assertEqual(n.fetch_pending_faq_candidates(), [])

    def test_multiple_pages_only_pending_ones_returned(self):
        pages = [
            _faq_page("加班費怎麼計算？"),
            _faq_page("發薪日是哪天", answer="每月5號"),
            _faq_page("照片", status="停用"),
            _faq_page("颱風天上班算加班嗎"),
        ]
        with patch("services.notion_service.query_notion_database_direct", return_value=pages):
            self.assertEqual(
                n.fetch_pending_faq_candidates(),
                ["加班費怎麼計算？", "颱風天上班算加班嗎"],
            )


if __name__ == "__main__":
    unittest.main()
