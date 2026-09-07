import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from services import matcher_service as m


def _job(vendor="", search_text="", leave="", shift="", salary="", category="", title="", industry=""):
    return {
        "系統廠商名稱": vendor,
        "_search_text": search_text,
        "休假方式": leave,
        "班別": shift,
        "薪資": salary,
        "職務類別": category,
        "_parsed_title": title,
        "行業別": industry,
        "職缺名稱(對外)": title,
        "職缺名稱": title,
    }


class ExtractLocationTests(unittest.TestCase):
    def test_extracts_plain_location(self):
        self.assertEqual(m.extract_current_target_location("新莊有工作嗎"), "新莊")

    def test_skips_negated_location(self):
        # 「不要新莊」的新莊是被排除的，不該當成正向鎖定的地區
        self.assertEqual(m.extract_current_target_location("不要新莊，想找桃園的"), "桃園")

    def test_no_location_returns_empty(self):
        self.assertEqual(m.extract_current_target_location("有什麼工作"), "")

    def test_detect_negated_location(self):
        self.assertEqual(m.detect_negated_location("不要新莊了"), "新莊")
        self.assertEqual(m.detect_negated_location("新莊工作"), "")


class ShiftAndLeaveTests(unittest.TestCase):
    def test_shift_synonym_single_source(self):
        # SHIFT_SYNONYMS 是 extract_shift_preference 唯一的關鍵字來源
        self.assertEqual(m.extract_shift_preference("想找早上班的工作"), "早班")
        self.assertEqual(m.extract_shift_preference("大夜班可以嗎"), "大夜班")
        self.assertEqual(m.extract_shift_preference("沒有特別偏好"), "")

    def test_leave_preference(self):
        self.assertEqual(m.extract_leave_preference("想要週休二日"), "週休二日")
        self.assertEqual(m.extract_leave_preference("做四休二可以"), "四休二")
        self.assertEqual(m.extract_leave_preference("排休也行"), "排休")


class CategoryAndBrandTests(unittest.TestCase):
    def test_detect_category_label(self):
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("想找理貨的工作")), "理貨/倉儲")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("餐飲內場工作")), "餐飲/服務")

    def test_detect_category_skips_negated(self):
        clean = m.clean_text_for_search("除了外送都可以")
        self.assertEqual(m.detect_category_label(clean), "")
        self.assertEqual(m.detect_negated_category(clean), "外送")

    def test_detect_brand_label_core_name_across_regions(self):
        active_jobs = [
            _job(vendor="美光(桃園)", search_text="美光桃園週休二日"),
            _job(vendor="美光(台南)", search_text="美光台南排休"),
        ]
        # 命中任一分店寫法都要回傳「核心名稱」，讓同品牌跨地區的職缺都能被篩選到
        self.assertEqual(m.detect_brand_label("有美光的工作嗎", active_jobs), "美光")

    def test_has_recognizable_category_or_brand_keyword(self):
        # 統一意圖判斷來源要能涵蓋 CATEGORY_KEYWORDS 裡所有類別（含理貨、餐飲），
        # 不能像 message_handler.py 原本手動維護的清單漏掉這些
        self.assertTrue(m.has_recognizable_category_or_brand_keyword(m.clean_text_for_search("理貨")))
        self.assertTrue(m.has_recognizable_category_or_brand_keyword(m.clean_text_for_search("餐飲內場")))
        self.assertTrue(m.has_recognizable_category_or_brand_keyword(m.clean_text_for_search("momo倉庫")))
        self.assertFalse(m.has_recognizable_category_or_brand_keyword(m.clean_text_for_search("隨便聊聊")))


class BuildAiJobCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            _job(vendor="美光(桃園)", search_text="美光桃園週休二日早班", leave="週休二日",
                 shift="早班", salary="月薪32000", category="作業員", title="美光作業員", industry="製造業"),
            _job(vendor="美光(台南)", search_text="美光台南排休大夜", leave="排休",
                 shift="大夜班", salary="月薪35000", category="作業員", title="美光作業員", industry="製造業"),
            _job(vendor="蝦皮門市", search_text="蝦皮新莊門市週休二日", leave="週休二日",
                 shift="早班", salary="時薪190", category="門市", title="蝦皮門市人員", industry="服務業"),
        ]

    def test_location_with_zero_direct_matches_is_not_empty(self):
        # 舊版會把 target_pool 硬篩成空 list：指定地區在職缺庫裡完全查無資料、
        # 又沒有指定品牌時，AI 完全看不到任何候選職缺。改成加權排序後不該再發生。
        slots = {"location": "五股"}
        candidates = m.build_ai_job_candidates(self.jobs, "五股有工作嗎", "五股", slots, limit=70)
        self.assertEqual(len(candidates), len(self.jobs))

    def test_brand_candidates_span_regions(self):
        slots = {"brand": "美光", "location": "新莊"}
        candidates = m.build_ai_job_candidates(self.jobs, "有美光的工作嗎", "新莊", slots, limit=70)
        vendors = [j["系統廠商名稱"] for j in candidates]
        self.assertIn("美光(桃園)", vendors)
        self.assertIn("美光(台南)", vendors)

    def test_empty_active_jobs_returns_empty(self):
        self.assertEqual(m.build_ai_job_candidates([], "有工作嗎"), [])


class HighConfidenceFaqTests(unittest.TestCase):
    def setUp(self):
        self.faq_list = [
            {"question": "發薪日是什麼時候", "answer": "我司薪資一律每月10號發薪，若遇假日會順延發薪。"},
            {"question": "請假規定", "answer": "請於前一天告知主管請假，特殊狀況可事後補請假單。"},
            {"question": "薪水", "answer": "（問題本文太短，不應該被當成高信心比對來源）"},
        ]

    def test_matches_when_query_contains_full_question(self):
        result = m.find_high_confidence_faq_match(self.faq_list, "請問發薪日是什麼時候呢")
        self.assertIsNotNone(result)
        self.assertEqual(result["question"], "發薪日是什麼時候")

    def test_matches_when_question_contains_full_query(self):
        result = m.find_high_confidence_faq_match(self.faq_list, "請假規定")
        self.assertIsNotNone(result)
        self.assertEqual(result["question"], "請假規定")

    def test_short_question_not_treated_as_high_confidence(self):
        # 「薪水」只有 2 個字，即使被包含在查詢裡也不該被當成高信心命中，
        # 避免短詞子字串比對誤判
        result = m.find_high_confidence_faq_match(self.faq_list, "薪水多少")
        self.assertIsNone(result)

    def test_no_match_returns_none(self):
        self.assertIsNone(m.find_high_confidence_faq_match(self.faq_list, "有momo的工作嗎"))

    def test_empty_inputs(self):
        self.assertIsNone(m.find_high_confidence_faq_match([], "任何問題"))
        self.assertIsNone(m.find_high_confidence_faq_match(self.faq_list, ""))


if __name__ == "__main__":
    unittest.main()
