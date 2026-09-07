import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
