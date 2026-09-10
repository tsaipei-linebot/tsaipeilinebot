import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)

from services import flex_service as f


def _detail_texts(bubble):
    detail_box = bubble.body.contents[-1]
    return [c.text for c in detail_box.contents]


class FormatCleanLocationCountyPrefixTests(unittest.TestCase):
    """使用者反映：同仁為了避免同名行政區跨縣市搞混（例如中山區台北市、
    基隆市都有），習慣在「行政區」欄位直接寫成「桃園市八德區」這種帶縣市
    前綴的完整寫法，不是單純「八德區」。這不影響地區比對邏輯是否命中，
    但直接組成顯示文字會變成「桃園市（桃園市八德區、桃園市蘆竹區）」這種
    縣市名稱重複兩次的累贅呈現，這裡驗證顯示前有把重複的縣市前綴去掉。"""

    def test_strips_duplicated_county_prefix_from_multiple_districts(self):
        job = {"縣市": "桃園市", "行政區": "桃園市八德區,桃園市蘆竹區", "行業別": "服務業"}
        self.assertEqual(f.format_clean_location(job), "桃園市（八德區、蘆竹區）")

    def test_strips_prefix_regardless_of_tai_variant(self):
        # 縣市欄位寫半形「台」、行政區欄位卻寫全形「臺」，也要能正確去掉前綴
        job = {"縣市": "台北市", "行政區": "臺北市中山區", "行業別": "服務業"}
        self.assertEqual(f.format_clean_location(job), "台北市（中山區）")

    def test_no_prefix_present_is_unaffected(self):
        # 行政區欄位本來就沒有帶縣市前綴時，維持原本的行為不受影響
        job = {"縣市": "桃園市", "行政區": "八德區", "行業別": "服務業"}
        self.assertEqual(f.format_clean_location(job), "桃園市（八德區）")

    def test_target_location_match_also_uses_stripped_district(self):
        job = {"縣市": "桃園市", "行政區": "桃園市八德區", "行業別": "服務業"}
        self.assertEqual(f.format_clean_location(job, "八德"), "八德區")


class CreateJobFlexCardPayMethodTests(unittest.TestCase):
    def test_shows_pay_method_line_when_present(self):
        job = {
            "職缺名稱(對外)": "測試職缺A", "職缺名稱": "測試職缺A",
            "薪資": "時薪200", "領薪方式": "日領", "班別": "早班",
            "縣市": "新北市", "行政區": "新莊區",
        }
        card = f.create_job_flex_card([job], "user1", "新莊")
        texts = _detail_texts(card.contents.contents[0])
        self.assertTrue(any("領薪方式：日領" in t for t in texts))

    def test_omits_pay_method_line_when_missing(self):
        job = {
            "職缺名稱(對外)": "測試職缺B", "職缺名稱": "測試職缺B",
            "薪資": "月薪32000", "班別": "早班",
            "縣市": "新北市", "行政區": "新莊區",
        }
        card = f.create_job_flex_card([job], "user1", "新莊")
        texts = _detail_texts(card.contents.contents[0])
        self.assertFalse(any("領薪方式" in t for t in texts))


class CreateJobFlexCardBrandColorTests(unittest.TestCase):
    """使用者要求卡片改用材霈的品牌橘色（跟 delivery/static/style.css 的
    --brand/#ea580c、--brand-dark/#c2410c 同一套，內部系統網頁共用的配色），
    取代原本 LINE 預設的綠色，讓卡片有材霈自己的識別，不是通用感覺的配色。"""

    def _card_job(self):
        return {
            "職缺名稱(對外)": "測試職缺", "職缺名稱": "測試職缺",
            "薪資": "時薪200", "班別": "早班", "職務類別": "門市",
            "縣市": "新北市", "行政區": "新莊區",
        }

    def test_header_tag_uses_brand_orange(self):
        card = f.create_job_flex_card([self._card_job()], "user1", "新莊")
        header_text = card.contents.contents[0].body.contents[0]
        self.assertEqual(header_text.text, "🎯 材霈推薦職缺")
        self.assertEqual(header_text.color, "#ea580c")

    def test_primary_apply_button_uses_brand_orange(self):
        card = f.create_job_flex_card([self._card_job()], "user1", "新莊")
        footer_buttons = card.contents.contents[0].footer.contents
        # 最後一顆是新加的「📅 預約面試」連結按鈕，主要應徵按鈕是倒數第二顆。
        primary_button = footer_buttons[-2]
        self.assertEqual(primary_button.color, "#ea580c")

    def test_category_badge_uses_brand_orange(self):
        card = f.create_job_flex_card([self._card_job()], "user1", "新莊")
        body = card.contents.contents[0].body
        tags_row = body.contents[2]
        category_badge_text = tags_row.contents[-1].contents[0]
        self.assertEqual(category_badge_text.text, "門市")
        self.assertEqual(category_badge_text.color, "#c2410c")


class CreateJobFlexCardInterviewButtonTests(unittest.TestCase):
    """求職者填完履歷後可以直接約面試時間，卡片上要多一顆「📅 預約面試」按鈕，
    按下去送出的文字要帶著 Notion 唯一識別鍵（職缺名稱），讓後續流程能精準
    比對回同一筆職缺。"""

    def _card_job(self):
        return {
            "職缺名稱(對外)": "測試職缺", "職缺名稱": "測試職缺(內部)",
            "薪資": "時薪200", "班別": "早班", "職務類別": "門市",
            "縣市": "新北市", "行政區": "新莊區",
        }

    def test_footer_has_interview_booking_button_with_internal_title(self):
        card = f.create_job_flex_card([self._card_job()], "user1", "新莊")
        footer_buttons = card.contents.contents[0].footer.contents
        booking_button = footer_buttons[-1]
        self.assertEqual(booking_button.action.label, "📅 預約面試")
        self.assertEqual(booking_button.action.text, "預約面試 測試職缺(內部)")


if __name__ == "__main__":
    unittest.main()
