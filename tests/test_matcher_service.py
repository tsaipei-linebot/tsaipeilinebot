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
        self.assertEqual(m.extract_leave_preference("做四休二可以"), "做四休二")
        # 做二休二跟做四休二是不同班表（使用者 2026-09-23 決定分開）
        self.assertEqual(m.extract_leave_preference("做二休二也行"), "做二休二")
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
        # 職務類別照真實資料填「門市人員」：第五輪起職務類別有填時，寬鬆比對
        # 也不看對外職缺名稱（見 _job_extended_category_text）。
        job = self._job_with_internal_naming(
            internal_title="蝦皮店到店門市夥伴",
            public_title="🧡蝦皮店到店門市夥伴",
            category="門市人員",
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
            category="門市人員",
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


class FindSameCountyDistrictLabelsTests(unittest.TestCase):
    """使用者要求：同縣市退讓建議的回覆文字要直接列出具體有哪些行政區可選
    （不是只說「同樣在桃園市還有相關職缺」這種空泛說法），且這個情境刻意
    不設數量上限。這裡測 find_same_county_district_labels() 這個純函式本身。"""

    def test_extracts_same_county_districts_stripping_county_prefix(self):
        job = {"行政區": "桃園市蘆竹區,桃園市龜山區"}
        result = m.find_same_county_district_labels([job], "八德")
        self.assertEqual(result, ["蘆竹區", "龜山區"])

    def test_no_cap_on_number_of_districts_listed(self):
        # 使用者明確表示這個情境不設上限，涵蓋很多行政區時也要全部列出來。
        job = {
            "行政區": (
                "宜蘭市,桃園區,高雄區,基隆區,新北市板橋區,新竹縣區,"
                "嘉義縣區,彰化縣區,台中市區,台北市區,新竹市區,屏東縣區,"
                "台南市區,澎湖縣區,雲林縣區,桃園市蘆竹區,桃園市龜山區,"
                "桃園市中壢區,桃園市平鎮區,桃園市楊梅區,桃園市大園區,桃園市龍潭區"
            )
        }
        result = m.find_same_county_district_labels([job], "八德")
        self.assertEqual(
            result,
            ["桃園區", "蘆竹區", "龜山區", "中壢區", "平鎮區", "楊梅區", "大園區", "龍潭區"],
        )

    def test_deduplicates_same_district_across_multiple_jobs(self):
        job_a = {"行政區": "桃園市蘆竹區"}
        job_b = {"行政區": "蘆竹區,龜山區"}
        result = m.find_same_county_district_labels([job_a, job_b], "八德")
        self.assertEqual(result, ["蘆竹區", "龜山區"])

    def test_excludes_districts_from_a_different_county(self):
        job = {"行政區": "新北市板橋區,桃園市蘆竹區"}
        result = m.find_same_county_district_labels([job], "八德")
        self.assertEqual(result, ["蘆竹區"])

    def test_missing_raw_district_field_returns_empty(self):
        # 職缺沒有結構化的「行政區」欄位時（例如測試資料只給了
        # _location_search_text），安全回傳空清單，呼叫端會退回原本的空泛
        # 說法，不會因為列不出清單就整句話都不回覆。
        job = {"_location_search_text": "桃園市桃園區"}
        result = m.find_same_county_district_labels([job], "八德")
        self.assertEqual(result, [])

    def test_unknown_location_returns_empty(self):
        job = {"行政區": "桃園市蘆竹區"}
        result = m.find_same_county_district_labels([job], "不存在的地名")
        self.assertEqual(result, [])


class StripAdminSuffixTests(unittest.TestCase):
    def test_strips_trailing_district_suffix(self):
        self.assertEqual(m._strip_admin_suffix("大安區"), "大安")
        self.assertEqual(m._strip_admin_suffix("竹北市"), "竹北")

    def test_keeps_short_two_char_district_names_intact(self):
        # 「東區」「西區」去掉字尾會剩下單一個字，太短太容易誤判，刻意不去掉
        self.assertEqual(m._strip_admin_suffix("東區"), "東區")
        self.assertEqual(m._strip_admin_suffix("北區"), "北區")

    def test_no_suffix_present_is_unaffected(self):
        self.assertEqual(m._strip_admin_suffix("佳里"), "佳里")


class SplitDistrictTokenTests(unittest.TestCase):
    def test_splits_county_and_district_prefix(self):
        self.assertEqual(m._split_district_token("台北市大安區"), ("台北", "大安"))

    def test_handles_full_width_tai_variant(self):
        self.assertEqual(m._split_district_token("臺南市佳里區"), ("台南", "佳里"))

    def test_uses_fallback_county_when_no_prefix_present(self):
        self.assertEqual(m._split_district_token("佳里區", fallback_county_core="台南"), ("台南", "佳里"))

    def test_no_prefix_and_no_fallback_returns_empty_county(self):
        self.assertEqual(m._split_district_token("佳里區"), ("", "佳里"))


class BuildDistrictCountyIndexTests(unittest.TestCase):
    """驗證從 active_jobs 動態解析出「行政區 -> 縣市集合」索引：這是修好
    竹北（誤配對到無關縣市）、佳里（完全沒被辨識）這兩個回報案例的核心。"""

    def test_unambiguous_district_maps_to_single_county(self):
        jobs = [{"行政區": "台南市佳里區", "縣市": "台南市"}]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index.get("佳里"), {"台南"})

    def test_district_name_shared_across_counties_is_flagged_ambiguous(self):
        jobs = [
            {"行政區": "台中市東區", "縣市": "台中市"},
            {"行政區": "台南市東區", "縣市": "台南市"},
        ]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index.get("東區"), {"台中", "台南"})

    def test_falls_back_to_county_field_when_district_has_no_prefix(self):
        jobs = [{"行政區": "竹北市", "縣市": "新竹縣"}]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index.get("竹北"), {"新竹"})

    def test_does_not_use_fallback_when_county_field_lists_multiple_counties(self):
        # 縣市欄位列了好幾個縣市時，沒辦法知道哪個行政區 token 對應哪一個，
        # 不猜，跳過這個 token。
        jobs = [{"行政區": "佳里區", "縣市": "台南市,高雄市"}]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index.get("佳里"), None)

    def test_multiple_districts_in_same_field_are_all_indexed(self):
        jobs = [{"行政區": "桃園市蘆竹區,桃園市龜山區", "縣市": "桃園市"}]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index.get("蘆竹"), {"桃園"})
        self.assertEqual(index.get("龜山"), {"桃園"})

    def test_missing_district_field_is_skipped(self):
        jobs = [{"縣市": "台南市"}]
        index = m.build_district_county_index(jobs)
        self.assertEqual(index, {})


class ResolveCountyForLocationTests(unittest.TestCase):
    def test_prefers_static_location_to_county_table(self):
        self.assertEqual(m.resolve_county_for_location("八德"), "桃園市")

    def test_falls_back_to_dynamic_index_when_unambiguous(self):
        jobs = [{"行政區": "台南市佳里區", "縣市": "台南市"}]
        self.assertEqual(m.resolve_county_for_location("佳里", jobs), "台南市")

    def test_ambiguous_dynamic_district_returns_empty(self):
        jobs = [
            {"行政區": "台中市東區", "縣市": "台中市"},
            {"行政區": "台南市東區", "縣市": "台南市"},
        ]
        self.assertEqual(m.resolve_county_for_location("東區", jobs), "")

    def test_unknown_location_without_active_jobs_returns_empty(self):
        self.assertEqual(m.resolve_county_for_location("不存在的地名"), "")


class ExtractLocationDynamicDistrictRegressionTests(unittest.TestCase):
    """回歸測試：使用者實際回報的兩個案例——竹北（LOCATION_CANDIDATES 只收錄
    「新竹」,沒有「竹北」,導致「新竹縣 竹北沒缺嗎」被誤判成只命中「新竹」，
    進而配對到無關的新竹市北區職缺）與佳里（完全沒被任何清單收錄，掉到 AI
    決策、AI 即使看到正確資料仍判斷錯誤）。"""

    def test_recognizes_zhubei_from_active_jobs_instead_of_only_hsinchu(self):
        jobs = [{"行政區": "新竹縣竹北市", "縣市": "新竹縣"}]
        self.assertEqual(m.extract_current_target_location("新竹縣 竹北沒缺嗎", "", jobs), "竹北")

    def test_recognizes_jiali_from_active_jobs(self):
        jobs = [{"行政區": "台南市佳里區", "縣市": "台南市"}]
        self.assertEqual(m.extract_current_target_location("佳里有缺嗎", "", jobs), "佳里")

    def test_bare_county_still_recognized_when_no_district_match(self):
        jobs = [{"行政區": "台南市佳里區", "縣市": "台南市"}]
        self.assertEqual(m.extract_current_target_location("台南有哪些區有缺呢", "", jobs), "台南")

    def test_ambiguous_district_is_not_guessed(self):
        jobs = [
            {"行政區": "台中市東區", "縣市": "台中市"},
            {"行政區": "台南市東區", "縣市": "台南市"},
        ]
        self.assertEqual(m.extract_current_target_location("東區有缺嗎", "", jobs), "")

    def test_without_active_jobs_dynamic_recognition_is_skipped(self):
        # 沒有傳 active_jobs 時（例如舊呼叫端尚未更新），維持原本行為，不會
        # 因為新功能而噴錯。
        self.assertEqual(m.extract_current_target_location("佳里有缺嗎"), "")

    def test_negated_dynamic_district_is_recognized(self):
        jobs = [{"行政區": "台南市佳里區", "縣市": "台南市"}]
        self.assertEqual(m.detect_negated_location("不要佳里了", jobs), "佳里")

    def test_negated_ambiguous_district_is_not_guessed(self):
        jobs = [
            {"行政區": "台中市東區", "縣市": "台中市"},
            {"行政區": "台南市東區", "縣市": "台南市"},
        ]
        self.assertEqual(m.detect_negated_location("不要東區", jobs), "")


class BuildBenefitKeywordIndexTests(unittest.TestCase):
    def test_indexes_jobs_by_benefit_keyword(self):
        job_a = {"職缺名稱": "蝦皮外送三輪雇傭", "福利": "公司車,全勤獎金"}
        job_b = {"職缺名稱": "蝦皮門市人員", "福利": "員購優惠"}
        index = m.build_benefit_keyword_index([job_a, job_b])
        self.assertEqual(index.get("公司車"), [job_a])
        self.assertEqual(index.get("全勤獎金"), [job_a])
        self.assertEqual(index.get("員購優惠"), [job_b])

    def test_multiple_jobs_sharing_same_benefit_are_all_indexed(self):
        job_a = {"職缺名稱": "工作A", "福利": "公司車"}
        job_b = {"職缺名稱": "工作B", "福利": "公司車,員購優惠"}
        index = m.build_benefit_keyword_index([job_a, job_b])
        self.assertEqual(index.get("公司車"), [job_a, job_b])

    def test_missing_benefit_field_is_skipped(self):
        job = {"職缺名稱": "工作A"}
        self.assertEqual(m.build_benefit_keyword_index([job]), {})


class FindBenefitMatchedJobsTests(unittest.TestCase):
    """使用者反映：像「我要公司車的工作」「我選公司車」「有公司車嗎」這種
    問法，很直接就是要推薦有勾選該福利的職缺（例如「蝦皮外送三輪雇傭」），
    改成從 Notion 職缺資料庫的「福利」欄位動態辨識關鍵字，不用寫死在程式
    碼裡。"""

    def test_matches_job_by_benefit_keyword_in_message(self):
        job = {"職缺名稱": "蝦皮外送三輪雇傭", "福利": "公司車"}
        keyword, jobs = m.find_benefit_matched_jobs("有公司車嗎", [job])
        self.assertEqual(keyword, "公司車")
        self.assertEqual(jobs, [job])

    def test_matches_regardless_of_surrounding_phrasing(self):
        job = {"職缺名稱": "蝦皮外送三輪雇傭", "福利": "公司車"}
        for msg in ["我要公司車的工作", "我選公司車", "有公司車嗎"]:
            keyword, jobs = m.find_benefit_matched_jobs(msg, [job])
            self.assertEqual(keyword, "公司車")
            self.assertEqual(jobs, [job])

    def test_no_match_returns_empty(self):
        job = {"職缺名稱": "蝦皮門市人員", "福利": "員購優惠"}
        keyword, jobs = m.find_benefit_matched_jobs("有公司車嗎", [job])
        self.assertEqual(keyword, "")
        self.assertEqual(jobs, [])

    def test_no_active_jobs_returns_empty(self):
        keyword, jobs = m.find_benefit_matched_jobs("有公司車嗎", [])
        self.assertEqual(keyword, "")
        self.assertEqual(jobs, [])

    def test_longer_keyword_preferred_over_shorter_substring(self):
        # 「保障底薪」跟「底薪」都可能同時被登記成福利關鍵字時，訊息裡如果
        # 出現較長、較精確的關鍵字，要優先命中它，不要被短的搶先攔截。
        job_a = {"職缺名稱": "工作A", "福利": "底薪"}
        job_b = {"職缺名稱": "工作B", "福利": "保障底薪"}
        keyword, jobs = m.find_benefit_matched_jobs("有保障底薪嗎", [job_a, job_b])
        self.assertEqual(keyword, "保障底薪")
        self.assertEqual(jobs, [job_b])

    def test_negated_benefit_keyword_is_not_matched(self):
        # 安全性檢查發現：這支函式原本沒有檢查否定語氣，「不要有公司車的
        # 工作」會被誤判成使用者要找公司車職缺，答非所問。
        job = {"職缺名稱": "蝦皮外送三輪雇傭", "福利": "公司車"}
        keyword, jobs = m.find_benefit_matched_jobs("不要有公司車的工作", [job])
        self.assertEqual(keyword, "")
        self.assertEqual(jobs, [])

    def test_single_character_benefit_keyword_is_ignored(self):
        # 安全性檢查發現：這支函式原本沒有排除太短的關鍵字，如果同仁不小心
        # 在「福利」欄位填了單一個字（例如「餐」），會變成極危險的短字串，
        # 任何剛好包含這個字、卻完全無關的句子都會被誤判命中（例如問「想找
        # 餐飲的工作」，這句話是在問職務類別，不是在問福利）。
        job = {"職缺名稱": "工作A", "福利": "餐"}
        keyword, jobs = m.find_benefit_matched_jobs("想找餐飲的工作", [job])
        self.assertEqual(keyword, "")
        self.assertEqual(jobs, [])


class DetectPayMethodLabelTests(unittest.TestCase):
    """使用者實測回報：求職者問「台北日領工作」，卻被推薦了領薪方式其實是
    「週領,匯款,月領,現金」（沒有日領）的職缺，因為 AI 把職缺行銷文案裡的
    「薪資當日結算」誤判成「日領」。改成手動維護一份固定的發薪方式同義詞
    清單，求職者問到任一種發薪方式都要能正確辨識，不能因為系統裡目前剛好
    沒有職缺勾選某個發薪方式，就辨識不出求職者在問什麼。"""

    def test_detects_canonical_label_from_synonyms(self):
        self.assertEqual(m.detect_pay_method_label("台北日領工作"), "日領")
        self.assertEqual(m.detect_pay_method_label("有沒有日結的工作"), "日領")
        self.assertEqual(m.detect_pay_method_label("當天領的工作"), "日領")
        self.assertEqual(m.detect_pay_method_label("我想找週領的工作"), "週領")
        self.assertEqual(m.detect_pay_method_label("月領薪水的工作"), "月領")
        self.assertEqual(m.detect_pay_method_label("現金領薪的工作"), "現金")

    def test_no_pay_method_mentioned_returns_empty(self):
        self.assertEqual(m.detect_pay_method_label("台北的工作"), "")

    def test_negated_pay_method_is_not_matched(self):
        self.assertEqual(m.detect_pay_method_label("不要日領的工作"), "")


class FindPayMethodMatchedJobsTests(unittest.TestCase):
    """比對必須完全依據 Notion 結構化的「領薪方式」欄位，不能受職缺行銷
    文案（「特色」「工作內容」等自由文字）裡出現的相似字眼影響——這正是
    使用者回報的那個實際案例：職缺「精華亮點」寫著「薪資當日結算」，但
    「領薪方式」欄位裡沒有日領，問「日領」時這筆職缺就不該被算進去。"""

    def test_matches_job_by_structured_pay_method_field_only(self):
        shopee_job = {
            "職缺名稱": "蝦皮外送三輪雇傭-大型宅配店",
            "領薪方式": "週領,匯款,月領,現金",
            "精華亮點": "公司提供三輪車，享勞健保與電話費補助，薪資當日結算，多點可選輕鬆賺！",
        }
        daily_pay_job = {
            "職缺名稱": "測試日領外送員",
            "領薪方式": "日領,現金",
        }
        label, jobs = m.find_pay_method_matched_jobs("台北日領工作", [shopee_job, daily_pay_job])
        self.assertEqual(label, "日領")
        self.assertEqual(jobs, [daily_pay_job])

    def test_marketing_text_mentioning_same_day_settlement_does_not_count_as_daily_pay(self):
        # 使用者實測回報的確切案例：領薪方式沒有日領，但精華亮點提到
        # 「當日結算」，這筆職缺絕對不能被算進「日領」的比對結果裡。
        job = {
            "職缺名稱": "蝦皮外送三輪雇傭-大型宅配店",
            "領薪方式": "週領,匯款,月領,現金",
            "精華亮點": "薪資採當日結算，時薪或件酬取最高計算",
        }
        label, jobs = m.find_pay_method_matched_jobs("台北日領工作", [job])
        self.assertEqual(label, "日領")
        self.assertEqual(jobs, [])

    def test_no_pay_method_keyword_returns_empty_label(self):
        job = {"職缺名稱": "工作A", "領薪方式": "月領"}
        label, jobs = m.find_pay_method_matched_jobs("台北的工作", [job])
        self.assertEqual(label, "")
        self.assertEqual(jobs, [])

    def test_no_active_jobs_returns_empty(self):
        label, jobs = m.find_pay_method_matched_jobs("台北日領工作", [])
        self.assertEqual(label, "")
        self.assertEqual(jobs, [])

    def test_negated_pay_method_does_not_match(self):
        job = {"職缺名稱": "工作A", "領薪方式": "日領"}
        label, jobs = m.find_pay_method_matched_jobs("不要日領的工作", [job])
        self.assertEqual(label, "")
        self.assertEqual(jobs, [])


class DistinctRoutableCategoriesForJobsTests(unittest.TestCase):
    """供「蝦皮職缺類型反問」使用：只統計有專屬直達攔截的類別（外送/門市/
    理貨倉儲/製造作業員），依 DIRECT_INTERCEPT_ROUTABLE_CATEGORIES 的順序
    回傳；沒有專屬直達攔截的類別（例如人資專員）不該被算進來，避免反問
    了卻沒有對應按鈕可以精準路由。

    刻意不用「設備人員」當作「沒有專屬直達攔截」的範例——那個字串剛好會
    命中 category_search_keywords()「製造/作業員」清單裡的「設備」關鍵字
    （見 job_matches_category_filter() 的既有行為），改用完全不會跟任何
    已知類別關鍵字重疊的「人資專員」。"""

    def _job(self, category):
        return {"職缺名稱(對外)": f"測試{category}職缺", "職務類別": category}

    def test_single_category_returns_one_label(self):
        jobs = [self._job("外送")]
        self.assertEqual(m.distinct_routable_categories_for_jobs(jobs), ["外送"])

    def test_multiple_categories_returned_in_fixed_order(self):
        jobs = [self._job("製造/作業員"), self._job("外送"), self._job("門市")]
        self.assertEqual(
            m.distinct_routable_categories_for_jobs(jobs),
            ["外送", "門市", "製造/作業員"],
        )

    def test_unroutable_category_is_excluded(self):
        jobs = [self._job("人資專員")]
        self.assertEqual(m.distinct_routable_categories_for_jobs(jobs), [])

    def test_mixed_routable_and_unroutable_only_returns_routable(self):
        jobs = [self._job("外送"), self._job("人資專員")]
        self.assertEqual(m.distinct_routable_categories_for_jobs(jobs), ["外送"])

    def test_empty_jobs_returns_empty_list(self):
        self.assertEqual(m.distinct_routable_categories_for_jobs([]), [])


class FindLeaveMatchedJobsTests(unittest.TestCase):
    """跟 find_pay_method_matched_jobs() 同一種寫法：刻意重複使用
    extract_leave_preference() 這同一套分類邏輯，同時套用在求職者的話跟
    職缺自己的「休假方式」欄位值上，確保兩邊都歸類到同一個標準用語才算
    符合。"""

    def test_matches_job_by_structured_leave_field(self):
        job_a = {"職缺名稱": "工作A", "休假方式": "週休"}
        job_b = {"職缺名稱": "工作B", "休假方式": "排休"}
        label, jobs = m.find_leave_matched_jobs("有週休二日的工作嗎", [job_a, job_b])
        self.assertEqual(label, "週休二日")
        self.assertEqual(jobs, [job_a])

    def test_synonym_in_job_field_is_recognized(self):
        # 職缺欄位寫「做二休二」，求職者問「2休2」，兩者都屬於同一個標準
        # 分類（做二休二），應該要能對得上。
        job = {"職缺名稱": "工作A", "休假方式": "做二休二"}
        label, jobs = m.find_leave_matched_jobs("想找2休2的工作", [job])
        self.assertEqual(label, "做二休二")
        self.assertEqual(jobs, [job])

    def test_work_four_rest_two_does_not_match_work_two_rest_two(self):
        # 使用者 2026-09-23 決定分開：問做四休二不能推薦做二休二的職缺。
        job = {"職缺名稱": "美光(桃園)_Porter", "休假方式": "做二休二"}
        label, jobs = m.find_leave_matched_jobs("想找做四休二的工作", [job])
        self.assertEqual(label, "做四休二")
        self.assertEqual(jobs, [])

    def test_no_leave_keyword_returns_empty(self):
        job = {"職缺名稱": "工作A", "休假方式": "排休"}
        label, jobs = m.find_leave_matched_jobs("台北的工作", [job])
        self.assertEqual(label, "")
        self.assertEqual(jobs, [])

    def test_no_active_jobs_returns_empty(self):
        label, jobs = m.find_leave_matched_jobs("週休二日的工作", [])
        self.assertEqual(label, "")
        self.assertEqual(jobs, [])

    def test_job_with_multiple_leave_values_matches_the_one_not_checked_first(self):
        # 多輪對話背景測試找到的真實案例：康寧的職缺「休假方式」欄位同時
        # 填了「做二休二,排休」，代表這筆職缺依班別不同分別適用兩種制度。
        # extract_leave_preference() 一次只判斷「第一個命中」的分類（週休
        # 二日→四休二→排休 依序檢查），對整串欄位值只呼叫一次的話，「做二
        # 休二」先命中、"排休" 就完全比對不到，即使欄位裡明明也寫了排休。
        job = {"職缺名稱": "康寧(世捷)_倉儲", "休假方式": "做二休二,排休"}
        label, jobs = m.find_leave_matched_jobs("有排休的工作嗎", [job])
        self.assertEqual(label, "排休")
        self.assertEqual(jobs, [job])

    def test_job_with_multiple_leave_values_still_matches_the_first_checked_one(self):
        job = {"職缺名稱": "康寧(世捷)_倉儲", "休假方式": "做二休二,排休"}
        label, jobs = m.find_leave_matched_jobs("有做二休二的工作嗎", [job])
        self.assertEqual(label, "做二休二")
        self.assertEqual(jobs, [job])


class LocationGranularityTests(unittest.TestCase):
    """全資料庫自動比對測到地區比對太粗：「台北市中山區」被當成整個台北、
    「桃園區」被當成整個桃園市、「嘉義縣」混到嘉義市的職缺。"""

    def setUp(self):
        self.jobs = [
            {"縣市": "台北市", "行政區": "台北市中山區"},
            {"縣市": "基隆市", "行政區": "基隆市中山區"},
            {"縣市": "台中市", "行政區": "台中市東區"},
            {"縣市": "台南市", "行政區": "台南市東區"},
            {"縣市": "桃園市", "行政區": "桃園市桃園區,桃園市八德區"},
        ]

    def test_ambiguous_district_is_qualified_by_mentioned_county(self):
        self.assertEqual(m.extract_current_target_location("台北市中山區有內場的工作嗎", "", self.jobs), "台北市中山區")
        self.assertEqual(m.extract_current_target_location("台中東區有工作嗎", "", self.jobs), "台中市東區")

    def test_ambiguous_district_without_county_is_still_not_guessed(self):
        self.assertEqual(m.extract_current_target_location("中山區有工作嗎", "", self.jobs), "")

    def test_taoyuan_district_is_not_whole_taoyuan_city(self):
        self.assertEqual(m.extract_current_target_location("桃園區有工作嗎", "", self.jobs), "桃園區")
        self.assertEqual(m.extract_current_target_location("桃園有工作嗎", "", self.jobs), "桃園")
        self.assertEqual(m.resolve_county_for_location("桃園區", self.jobs), "桃園市")

    def test_county_and_city_with_same_core_name_are_separated(self):
        self.assertEqual(m.extract_current_target_location("嘉義縣的工作"), "嘉義縣")
        self.assertEqual(m.extract_current_target_location("新竹市的工作"), "新竹市")
        self.assertEqual(m.extract_current_target_location("嘉義有工作嗎"), "嘉義")

    def test_qualified_location_resolves_to_its_county(self):
        self.assertEqual(m.resolve_county_for_location("台北市中山區", self.jobs), "台北市")
        self.assertEqual(m.resolve_county_for_location("嘉義縣"), "嘉義縣")


class MultiTurnRoundThreeMatcherFixTests(unittest.TestCase):
    """第三輪多輪對話背景測試（4 個 agent、145 筆真實職缺）找到的比對層問題。"""

    def test_double_weekly_pay_is_not_read_as_weekly(self):
        self.assertEqual(m.detect_pay_method_label("有雙週領的嗎"), "雙週領")
        job = {"職缺名稱": "A", "領薪方式": "週領,匯款"}
        _, jobs = m.find_pay_method_matched_jobs("有雙週領的嗎", [job])
        self.assertEqual(jobs, [])

    def test_weekly_pay_query_still_matches_weekly_job(self):
        job = {"職缺名稱": "A", "領薪方式": "月領,週領,匯款"}
        label, jobs = m.find_pay_method_matched_jobs("可以週領嗎", [job])
        self.assertEqual(label, "週領")
        self.assertEqual(jobs, [job])

    def test_jiekou_and_advance_pay_are_recognized(self):
        self.assertEqual(m.detect_pay_method_label("可以用街口領嗎"), "街口")
        self.assertEqual(m.detect_pay_method_label("可以預支薪水嗎"), "預支")

    def test_negation_does_not_spill_into_next_clause(self):
        # 「不要蝦皮了」的否定詞不該波及後面的「高雄」。
        self.assertEqual(m.extract_current_target_location("那不要蝦皮了 高雄有什麼餐廳的兼職"), "高雄")

    def test_chu_le_is_still_a_negation(self):
        self.assertTrue(m._keyword_is_negated("除了外送都可以", "外送"))

    def test_previously_missing_counties_are_recognized(self):
        for county in ["花蓮", "台東", "南投", "雲林", "澎湖"]:
            self.assertEqual(m.extract_current_target_location(f"{county}有工作嗎"), county)

    def test_service_word_alone_does_not_trigger_food_service_category(self):
        clean = m.clean_text_for_search("有交通車接送服務嗎")
        self.assertEqual(m.detect_category_label(clean), "")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("想找餐廳服務員")), "餐飲/服務")

    def test_equipment_staff_is_not_counted_as_manufacturing(self):
        job = {"職缺名稱(對外)": "【雙北基宜】知名企業設備人員", "職務類別": "設備人員"}
        # 第五輪起設備人員有自己的「設備/技術」類型（使用者決定），但仍然不算製造/作業員
        self.assertEqual(m.distinct_routable_categories_for_jobs([job]), ["設備/技術"])

    def test_packing_job_is_warehouse_not_manufacturing(self):
        job = {"職缺名稱(對外)": "電商物流理貨包裝員", "職務類別": "理貨人員"}
        self.assertEqual(m.distinct_routable_categories_for_jobs([job]), ["理貨/倉儲"])

    def test_known_brand_family_wins_over_full_vendor_name(self):
        # 點「蝦皮外送」按鈕時，廠商要記「蝦皮」，不是「蝦皮外送」。
        jobs = [{"系統廠商名稱": "蝦皮外送(支援)"}, {"系統廠商名稱": "蝦皮門市"}]
        self.assertEqual(m.detect_brand_label("蝦皮外送", jobs), "蝦皮")
        self.assertEqual(m.detect_brand_label("蝦皮門市", jobs), "蝦皮")

    def test_pchome_is_recognized(self):
        jobs = [{"系統廠商名稱": "PChome理貨"}]
        self.assertEqual(m.detect_brand_label("PChome林口倉還有在徵人嗎", jobs), "PChome")

    def test_longest_vendor_match_wins_regardless_of_order(self):
        jobs = [{"系統廠商名稱": "大立"}, {"系統廠商名稱": "大立光"}]
        self.assertEqual(m.detect_brand_label("大立光有缺嗎", jobs), "大立光")

    def test_job_matches_brand_ignores_location_text(self):
        # 廠商「新興(代招)」不能比對到地址在高雄市新興區的其他廠商職缺。
        other = {"系統廠商名稱": "薪航宅配", "職缺名稱": "薪航宅配", "_search_text": "薪航宅配高雄市新興區"}
        target = {"系統廠商名稱": "新興(代招)", "職缺名稱": "新興(代招)", "_search_text": "新興代招新北市五股區"}
        self.assertFalse(m.job_matches_brand(other, "新興"))
        self.assertTrue(m.job_matches_brand(target, "新興"))


class RoundFourUnderstandingMatcherTests(unittest.TestCase):
    """第四輪多輪對話測試（使用者 2026-09-23 定的「不確定就讓求職者選」原則）
    matcher 端的修正：否定詞只看自己的子句、一句話講多個值、放寬說法、
    問問題還是提需求、班別篩選、類型關鍵字。"""

    def test_negation_only_applies_to_its_own_clause(self):
        self.assertEqual(m.detect_pay_method_labels("不要夜班，日領的就好"), ["日領"])
        self.assertEqual(m.extract_shift_labels("不要夜班，日領的就好", negated=True), ["大夜班"])
        self.assertEqual(m.extract_shift_labels("不要夜班，日領的就好"), [])
        self.assertEqual(m.detect_pay_method_labels("不要日領"), [])
        self.assertEqual(m.detect_pay_method_labels("不要日領", negated=True), ["日領"])

    def test_multiple_values_in_one_sentence(self):
        self.assertEqual(m.detect_pay_method_labels("日領或週領都可以"), ["日領", "週領"])
        self.assertEqual(m.detect_pay_method_labels("雙週領"), ["雙週領"])

    def test_holiday_shift_is_not_day_shift(self):
        self.assertEqual(m.extract_shift_labels("假日班"), ["假日班"])
        self.assertEqual(m.extract_leave_labels("四三輪休"), ["四三輪休"])

    def test_new_leave_wordings(self):
        self.assertEqual(m.extract_leave_preference("做兩休兩"), "做二休二")
        self.assertEqual(m.extract_leave_preference("做三休三的工作"), "做三休三")
        self.assertEqual(m.extract_leave_preference("你們休假日也要上班嗎"), "")

    def test_multi_value_label_filters(self):
        jobs = [
            {"領薪方式": "日領", "班別": "早班", "休假方式": "排休"},
            {"領薪方式": "週領", "班別": "假日班", "休假方式": "週休二日"},
            {"領薪方式": "月領", "班別": "夜班(打烊班)", "休假方式": "做四休二"},
        ]
        self.assertEqual(len(m.filter_jobs_by_pay_label(jobs, "日領|週領")), 2)
        self.assertEqual(m.filter_jobs_by_shift_label(jobs, "假日班"), [jobs[1]])
        self.assertEqual(m.filter_jobs_by_shift_label(jobs, "晚班"), [jobs[2]])
        self.assertEqual(m.filter_jobs_by_leave_label(jobs, "週休二日|做四休二"), jobs[1:])

    def test_relax_wordings(self):
        c = m.clean_text_for_search
        self.assertEqual(m.detect_relax_dimensions(c("不一定要週休")), {"leave"})
        self.assertEqual(m.detect_relax_dimensions(c("日領沒有就算了")), {"pay"})
        self.assertEqual(m.detect_relax_dimensions(c("不需要交通車"), ["交通車"]), {"benefit"})
        self.assertEqual(m.detect_relax_dimensions(c("日領的就好")), set())

    def test_condition_words_broaden_all_secondary(self):
        # 第六輪起多了全職/兼職跟薪資兩項
        self.assertEqual(m.detect_scoped_broaden_dimensions(m.clean_text_for_search("條件都不限")), {"leave", "pay", "benefit", "shift", "worktype", "salary", "exclude"})

    def test_question_or_demand(self):
        self.assertEqual(m.classify_condition_utterance("可以預支薪水嗎"), "question")
        self.assertEqual(m.classify_condition_utterance("週領是禮拜幾發"), "question")
        self.assertEqual(m.classify_condition_utterance("交通車有哪些站點"), "question")
        self.assertEqual(m.classify_condition_utterance("有交通車的嗎"), "demand")
        self.assertEqual(m.classify_condition_utterance("有日領的工作嗎"), "demand")
        self.assertEqual(m.classify_condition_utterance("日領的就好"), "demand")
        self.assertEqual(m.classify_condition_utterance("想了解日領的規定"), "info")

    def test_category_keywords(self):
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("倉庫的工作")), "理貨/倉儲")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("服飾店有缺嗎")), "門市")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("百貨專櫃")), "門市")

    def test_category_field_wins_over_public_title(self):
        job = {"職缺名稱(對外)": "電商物流理貨包裝", "職務類別": "作業員", "_job_category": "作業員"}
        self.assertFalse(m.job_matches_category_filter(job, "理貨/倉儲", allow_relaxed=False))
        self.assertTrue(m.job_matches_category_filter(job, "製造/作業員", allow_relaxed=False))
        untagged = {"職缺名稱(對外)": "電商物流理貨包裝", "職務類別": ""}
        self.assertTrue(m.job_matches_category_filter(untagged, "理貨/倉儲", allow_relaxed=False))


class RoundFourLocationPrecisionTests(unittest.TestCase):
    """第四輪多輪對話測試第三批：地區比對更精準。"""

    def _jobs(self):
        return [
            {"縣市": "桃園市", "行政區": "桃園市八德區,桃園市中壢區", "_location_search_text": "桃園市桃園市八德區桃園市中壢區"},
            {"縣市": "宜蘭縣", "行政區": "宜蘭縣宜蘭市,宜蘭縣礁溪鄉", "_location_search_text": "宜蘭縣宜蘭縣宜蘭市宜蘭縣礁溪鄉"},
            {"縣市": "台南市", "行政區": "台南市安南區,台南市南區", "_location_search_text": "臺南市臺南市安南區臺南市南區"},
            {"縣市": "台中市", "行政區": "台中市南區", "_location_search_text": "臺中市臺中市南區"},
            {"縣市": "台北市,基隆市", "行政區": "中山區,仁愛區", "_location_search_text": "臺北市基隆市中山區仁愛區"},
            {"縣市": "新竹縣", "行政區": "竹北市", "_location_search_text": "新竹縣竹北市"},
            {"縣市": "新竹市", "行政區": "新竹市東區", "_location_search_text": "新竹市新竹市東區"},
        ]

    def test_district_wins_over_county_core_in_full_address(self):
        jobs = self._jobs()
        self.assertEqual(m.extract_current_target_location("桃園市八德區有工作嗎", "", jobs), "八德")
        self.assertEqual(m.extract_current_target_location("桃園市中壢區", "", jobs), "中壢")
        self.assertEqual(m.extract_current_target_location("宜蘭縣礁溪鄉有工作嗎", "", jobs), "礁溪")

    def test_earliest_district_wins(self):
        jobs = self._jobs()
        self.assertEqual(m.extract_current_target_location("台南市安南區的工作", "", jobs), "安南")
        # 使用者 2026-09-23（第五輪）決定：用「或」連起來的兩個地區都算
        self.assertEqual(m.extract_current_target_location("中壢或八德", "", jobs), "中壢|八德")

    def test_ambiguous_district_uses_context_county(self):
        jobs = self._jobs() + [{"縣市": "台北市", "行政區": "台北市中山區", "_location_search_text": "臺北市臺北市中山區"},
                               {"縣市": "基隆市", "行政區": "基隆市中山區", "_location_search_text": "基隆市基隆市中山區"}]
        self.assertEqual(m.extract_current_target_location("中山區呢", "", jobs, context_location="台北"), "台北市中山區")
        self.assertEqual(m.extract_current_target_location("中山區呢", "", jobs, context_location="基隆"), "基隆市中山區")
        self.assertEqual(m.extract_current_target_location("中山區呢", "", jobs), "")
        self.assertEqual(m.ambiguous_district_choices("中山區有工作嗎", jobs), ["台北市中山區", "基隆市中山區"])
        self.assertEqual(m.ambiguous_district_choices("中山路附近", jobs), [])

    def test_full_index_keeps_county_or_city(self):
        index = m.build_district_county_full_index(self._jobs())
        self.assertEqual(index["竹北"], {"新竹縣"})
        self.assertEqual(m.resolve_county_for_location("竹北", self._jobs()), "新竹縣")

    def test_structured_match_for_unprefixed_district_with_many_counties(self):
        job = self._jobs()[4]
        self.assertTrue(m.job_matches_location(job, "台北市中山區"))
        self.assertTrue(m.job_matches_location(job, "基隆市中山區"))
        self.assertFalse(m.job_matches_location(job, "台北市大安區"))
        self.assertFalse(m.job_matches_location(self._jobs()[3], "台南市南區"))
        self.assertTrue(m.job_matches_location(self._jobs()[2], "台南市南區"))


    def test_generic_service_staff_is_food_service_only_in_food_industry(self):
        clothing = {"職缺名稱(對外)": "佐丹奴兼職", "職務類別": "服務人員,門市人員", "_job_category": "服務人員,門市人員", "行業別": "服飾業"}
        restaurant = {"職缺名稱(對外)": "上海鄉村-發傳單", "職務類別": "服務人員", "_job_category": "服務人員", "行業別": "餐飲業"}
        self.assertFalse(m.job_matches_category_filter(clothing, "餐飲/服務"))
        self.assertTrue(m.job_matches_category_filter(clothing, "門市"))
        self.assertTrue(m.job_matches_category_filter(restaurant, "餐飲/服務"))


class RoundFiveUnderstandingMatcherTests(unittest.TestCase):
    """第五輪多輪對話測試第一批（matcher 端）：更多否定說法、非常/非…不可、
    沒有標點時否定詞不越界、廠商誤判、別名、放寬只放寬講到的值。"""

    def _jobs(self):
        return [
            {"系統廠商名稱": "M打烊班", "職缺名稱": "M打烊班"},
            {"系統廠商名稱": "文華", "職缺名稱": "文華"},
            {"系統廠商名稱": "美光(桃園)", "職缺名稱": "美光(桃園)_堆高機"},
            {"系統廠商名稱": "蝦皮(威獅)(時薪)", "職缺名稱": "蝦皮(威獅)(時薪)"},
            {"系統廠商名稱": "台灣大哥大客服", "職缺名稱": "台灣大哥大客服"},
        ]

    def test_more_ways_to_say_no(self):
        for text in ["沒有夜班的工作", "我不能上夜班", "夜班不行", "夜班就不要了", "不用上夜班", "拒絕夜班", "不接受夜班"]:
            self.assertEqual(m.extract_shift_labels(text), [], text)
            self.assertEqual(m.extract_shift_labels(text, negated=True), ["大夜班"], text)
        self.assertEqual(m.extract_shift_labels("有沒有夜班"), ["大夜班"])
        self.assertEqual(m.detect_pay_method_labels("能不能日領"), ["日領"])
        self.assertEqual(m.extract_shift_labels("沒有經驗夜班可以嗎"), ["大夜班"])

    def test_fei_is_not_always_negation(self):
        self.assertEqual(m.detect_pay_method_labels("我非常需要日領"), ["日領"])
        self.assertEqual(m.detect_pay_method_labels("非日領不可"), ["日領"])
        self.assertFalse(m.has_negative_intent("我非常想找桃園的理貨工作"))
        self.assertTrue(m.has_negative_intent("非夜班"))

    def test_negation_does_not_spill_without_punctuation(self):
        self.assertEqual(m.detect_pay_method_labels("不要夜班日領就好"), ["日領"])
        self.assertEqual(m.extract_shift_labels("不要夜班跟大夜", negated=True), ["大夜班"])
        cc = m.clause_clean_text("不要外送，理貨呢")
        self.assertEqual(m.detect_category_label(cc), "理貨/倉儲")
        self.assertEqual(m.detect_negated_category(cc), "外送")

    def test_negated_vendor_is_not_the_brand(self):
        jobs = self._jobs()
        for text in ["不要蝦皮", "除了蝦皮以外的", "蝦皮以外的"]:
            self.assertEqual(m.detect_brand_label(text, jobs), "", text)
            self.assertEqual(m.detect_negated_brand(text, jobs), "蝦皮", text)
        self.assertEqual(m.detect_brand_label("不要美光的", jobs), "")
        self.assertEqual(m.detect_negated_brand("不要美光的", jobs), "美光")

    def test_shift_words_and_streets_are_not_vendors(self):
        jobs = self._jobs()
        self.assertEqual(m.detect_brand_label("我想找打烊班", jobs), "")
        self.assertEqual(m.detect_brand_label("有打烊班嗎", jobs), "")
        self.assertEqual(m.detect_brand_label("我住文華路附近", jobs), "")
        self.assertEqual(m.extract_shift_labels("我想找打烊班"), ["晚班"])

    def test_brand_aliases(self):
        jobs = self._jobs()
        self.assertEqual(m.detect_brand_label("shopee有缺嗎", jobs), "蝦皮")
        self.assertEqual(m.detect_brand_label("優步", jobs), "Uber")
        self.assertEqual(m.detect_brand_label("台哥大有缺嗎", jobs), "台灣大哥大")
        self.assertTrue(m.job_matches_brand(jobs[4], "台灣大哥大"))
        self.assertTrue(m._brand_matches_text("台積電", "台積電"))

    def test_everyday_shift_words(self):
        self.assertEqual(m.extract_shift_labels("桃園有白天班的嗎"), ["早班"])
        self.assertEqual(m.extract_shift_labels("晚上的"), ["晚班"])
        self.assertEqual(m.extract_shift_labels("週末可以上的"), ["假日班"])
        self.assertEqual(m.extract_shift_labels("休六日"), [])

    def test_seeker_side_category_words(self):
        for text, label in [("檢驗人員的工作", "製造/作業員"), ("品保", "製造/作業員"), ("搬運的工作", "理貨/倉儲")]:
            self.assertEqual(m.detect_category_label(m.clean_text_for_search(text)), label, text)

    def test_relax_only_the_named_value(self):
        c = m.clean_text_for_search
        self.assertEqual(m.detect_relax_labels(c("我不需要日領，月領就好")), {"pay": {"日領"}})
        self.assertEqual(m.detect_relax_labels(c("不一定要雙週領")), {"pay": {"雙週領"}})
        self.assertEqual(m.detect_relax_labels(c("休假方式不一定")), {"leave": {"*"}})

    def test_more_scoped_broaden_words(self):
        c = m.clean_text_for_search
        self.assertEqual(m.detect_scoped_broaden_dimensions(c("什麼班都可以")), {"shift"})
        self.assertEqual(m.detect_scoped_broaden_dimensions(c("地方都可以")), {"location"})
        self.assertEqual(m.detect_scoped_broaden_dimensions(c("哪家都可以")), {"brand"})
        self.assertEqual(m.detect_scoped_broaden_dimensions(c("縣市不限")), {"location"})

    def test_question_markers(self):
        self.assertEqual(m.classify_condition_utterance("週領是每週幾"), "question")
        self.assertEqual(m.classify_condition_utterance("倉儲會很累嗎"), "question")
        self.assertEqual(m.classify_condition_utterance("作業員是做什麼的"), "question")
        self.assertEqual(m.classify_condition_utterance("早班還是晚班都可以"), "demand")

    def test_short_message_does_not_match_faq(self):
        faqs = [{"question": "面試要帶什麼", "answer": "x"}]
        self.assertIsNone(m.find_high_confidence_faq_match(faqs, "要"))
        self.assertIsNone(m.find_high_confidence_faq_match(faqs, "什麼"))
        self.assertIsNotNone(m.find_high_confidence_faq_match(faqs, "請問面試要帶什麼"))


class RoundFiveLocationMatcherTests(unittest.TestCase):
    """第五輪多輪對話測試第二批：地名。"""

    def _jobs(self):
        return [
            {"縣市": "台北市", "行政區": "台北市大安區,台北市大同區,台北市中山區"},
            {"縣市": "台中市", "行政區": "台中市西屯區,台中市西區,台中市北區"},
            {"縣市": "台南市", "行政區": "台南市中西區,台南市北區"},
            {"縣市": "新竹縣", "行政區": "新竹縣竹北市,新竹縣竹東鎮"},
            {"縣市": "新竹市", "行政區": "新竹市北區,新竹市東區"},
            {"縣市": "苗栗縣", "行政區": "苗栗縣苗栗市,苗栗縣頭份市"},
            {"縣市": "宜蘭縣", "行政區": "宜蘭縣大同鄉,宜蘭縣礁溪鄉"},
            {"縣市": "基隆市", "行政區": "基隆市中山區"},
            {"縣市": "桃園市", "行政區": "桃園市中壢區,桃園市八德區,桃園市平鎮區"},
        ]

    def _loc(self, text):
        return m.extract_current_target_location(text, "", self._jobs())

    def test_named_county_is_not_ignored(self):
        self.assertEqual(self._loc("台中市大安區有工作嗎"), "台中市大安區")
        self.assertEqual(self._loc("台北市大安區"), "大安")

    def test_district_does_not_straddle_county_name(self):
        self.assertEqual(self._loc("台中西屯"), "西屯")
        self.assertEqual(self._loc("台中西區"), "西區")  # 這組資料只有台中有西區
        self.assertEqual(self._loc("新竹北區"), "新竹市北區")
        self.assertEqual(self._loc("新竹東區"), "東區")

    def test_county_seat_city_is_not_the_whole_county(self):
        self.assertEqual(self._loc("苗栗市的工作"), "苗栗縣苗栗市")
        self.assertEqual(self._loc("苗栗縣的工作"), "苗栗")
        job = {"縣市": "苗栗縣", "行政區": "苗栗縣頭份市", "_location_search_text": "苗栗縣苗栗縣頭份市"}
        self.assertFalse(m.job_matches_location(job, "苗栗縣苗栗市"))

    def test_real_district_suffix_is_used(self):
        self.assertEqual(self._loc("大同鄉有工作嗎"), "宜蘭縣大同鄉")
        self.assertEqual(self._loc("大同區"), "台北市大同區")
        self.assertEqual(m.ambiguous_district_choices("中山區有工作嗎", self._jobs()), ["台北市中山區", "基隆市中山區"])

    def test_home_versus_work_place(self):
        self.assertEqual(self._loc("我住在桃園想去新竹上班"), "新竹")
        self.assertEqual(self._loc("我住中壢想去台北上班"), "台北")
        self.assertEqual(self._loc("我住台北"), "台北")

    def test_two_places_both_count(self):
        self.assertEqual(self._loc("桃園或新竹都可以"), "桃園|新竹")
        self.assertEqual(self._loc("中壢跟八德"), "中壢|八德")
        job = {"_location_search_text": "新竹市東區"}
        self.assertTrue(m.job_matches_location(job, "桃園|新竹"))

    def test_negating_a_qualified_location(self):
        self.assertTrue(m.location_is_negated("不要中山區", "台北市中山區", self._jobs()))
        self.assertTrue(m.location_is_negated("不要台北市中山區", "台北市中山區", self._jobs()))
        self.assertFalse(m.location_is_negated("中山區呢", "台北市中山區", self._jobs()))


class RoundFiveFlowMatcherTests(unittest.TestCase):
    """第五輪多輪對話測試第三批（matcher 端）：新類型、兩個類型都算、排除條件。"""

    def test_new_categories(self):
        c = m.clean_text_for_search
        self.assertEqual(m.detect_category_label(c("台北客服的工作")), "客服/行政")
        self.assertEqual(m.detect_category_label(c("行政人員")), "客服/行政")
        self.assertEqual(m.detect_category_label(c("設備人員的工作")), "設備/技術")
        self.assertTrue(m.job_matches_category_filter({"職務類別": "文字客服"}, "客服/行政", allow_relaxed=False))
        self.assertTrue(m.job_matches_category_filter({"職務類別": "設備人員"}, "設備/技術", allow_relaxed=False))

    def test_categories_in_the_order_they_are_said(self):
        c = m.clean_text_for_search
        self.assertEqual(m.detect_category_labels(c("理貨或門市都可以")), ["理貨/倉儲", "門市"])
        self.assertEqual(m.detect_category_labels(c("蝦皮外送理貨")), ["外送", "理貨/倉儲"])

    def test_two_categories_both_count(self):
        jobs = [{"職務類別": "理貨人員"}, {"職務類別": "門市人員"}, {"職務類別": "作業員"}]
        self.assertEqual(len(m.filter_jobs_by_category_tiered(jobs, "理貨/倉儲|門市")), 2)

    def test_public_title_ignored_when_category_is_filled(self):
        ramen = {"職缺名稱(對外)": "日式連鎖拉麵門市人員", "職務類別": "內場人員,外場人員"}
        self.assertFalse(m.job_matches_category_filter(ramen, "門市", allow_relaxed=True))
        self.assertTrue(m.job_matches_category_filter(ramen, "餐飲/服務", allow_relaxed=True))

    def test_exclusions_keep_jobs_that_have_other_options(self):
        night_only = {"班別": "夜班"}
        both = {"班別": "早班,夜班"}
        self.assertTrue(m.job_is_excluded(night_only, {"shift": {"大夜班"}}))
        self.assertFalse(m.job_is_excluded(both, {"shift": {"大夜班"}}))
        self.assertTrue(m.job_is_excluded({"領薪方式": "日領,匯款"}, {"pay": {"日領"}}))
        self.assertFalse(m.job_is_excluded({"領薪方式": "日領,月領,匯款"}, {"pay": {"日領"}}))
        self.assertTrue(m.job_is_excluded({"職務類別": "外送員"}, {"category": {"外送"}}))
        self.assertTrue(m.job_is_excluded({"系統廠商名稱": "蝦皮門市"}, {"brand": {"蝦皮"}}))
        only_zhongli = {"縣市": "桃園市", "行政區": "桃園市中壢區", "_location_search_text": "桃園市桃園市中壢區"}
        two = {"縣市": "桃園市", "行政區": "桃園市中壢區,桃園市八德區", "_location_search_text": "桃園市桃園市中壢區桃園市八德區"}
        self.assertTrue(m.job_is_excluded(only_zhongli, {"location": {"中壢"}}))
        self.assertFalse(m.job_is_excluded(two, {"location": {"中壢"}}))


class RoundSixUnderstandingMatcherTests(unittest.TestCase):
    """第六輪多輪對話測試第一批（matcher 端）。"""

    def test_conditional_and_relax_are_not_negation(self):
        self.assertEqual(m.detect_pay_method_labels("日領沒有的話週領也行"), ["日領", "週領"])
        self.assertEqual(m.detect_pay_method_labels("沒有日領的話週領也可以", negated=True), [])

    def test_negation_after_a_space_or_comma(self):
        for text in ["夜班 不要", "夜班，不要", "夜班 不行"]:
            self.assertEqual(m.extract_shift_labels(text), [], text)
            self.assertEqual(m.extract_shift_labels(text, negated=True), ["大夜班"], text)

    def test_more_postfix_negations(self):
        for text in ["晚上不能上班", "夜班不做", "夜班做不來"]:
            self.assertEqual(m.extract_shift_labels(text), [], text)

    def test_weekend_off_is_leave_not_holiday_shift(self):
        for text in ["週末休", "假日休息", "假日不上班", "六日休", "週末要休息"]:
            self.assertEqual(m.extract_shift_labels(text), [], text)
            self.assertEqual(m.extract_leave_labels(text), ["週休二日"], text)
        self.assertEqual(m.extract_shift_labels("假日班"), ["假日班"])

    def test_four_shift_two_rotation_is_only_a_shift(self):
        self.assertEqual(m.extract_shift_labels("四班二輪"), ["輪班"])
        self.assertEqual(m.extract_leave_labels("四班二輪"), [])

    def test_everyday_wordings(self):
        self.assertEqual(m.detect_pay_method_labels("月薪"), ["月領"])
        self.assertEqual(m.detect_pay_method_labels("做一天領一天"), ["日領"])
        self.assertEqual(m.detect_pay_method_labels("兩週領"), ["雙週領"])
        self.assertEqual(m.extract_leave_labels("休禮拜一"), ["休日一"])
        self.assertEqual(m.extract_shift_labels("早上"), ["早班"])
        # 第六輪起全職/兼職獨立成一項，不再算在班別裡
        self.assertEqual(m.extract_worktype_labels("part time"), ["兼職"])
        self.assertEqual(m.extract_shift_labels("I accept anything"), [])
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("送貨的工作")), "外送")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("櫃台")), "門市")
        self.assertEqual(m.detect_category_label(m.clean_text_for_search("辦公室在哪裡")), "")

    def test_flexible_shift_matches_free_scheduling_jobs(self):
        self.assertIn("彈性排班", m.job_shift_labels({"班別": "早班", "休假方式": "自由報班"}))

    def test_vendor_families_and_spaces(self):
        jobs = [{"系統廠商名稱": "呷哺呷哺"}, {"系統廠商名稱": "強茂永安廠(代招)"}, {"系統廠商名稱": "LADY M"}]
        self.assertEqual(m.detect_brand_label("呷哺有缺嗎", jobs), "呷哺呷哺")
        self.assertEqual(m.detect_brand_label("強茂的工作", jobs), "強茂")
        self.assertEqual(m.detect_brand_label("LADY M的工作", jobs), "LADY M")

    def test_changing_mind_is_not_a_question(self):
        self.assertEqual(m.classify_condition_utterance("還是新竹"), "demand")
        self.assertEqual(m.classify_condition_utterance("算了還是桃園"), "demand")

    def test_questions_about_the_company(self):
        self.assertEqual(m.classify_condition_utterance("你們假日有上班嗎"), "info")
        self.assertEqual(m.classify_condition_utterance("可以找真人客服嗎"), "info")
        self.assertEqual(m.classify_condition_utterance("你們有交通車嗎"), "demand")


class RoundSixExclusionLocationMatcherTests(unittest.TestCase):
    """第六輪多輪對話測試第二批（matcher 端）。"""

    def test_pay_frequency_and_channel_together_mean_both(self):
        self.assertEqual(m.combine_pay_labels(["日領", "現金"], "日領現金的工作"), "日領+現金")
        self.assertEqual(m.combine_pay_labels(["日領", "現金"], "日領或現金都可以"), "日領|現金")
        jobs = [{"領薪方式": "日領,現金"}, {"領薪方式": "月領,週領,現金"}]
        self.assertEqual(m.filter_jobs_by_pay_label(jobs, "日領+現金"), [jobs[0]])

    def test_shift_exclusion_ignores_part_time_flag(self):
        evening_part_time = {"班別": "晚班", "全/兼職": "兼職"}
        self.assertTrue(m.job_is_excluded(evening_part_time, {"shift": {"晚班"}}))

    def test_category_exclusion_keeps_jobs_with_another_category(self):
        forklift = {"職務類別": "倉儲人員,作業員"}
        operator = {"職務類別": "作業員"}
        self.assertFalse(m.job_is_excluded(forklift, {"category": {"製造/作業員"}}))
        self.assertTrue(m.job_is_excluded(operator, {"category": {"製造/作業員"}}))

    def test_sub_role_exclusion(self):
        c = m.clause_clean_text
        self.assertEqual(m.detect_negated_subroles(c("不要外場")), {"外場人員"})
        self.assertEqual(m.detect_negated_category(c("不要外場")), "")
        self.assertTrue(m.job_is_excluded({"職務類別": "外場人員"}, {"role": {"外場人員"}}))
        self.assertFalse(m.job_is_excluded({"職務類別": "內場人員,外場人員"}, {"role": {"外場人員"}}))

    def test_each_occurrence_of_a_shared_district_uses_its_own_county(self):
        jobs = [
            {"縣市": "新竹市", "行政區": "新竹市東區"}, {"縣市": "台南市", "行政區": "台南市東區"},
            {"縣市": "台中市", "行政區": "台中市東區"},
        ]
        self.assertEqual(m.extract_current_target_location("新竹東區或台南東區的餐飲", "", jobs), "新竹市東區|台南市東區")

    def test_county_plus_district_ending_in_ku_is_not_doubled(self):
        jobs = [{"縣市": "嘉義市", "行政區": "嘉義市西區"}, {"縣市": "台中市", "行政區": "台中市東區"}, {"縣市": "新竹市", "行政區": "新竹市東區"}]
        self.assertEqual(m.extract_current_target_location("嘉義市東區", "", jobs), "嘉義市東區")



class RoundSixNewFeatureMatcherTests(unittest.TestCase):
    """第六輪第三批：全職/兼職、薪資篩選（matcher 端）。"""

    def test_salary_wordings(self):
        cases = {
            "時薪200以上": ["時薪200"], "月薪3萬5": ["月薪35000"], "月薪三萬五以上的工作": ["月薪35000"],
            "薪水要有四萬": ["月薪40000"], "35k以上": ["月薪35000"], "一個月3萬2": ["月薪32000"],
            "時薪兩百五": ["時薪250"], "200以上": ["時薪200"],
        }
        for text, expected in cases.items():
            self.assertEqual(m.detect_salary_labels(text), expected, text)

    def test_numbers_that_are_not_salary(self):
        for text in ("早上8點上班", "我有2個小孩", "時薪多少", "我今年30歲", "時薪200以下", "做一休一 2天"):
            self.assertEqual(m.detect_salary_labels(text), [], text)

    def test_job_salary_text_formats(self):
        cases = {
            "時薪\\$196~215": [("時薪", 196)], "月薪33-38k": [("月薪", 33000)],
            "月薪\\$31,500-32,100": [("月薪", 31500)], "年薪120萬起": [("月薪", 100000)],
            "日班薪資 42500  夜班薪資 49000": [("月薪", 42500), ("月薪", 49000)],
            "月薪35000元 早班時薪\\$196 晚班時薪\\$250": [("月薪", 35000), ("時薪", 196), ("時薪", 250)],
            "兼職196/ h   全職32000": [("時薪", 196), ("月薪", 32000)],
            "196+14工時獎金": [("時薪", 196)], "蝦皮內勤(測試)": [],
        }
        for text, expected in cases.items():
            self.assertEqual([(k, round(v)) for k, v in m.job_salary_levels({"薪資": text})], expected, text)

    def test_salary_filter_uses_the_lowest_of_each_part(self):
        jobs = [{"薪資": "時薪196~215"}, {"薪資": "時薪230"}, {"薪資": "日班 30000 夜班 36000"}, {"薪資": "月薪40000"}]
        self.assertEqual(m.filter_jobs_by_salary_label(jobs, "時薪200"), [jobs[1]])
        self.assertEqual(m.filter_jobs_by_salary_label(jobs, "月薪35000"), [jobs[2], jobs[3]])
        self.assertEqual(m.format_salary_label("月薪35000"), "月薪35,000元以上")

    def test_salary_words_are_not_pay_frequency(self):
        self.assertEqual(m.detect_pay_method_labels(m.mask_salary_phrases("月薪3萬5")), [])
        self.assertEqual(m.detect_pay_method_labels(m.mask_salary_phrases("日領 時薪200")), ["日領"])

    def test_worktype(self):
        self.assertEqual(m.extract_worktype_labels("正職早班"), ["全職"])
        self.assertEqual(m.extract_shift_labels("正職早班"), ["早班"])
        self.assertEqual(m.extract_worktype_labels("不要兼職", negated=True), ["兼職"])
        self.assertEqual(m.job_worktype_labels({"全/兼職": "全職,兼職"}), {"全職", "兼職"})
        self.assertTrue(m.job_is_excluded({"全/兼職": "兼職"}, {"worktype": {"兼職"}}))
        self.assertFalse(m.job_is_excluded({"全/兼職": "全職,兼職"}, {"worktype": {"兼職"}}))



class RoundSevenMatcherTests(unittest.TestCase):
    """第七輪多輪對話測試（5 個 agent：新功能壓力測試、打字很亂、超長對話、敏感刁鑽情境、整個資料庫組合條件）。"""

    def test_not_is_negation_only_right_before_the_keyword(self):
        self.assertEqual(m.extract_worktype_labels("不是兼職的", negated=True), ["兼職"])
        self.assertEqual(m.extract_worktype_labels("我不是學生想找全職"), ["全職"])

    def test_more_negation_wordings(self):
        for text in ("沒辦法上夜班", "不方便上夜班", "討厭夜班", "夜班不方便", "夜班免", "夜班NG", "夜班❌", "NO夜班"):
            self.assertEqual(m.extract_shift_labels(text, negated=True), ["大夜班"], text)
            self.assertEqual(m.extract_shift_labels(text), [], text)

    def test_amounts_that_are_not_wanted_salary(self):
        for text in ("我卡債50萬", "我欠了20萬", "我想存30萬", "我體重100多公斤", "工作200多人的公司", "日薪1500以上", "日領1500"):
            self.assertEqual(m.detect_salary_labels(text), [], text)
        self.assertEqual(m.detect_unparsed_salary_request("日薪1500以上"), "daily")
        self.assertEqual(m.detect_unparsed_salary_request("薪水高一點的"), "vague")
        self.assertEqual(m.detect_salary_labels("3萬也可以"), ["月薪30000"])

    def test_salary_wording_is_not_pay_frequency(self):
        for text in ("月薪至少三萬", "月薪要三萬以上", "月薪多少", "我上個月薪水少了兩千"):
            self.assertEqual(m.detect_pay_method_labels(m.mask_salary_phrases(text)), [], text)
        self.assertEqual(m.detect_pay_method_labels("週結的"), ["週領"])

    def test_relaxed_category_respects_filled_role(self):
        courier = {"職務類別": "外送員", "行業別": "物流業", "職缺名稱(對外)": "外送員"}
        blank = {"職務類別": "", "行業別": "物流業", "職缺名稱(對外)": "小幫手"}
        self.assertFalse(m.job_matches_category_filter(courier, "理貨/倉儲", allow_relaxed=True))
        self.assertTrue(m.job_matches_category_filter(blank, "理貨/倉儲", allow_relaxed=True))

    def test_note_only_vendor_name_is_not_a_brand(self):
        self.assertEqual(m._vendor_core_name("(代招)"), "")
        self.assertEqual(m._vendor_core_name("錢都(代招)"), "錢都")

    def test_people_who_are_not_job_seekers(self):
        for text in ("我是廠商想徵人 需要10個作業員在桃園", "我在蝦皮門市上班 想離職", "我上個月薪水少了兩千", "我昨天去面試 結果呢"):
            self.assertEqual(m.classify_condition_utterance(text), "info", text)


if __name__ == "__main__":
    unittest.main()
