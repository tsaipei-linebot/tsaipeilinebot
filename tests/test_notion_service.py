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


class LocationSearchTextTests(unittest.TestCase):
    """_location_search_text 是上線試營運後實測發現的修正：地區判斷只能依據同仁
    在 Notion 實際勾選的「縣市」「行政區」這兩個結構化欄位，不能沿用 _search_text
    （包含「工作內容(對外)」等自由文字）——實際案例是蝦皮門市的「行政區」沒有
    勾選八德，但工作內容文字剛好提到「八德」（例如地址上的路名），求職者問
    「八德有沒有缺額」時被誤判成有。"""

    def setUp(self):
        n._cached_jobs, n._last_jobs_fetch = None, 0

    def tearDown(self):
        n._cached_jobs, n._last_jobs_fetch = None, 0

    def _make_job_page(self):
        return {
            "id": "page-1",
            "properties": {
                "職缺名稱": {"type": "title", "title": [{"plain_text": "蝦皮門市"}]},
                "縣市": {"type": "rich_text", "rich_text": [{"plain_text": "桃園市"}]},
                "行政區": {"type": "rich_text", "rich_text": [{"plain_text": "蘆竹區,龜山區"}]},
                "工作內容(對外)": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "門市地址鄰近八德路口，交通便利"}],
                },
                "狀態": {"type": "select", "select": {"name": "招募中"}},
            },
        }

    def test_location_search_text_excludes_free_text_mentions(self):
        with patch("services.notion_service.query_notion_database_direct", return_value=[self._make_job_page()]):
            jobs = n.fetch_jobs_data()

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        # 「八德」只出現在工作內容的自由文字裡（路名），行政區實際上是蘆竹/龜山，
        # 地區比對專用的欄位不該包含「八德」。
        self.assertNotIn("八德", job["_location_search_text"])
        self.assertIn("蘆竹", job["_location_search_text"])
        self.assertIn("龜山", job["_location_search_text"])
        # 一般的 _search_text（給品牌/類別等其他比對用）仍然涵蓋自由文字，
        # 這裡確認「八德」確實只在這份欄位裡出現，才會造成誤判的可能性。
        self.assertIn("八德", job["_search_text"])


class BenefitFieldReadThroughTests(unittest.TestCase):
    """回歸測試：「福利」欄位一開始沒有被列在 ALLOWED_PROPERTIES 白名單裡，
    導致即使同仁在 Notion 填了「福利」欄位，fetch_jobs_data() 也會把這個
    欄位整個濾掉，讓 services/matcher_service.py 的福利關鍵字直達攔截
    （find_benefit_matched_jobs）永遠比對不到任何資料——這裡直接驗證
    「福利」欄位確實有被讀進 job_dict，不要重蹈覆轍。"""

    def setUp(self):
        n._cached_jobs, n._last_jobs_fetch = None, 0

    def tearDown(self):
        n._cached_jobs, n._last_jobs_fetch = None, 0

    def test_benefit_field_is_included_in_job_dict(self):
        job_page = {
            "id": "page-1",
            "properties": {
                "職缺名稱": {"type": "title", "title": [{"plain_text": "蝦皮外送三輪雇傭"}]},
                "縣市": {"type": "rich_text", "rich_text": [{"plain_text": "桃園市"}]},
                "行政區": {"type": "rich_text", "rich_text": [{"plain_text": "桃園區"}]},
                "福利": {"type": "rich_text", "rich_text": [{"plain_text": "公司車"}]},
                "狀態": {"type": "select", "select": {"name": "招募中"}},
            },
        }
        with patch("services.notion_service.query_notion_database_direct", return_value=[job_page]):
            jobs = n.fetch_jobs_data()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].get("福利"), "公司車")


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


def _slot_page(page_id="slot-1", label="9/15 早上場", date_iso="2099-01-15T14:00:00+08:00",
               capacity=3, booked=0, status="開放"):
    return {
        "id": page_id,
        "properties": {
            "時段名稱": {"type": "title", "title": [{"plain_text": label}]},
            "面試時間": {"type": "date", "date": {"start": date_iso}},
            "可預約人數": {"type": "number", "number": capacity},
            "已預約人數": {"type": "number", "number": booked},
            "狀態": {"type": "select", "select": {"name": status}},
        },
    }


class InterviewSlotDisplayFormatTests(unittest.TestCase):
    def test_formats_date_with_time(self):
        self.assertEqual(n.format_interview_slot_display("2026-09-15T14:00:00+08:00"), "9/15(二) 14:00")

    def test_formats_date_only_without_time(self):
        self.assertEqual(n.format_interview_slot_display("2026-09-15"), "9/15(二)")

    def test_empty_input_returns_empty(self):
        self.assertEqual(n.format_interview_slot_display(""), "")


class FetchAvailableInterviewSlotsTests(unittest.TestCase):
    """「面試時段」資料庫由同仁自己維護，這裡驗證只會回傳「還沒額滿、狀態
    開放、時間還沒過去」的時段，並依時間排序。"""

    def test_skips_when_db_id_not_configured(self):
        with patch("services.notion_service.NOTION_INTERVIEW_SLOTS_DB_ID", ""), \
             patch("services.notion_service.query_notion_database_direct") as mock_query:
            result = n.fetch_available_interview_slots()

        self.assertEqual(result, [])
        mock_query.assert_not_called()

    def test_excludes_full_closed_and_past_slots_sorted_by_time(self):
        pages = [
            _slot_page(page_id="future-later", date_iso="2099-03-01T10:00:00+08:00"),
            _slot_page(page_id="future-sooner", date_iso="2099-01-01T10:00:00+08:00"),
            _slot_page(page_id="full", date_iso="2099-02-01T10:00:00+08:00", capacity=2, booked=2),
            _slot_page(page_id="closed", date_iso="2099-02-01T10:00:00+08:00", status="關閉"),
            _slot_page(page_id="past", date_iso="2020-01-01T10:00:00+08:00"),
        ]
        with patch("services.notion_service.NOTION_INTERVIEW_SLOTS_DB_ID", "dummy-db-id"), \
             patch("services.notion_service.query_notion_database_direct", return_value=pages):
            result = n.fetch_available_interview_slots()

        self.assertEqual([s["page_id"] for s in result], ["future-sooner", "future-later"])


class BookInterviewSlotTests(unittest.TestCase):
    """求職者選定面試時段後：重新核對還沒額滿 -> 更新已預約人數 -> 在
    「面試預約」資料庫新增一筆紀錄。"""

    def test_successful_booking_updates_slot_and_creates_record(self):
        slot_page = _slot_page(page_id="slot-1", date_iso="2099-01-15T14:00:00+08:00", capacity=3, booked=1)
        patch_response = type("_Resp", (), {"status_code": 200, "text": ""})()
        post_response = type("_Resp", (), {"status_code": 201, "text": ""})()

        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_INTERVIEW_SLOTS_DB_ID", "slots-db"), \
             patch("services.notion_service.NOTION_INTERVIEW_BOOKINGS_DB_ID", "bookings-db"), \
             patch("services.notion_service.get_notion_page", return_value=slot_page), \
             patch("services.notion_service.requests.patch", return_value=patch_response) as mock_patch, \
             patch("services.notion_service.requests.post", return_value=post_response) as mock_post:
            result = n.book_interview_slot("slot-1", "U1234", "小明", job_title="蝦皮門市人員")

        self.assertTrue(result["success"])
        self.assertEqual(result["slot_label"], "1/15(四) 14:00")

        _, patch_kwargs = mock_patch.call_args
        self.assertEqual(patch_kwargs["json"]["properties"]["已預約人數"]["number"], 2)

        _, post_kwargs = mock_post.call_args
        booking_props = post_kwargs["json"]["properties"]
        self.assertEqual(booking_props["求職者暱稱"]["title"][0]["text"]["content"], "小明")
        self.assertEqual(booking_props["LINE User ID"]["rich_text"][0]["text"]["content"], "U1234")
        self.assertEqual(booking_props["應徵職缺"]["rich_text"][0]["text"]["content"], "蝦皮門市人員")
        self.assertEqual(booking_props["面試時間"]["date"]["start"], "2099-01-15T14:00:00+08:00")

    def test_slot_already_full_returns_full_reason_without_writing(self):
        slot_page = _slot_page(page_id="slot-1", capacity=2, booked=2)

        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_INTERVIEW_SLOTS_DB_ID", "slots-db"), \
             patch("services.notion_service.NOTION_INTERVIEW_BOOKINGS_DB_ID", "bookings-db"), \
             patch("services.notion_service.get_notion_page", return_value=slot_page), \
             patch("services.notion_service.requests.patch") as mock_patch, \
             patch("services.notion_service.requests.post") as mock_post:
            result = n.book_interview_slot("slot-1", "U1234", "小明")

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "full")
        mock_patch.assert_not_called()
        mock_post.assert_not_called()

    def test_skips_when_not_configured(self):
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_INTERVIEW_SLOTS_DB_ID", ""), \
             patch("services.notion_service.NOTION_INTERVIEW_BOOKINGS_DB_ID", ""), \
             patch("services.notion_service.get_notion_page") as mock_get:
            result = n.book_interview_slot("slot-1", "U1234", "小明")

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "config")
        mock_get.assert_not_called()


class RecordResumeClickTests(unittest.TestCase):
    """職缺卡片「填寫線上履歷」按鈕點擊記錄：main.py 的 /apply-click 轉址
    端點在求職者點擊當下呼叫這個函式，寫進『履歷點擊紀錄』資料庫，讓招募
    專員知道誰對哪個職缺有興趣。"""

    def test_successful_write_records_display_name_job_and_industry_label(self):
        post_response = type("_Resp", (), {"status_code": 201, "text": ""})()

        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_RESUME_CLICK_LOG_DB_ID", "click-log-db"), \
             patch("services.notion_service.requests.post", return_value=post_response) as mock_post:
            result = n.record_resume_click("U1234", "小明", "蝦皮店到店門市夥伴", "Spx")

        self.assertTrue(result)
        _, post_kwargs = mock_post.call_args
        props = post_kwargs["json"]["properties"]
        self.assertEqual(props["求職者暱稱"]["title"][0]["text"]["content"], "小明")
        self.assertEqual(props["LINE User ID"]["rich_text"][0]["text"]["content"], "U1234")
        self.assertEqual(props["應徵職缺"]["rich_text"][0]["text"]["content"], "蝦皮店到店門市夥伴")
        self.assertEqual(props["產業類別"]["rich_text"][0]["text"]["content"], "蝦皮/外送")
        self.assertIn("點擊時間", props)

    def test_falls_back_to_user_id_when_no_display_name(self):
        post_response = type("_Resp", (), {"status_code": 201, "text": ""})()

        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_RESUME_CLICK_LOG_DB_ID", "click-log-db"), \
             patch("services.notion_service.requests.post", return_value=post_response) as mock_post:
            n.record_resume_click("U1234", "", "美光(桃園)作業員", "Manufacture")

        _, post_kwargs = mock_post.call_args
        self.assertEqual(post_kwargs["json"]["properties"]["求職者暱稱"]["title"][0]["text"]["content"], "U1234")

    def test_skips_when_db_id_not_configured(self):
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_RESUME_CLICK_LOG_DB_ID", ""), \
             patch("services.notion_service.requests.post") as mock_post:
            result = n.record_resume_click("U1234", "小明", "蝦皮門市", "Spx")

        self.assertFalse(result)
        mock_post.assert_not_called()

    def test_skips_when_missing_user_id(self):
        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_RESUME_CLICK_LOG_DB_ID", "click-log-db"), \
             patch("services.notion_service.requests.post") as mock_post:
            result = n.record_resume_click("", "小明", "蝦皮門市", "Spx")

        self.assertFalse(result)
        mock_post.assert_not_called()

    def test_write_failure_returns_false_without_raising(self):
        error_response = type("_Resp", (), {"status_code": 500, "text": "server error"})()

        with patch("services.notion_service.NOTION_API_KEY", "dummy-key"), \
             patch("services.notion_service.NOTION_RESUME_CLICK_LOG_DB_ID", "click-log-db"), \
             patch("services.notion_service.requests.post", return_value=error_response):
            result = n.record_resume_click("U1234", "小明", "蝦皮門市", "Spx")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
