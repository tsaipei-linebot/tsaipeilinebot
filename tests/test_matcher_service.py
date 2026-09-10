import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from services import matcher_service as m


def _job(vendor="", search_text="", leave="", shift="", salary="", category="", title="", industry="", location_search_text=None, content_text=""):
    return {
        "系統廠商名稱": vendor,
        "_search_text": search_text,
        # 沒有另外指定時，預設沿用 search_text，方便原本就在測「地區能不能命中」
        # 的既有測試不用逐一補這個新欄位；真的要測「自由文字裡的地名不該誤判」
        # 這種情境時，才需要讓兩者不一樣（見 LocationScoringUsesStructuredFieldTests）。
        "_location_search_text": search_text if location_search_text is None else location_search_text,
        "休假方式": leave,
        "班別": shift,
        "薪資": salary,
        "職務類別": category,
        "_parsed_title": title,
        "行業別": industry,
        "職缺名稱(對外)": title,
        "職缺名稱": title,
        "工作內容(對外)": content_text,
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

    def test_detect_category_still_matches_when_only_first_synonym_is_negated(self):
        # 「外送」類別底下同時有「外送」跟「司機」兩個同義關鍵字。原本的寫法只挑
        # 文字裡「第一個出現」的關鍵字判斷有沒有被否定，這句話第一個匹配到的是
        # 被否定的「外送」，整個類別就會誤判成沒命中，白白漏掉後面明確肯定的
        # 「司機」——修正後要能找到任一個沒被否定的同義詞就算命中。
        clean = m.clean_text_for_search("不要外送，我想要司機的工作")
        self.assertEqual(m.detect_category_label(clean), "外送")

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


class ScoreJobForAiBrandBonusTests(unittest.TestCase):
    def test_brand_slot_with_halfwidth_tai_matches_normalized_search_text(self):
        # job 的 _search_text 是 clean_text_for_search() 處理過的結果，「台」一律
        # 轉成「臺」；KNOWN_BRANDS 的 key「台積電」本身是半形台，比對前沒有先
        # 正規化的話會永遠對不到 _search_text 裡的「臺積電」，80 分品牌加分形同
        # 虛設（這是修過的 bug，_brand_matches_text 一直都有做對這件事）。
        job = _job(search_text="臺積電新竹廠作業員")
        score_with_brand = m._score_job_for_ai(job, "有台積電的工作嗎", slots={"brand": "台積電"})
        score_without_brand = m._score_job_for_ai(job, "有台積電的工作嗎", slots={"brand": ""})
        self.assertGreaterEqual(score_with_brand - score_without_brand, 80)

    def test_brand_slot_without_special_characters_still_works(self):
        job = _job(search_text="美光桃園廠作業員")
        score_with_brand = m._score_job_for_ai(job, "有美光的工作嗎", slots={"brand": "美光"})
        score_without_brand = m._score_job_for_ai(job, "有美光的工作嗎", slots={"brand": ""})
        self.assertGreaterEqual(score_with_brand - score_without_brand, 80)


class LocationScoringUsesStructuredFieldTests(unittest.TestCase):
    """上線試營運後實測發現的 bug：蝦皮門市職缺的「行政區」沒有勾選八德，但
    工作內容(對外)的自由文字剛好提到「八德」（例如地址上的路名），求職者問
    「八德有沒有缺額」時被誤判成有。地區加分只能依據 _location_search_text
    （只含縣市/行政區這兩個結構化欄位），不能沿用含自由文字的 _search_text。"""

    def test_free_text_mention_does_not_earn_location_score(self):
        # search_text（自由文字）提到「八德」，但 _location_search_text（實際
        # 勾選的行政區）只有蘆竹、龜山，不該因為文案巧合就加到地區分數。
        job = _job(
            search_text="蝦皮門市地址鄰近八德路口交通便利",
            location_search_text="桃園市蘆竹區龜山區",
        )
        score_with_location = m._score_job_for_ai(job, "八德有工作嗎", current_location="八德")
        score_without_location = m._score_job_for_ai(job, "八德有工作嗎", current_location="")
        self.assertEqual(score_with_location, score_without_location)

    def test_structured_district_field_still_earns_location_score(self):
        # 反過來確認：行政區真的有勾選八德時，地區加分要正常生效，不能因為
        # 這次修正而連真正命中的情況都一起壞掉。
        job = _job(
            search_text="蝦皮門市作業員",
            location_search_text="桃園市八德區",
        )
        score_with_location = m._score_job_for_ai(job, "八德有工作嗎", current_location="八德")
        score_without_location = m._score_job_for_ai(job, "八德有工作嗎", current_location="")
        self.assertEqual(score_with_location - score_without_location, 40)


class CategoryRelaxedMatchingIgnoresFreeTextTests(unittest.TestCase):
    """上線試營運後實測發現的 bug：使用者問「有蝦皮門市嗎」，結果被推薦一筆
    「系統廠商名稱」是「蝦皮內勤」、「職務類別」是「設備人員」的職缺（跟門市
    完全無關），只因為它的「工作內容(對外)」自由文字裡寫到「各區門市據點
    （共60區，門市自選）」——這句話是在講到職地點遍布全台，不是在講職務類別
    是門市。跟先前地區誤判是同一種 bug 類型：寬鬆比對只能信任結構化欄位
    （職缺名稱／職務類別／行業別），不能信任自由文字說明欄位。"""

    def test_equipment_job_not_matched_as_store_category_via_free_text(self):
        # 真實案例重現：系統廠商名稱＝蝦皮內勤（廠商比對會過），職務類別＝設備
        # 人員（跟「門市」完全無關），但工作內容(對外) 自由文字剛好出現「門市」
        # 這個詞，寬鬆比對前會被誤判成門市類別職缺。
        equipment_job = _job(
            vendor="蝦皮內勤",
            category="設備人員",
            title="【雙北基宜】知名企業設備人員",
            content_text="工作內容：負責各區門市據點（共60區，門市自選）設備維護保養",
        )
        self.assertFalse(
            m.job_matches_category_filter(equipment_job, "門市", "蝦皮", allow_relaxed=True)
        )

    def test_genuine_store_job_still_matches_via_relaxed_industry_field(self):
        # 反過來確認：職缺名稱／職務類別都沒有明講「門市」，但「行業別」這個
        # 結構化欄位有門市相關字樣時，寬鬆比對仍要正常放行（不能因為這次修正
        # 而連真正命中的情況都一起壞掉）——注意這裡刻意不讓 title/category
        # 命中，才是真的在測「寬鬆比對」這一層，不是測嚴格比對。
        store_job = _job(
            vendor="蝦皮",
            category="臨時人力",
            industry="零售門市",
            title="蝦皮兼職人員037",
        )
        self.assertTrue(
            m.job_matches_category_filter(store_job, "門市", "蝦皮", allow_relaxed=True)
        )

    def test_filter_jobs_by_category_tiered_excludes_equipment_job(self):
        equipment_job = _job(
            vendor="蝦皮內勤",
            category="設備人員",
            title="【雙北基宜】知名企業設備人員",
            content_text="工作內容：負責各區門市據點（共60區，門市自選）設備維護保養",
        )
        store_job = _job(
            vendor="蝦皮",
            category="臨時人力",
            industry="零售門市",
            title="蝦皮兼職人員037",
        )
        result = m.filter_jobs_by_category_tiered([equipment_job, store_job], "門市", "蝦皮")
        self.assertIn(store_job, result)
        self.assertNotIn(equipment_job, result)


class CategoryMatchingIgnoresInternalNamingConventionTests(unittest.TestCase):
    """實測發現：使用者確認「蝦皮設備人員」「蝦皮客服」「蝦皮後勤專員」這三筆
    支援門市營運的內勤職缺，被誤判成「門市」類別職缺推薦出去。追查後直接
    核對這三筆職缺在 Notion 裡的實際欄位值，確認「門市」「智取店」「店到店」
    這幾個字眼只出現在「職缺名稱」（同仁自己取的內部/行政命名慣例，例如
    「蝦皮內勤(北北基宜)門市裝潢工程外勤專員」），完全沒有出現在求職者
    實際看到的「職缺名稱(對外)」或結構化的「職務類別」欄位——這些職缺的
    職務類別其實是「設備人員」「文字客服」，跟門市完全無關，只是剛好內部
    命名提到這是「支援門市營運的內勤職位」。"""

    def _job_with_internal_naming(self, internal_title, public_title, category, vendor="蝦皮內勤"):
        return {
            "_internal_title": internal_title,
            "職缺名稱": internal_title,
            "職缺名稱(對外)": public_title,
            "職務類別": category,
            "系統廠商名稱": vendor,
            "行業別": "服務業",
        }

    def test_equipment_job_named_with_menshi_in_internal_name_not_matched(self):
        job = self._job_with_internal_naming(
            internal_title="蝦皮內勤(北北基宜)門市裝潢工程外勤專員",
            public_title="【雙北基宜】知名企業設備人員",
            category="設備人員",
        )
        self.assertFalse(m.job_matches_category_filter(job, "門市", "蝦皮", allow_relaxed=True))

    def test_customer_service_job_named_with_zhiqudian_in_internal_name_not_matched(self):
        job = self._job_with_internal_naming(
            internal_title="蝦皮內勤 智取店客服",
            public_title="知名電商客服 早/晚/夜班 月薪42k",
            category="文字客服",
        )
        self.assertFalse(m.job_matches_category_filter(job, "門市", "蝦皮", allow_relaxed=True))

    def test_backoffice_job_named_with_diandaodian_in_internal_name_not_matched(self):
        job = self._job_with_internal_naming(
            internal_title="蝦皮內勤客戶服務專員+蝦皮店到店客服團隊專員",
            public_title="客戶服務暨後勤專員",
            category="文字客服",
        )
        self.assertFalse(m.job_matches_category_filter(job, "門市", "蝦皮", allow_relaxed=True))

    def test_genuine_store_job_still_matches_via_public_title(self):
        # 反過來確認：真的門市類職缺（職缺名稱(對外) 本身就有「門市」）仍然
        # 要能正常命中，不能因為這次修正而連真正命中的情況都一起壞掉。
        job = self._job_with_internal_naming(
            internal_title="蝦皮店到店門市夥伴",
            public_title="🧡蝦皮店到店門市夥伴",
            category="倉儲人員",
        )
        self.assertTrue(m.job_matches_category_filter(job, "門市", "蝦皮", allow_relaxed=True))

    def test_filter_jobs_by_category_tiered_excludes_internal_naming_false_positives(self):
        equipment_job = self._job_with_internal_naming(
            internal_title="蝦皮內勤(北北基宜)門市裝潢工程外勤專員",
            public_title="【雙北基宜】知名企業設備人員",
            category="設備人員",
        )
        customer_service_job = self._job_with_internal_naming(
            internal_title="蝦皮內勤 智取店客服",
            public_title="知名電商客服 早/晚/夜班 月薪42k",
            category="文字客服",
        )
        backoffice_job = self._job_with_internal_naming(
            internal_title="蝦皮內勤客戶服務專員+蝦皮店到店客服團隊專員",
            public_title="客戶服務暨後勤專員",
            category="文字客服",
        )
        store_job = self._job_with_internal_naming(
            internal_title="蝦皮店到店門市夥伴",
            public_title="🧡蝦皮店到店門市夥伴",
            category="倉儲人員",
        )
        result = m.filter_jobs_by_category_tiered(
            [equipment_job, customer_service_job, backoffice_job, store_job], "門市", "蝦皮"
        )
        self.assertEqual(result, [store_job])


class CountyLevelAlternativeJobsTests(unittest.TestCase):
    """使用者提出的新功能：真人派遣專員跟求職者對話時，通常會推薦鄰近或類似
    的工作——例如求職者問「蝦皮門市 八德有缺嗎」，八德沒有缺額時，會順口
    推薦同樣在桃園市的其他門市職缺。這裡測 find_county_level_alternative_jobs
    這個純比對函式本身（訊息處理流程的整合測試見 test_message_handler.py）。"""

    def test_finds_job_in_same_county_different_district(self):
        job_in_taoyuan_but_not_bade = _job(
            search_text="蝦皮桃園區門市", location_search_text="桃園市桃園區",
        )
        result = m.find_county_level_alternative_jobs([job_in_taoyuan_but_not_bade], "八德")
        self.assertEqual(result, [job_in_taoyuan_but_not_bade])

    def test_no_alternative_when_job_is_in_a_different_county(self):
        job_in_new_taipei = _job(
            search_text="門市", location_search_text="新北市板橋區",
        )
        result = m.find_county_level_alternative_jobs([job_in_new_taipei], "八德")
        self.assertEqual(result, [])

    def test_unknown_location_returns_empty(self):
        # LOCATION_TO_COUNTY 沒有收錄的地名（理論上不該發生，因為呼叫端只會
        # 傳進 LOCATION_CANDIDATES 抓到的地名），保守回傳空清單，不要噴例外。
        job = _job(search_text="門市", location_search_text="新北市板橋區")
        result = m.find_county_level_alternative_jobs([job], "不存在的地名")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
