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


def _title_page(title: str) -> dict:
    return {"properties": {"問題/關鍵字": {"type": "title", "title": [{"plain_text": title}]}}}


class FetchAllFaqQuestionTitlesCacheTests(unittest.TestCase):
    """`_fetch_all_faq_question_titles()` 是 append_unresolved_faq_to_notion() 每次
    寫入未收錄問題前都會呼叫的去重比對，原本沒有快取、每次都整份掃描 FAQ 資料庫，
    正式上線流量一大＋FAQ 候選題量刻意快速增加，會變慢也可能撞到 Notion API
    速率限制，所以補上跟 fetch_faqs_data() 一樣的 CACHE_TTL 快取。"""

    def setUp(self):
        n._cached_faq_titles, n._last_faq_titles_fetch = None, 0

    def tearDown(self):
        n._cached_faq_titles, n._last_faq_titles_fetch = None, 0

    def test_second_call_within_ttl_does_not_refetch(self):
        with patch("services.notion_service.query_notion_database_direct", return_value=[_title_page("加班費怎麼計算？")]) as mock_fetch:
            first = n._fetch_all_faq_question_titles()
            second = n._fetch_all_faq_question_titles()

        self.assertEqual(first, ["加班費怎麼計算？"])
        self.assertEqual(second, ["加班費怎麼計算？"])
        mock_fetch.assert_called_once()

    def test_refetches_after_ttl_expires(self):
        with patch("services.notion_service.query_notion_database_direct", return_value=[_title_page("加班費怎麼計算？")]) as mock_fetch:
            n._fetch_all_faq_question_titles()
            n._last_faq_titles_fetch -= (n.CACHE_TTL + 1)  # 模擬時間已經過了快取視窗
            n._fetch_all_faq_question_titles()

        self.assertEqual(mock_fetch.call_count, 2)

    def test_successful_write_appends_to_cache_without_extra_fetch(self):
        with patch("services.notion_service.query_notion_database_direct", return_value=[_title_page("加班費怎麼計算？")]) as mock_fetch:
            n._fetch_all_faq_question_titles()  # 先讓快取有東西

            mock_response = type("_Resp", (), {"status_code": 201, "text": ""})()
            with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
                 patch("services.notion_service.NOTION_FAQ_DB_ID", "dummy-db-id"), \
                 patch("services.notion_service.requests.post", return_value=mock_response):
                self.assertTrue(n.append_unresolved_faq_to_notion("颱風天上班算加班嗎"))

            # 寫入成功後不用重新整份查詢 Notion，直接把新問題併入快取
            self.assertEqual(mock_fetch.call_count, 1)
            self.assertEqual(
                n._fetch_all_faq_question_titles(),
                ["加班費怎麼計算？", "颱風天上班算加班嗎"],
            )


class AppendUnresolvedQuestionForFollowupTests(unittest.TestCase):
    """跟 append_unresolved_faq_to_notion() 不同：這個函式不做去重，同一個問題
    不同人問，每個人都要各自留下一筆紀錄，才能讓招募專員回頭找到當事人。"""

    def test_writes_display_name_user_id_and_question(self):
        mock_response = type("_Resp", (), {"status_code": 201, "text": ""})()
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_UNRESOLVED_QUESTIONS_DB_ID", "dummy-db-id"), \
             patch("services.notion_service.requests.post", return_value=mock_response) as mock_post:
            result = n.append_unresolved_question_for_followup(
                "颱風天上班算加班嗎", "U1234", display_name="小明"
            )

        self.assertTrue(result)
        _, kwargs = mock_post.call_args
        properties = kwargs["json"]["properties"]
        self.assertEqual(properties["求職者暱稱"]["title"][0]["text"]["content"], "小明")
        self.assertEqual(properties["LINE User ID"]["rich_text"][0]["text"]["content"], "U1234")
        self.assertEqual(properties["提問內容"]["rich_text"][0]["text"]["content"], "颱風天上班算加班嗎")
        self.assertEqual(properties["已回覆"]["checkbox"], False)

    def test_falls_back_to_user_id_when_no_display_name(self):
        mock_response = type("_Resp", (), {"status_code": 201, "text": ""})()
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_UNRESOLVED_QUESTIONS_DB_ID", "dummy-db-id"), \
             patch("services.notion_service.requests.post", return_value=mock_response) as mock_post:
            n.append_unresolved_question_for_followup("颱風天上班算加班嗎", "U1234")

        _, kwargs = mock_post.call_args
        properties = kwargs["json"]["properties"]
        self.assertEqual(properties["求職者暱稱"]["title"][0]["text"]["content"], "U1234")

    def test_does_not_dedupe_same_question_from_different_users(self):
        mock_response = type("_Resp", (), {"status_code": 201, "text": ""})()
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_UNRESOLVED_QUESTIONS_DB_ID", "dummy-db-id"), \
             patch("services.notion_service.requests.post", return_value=mock_response) as mock_post:
            n.append_unresolved_question_for_followup("颱風天上班算加班嗎", "U1111", "小明")
            n.append_unresolved_question_for_followup("颱風天上班算加班嗎", "U2222", "小華")

        self.assertEqual(mock_post.call_count, 2)

    def test_skips_when_db_id_not_configured(self):
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_UNRESOLVED_QUESTIONS_DB_ID", ""), \
             patch("services.notion_service.requests.post") as mock_post:
            result = n.append_unresolved_question_for_followup("颱風天上班算加班嗎", "U1234")

        self.assertFalse(result)
        mock_post.assert_not_called()

    def test_skips_when_missing_user_id(self):
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_UNRESOLVED_QUESTIONS_DB_ID", "dummy-db-id"), \
             patch("services.notion_service.requests.post") as mock_post:
            result = n.append_unresolved_question_for_followup("颱風天上班算加班嗎", "")

        self.assertFalse(result)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
