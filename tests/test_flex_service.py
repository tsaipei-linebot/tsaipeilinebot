import os
import sys
import unittest
from unittest.mock import patch

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
        primary_button = footer_buttons[-1]
        self.assertEqual(primary_button.color, "#ea580c")

    def test_category_badge_uses_brand_orange(self):
        card = f.create_job_flex_card([self._card_job()], "user1", "新莊")
        body = card.contents.contents[0].body
        tags_row = body.contents[2]
        category_badge_text = tags_row.contents[-1].contents[0]
        self.assertEqual(category_badge_text.text, "門市")
        self.assertEqual(category_badge_text.color, "#c2410c")


class CreateJobFlexCardResumeClickTrackingTests(unittest.TestCase):
    """使用者要求要能知道誰點了「填寫線上履歷」按鈕：LINE 的 uri 按鈕點擊
    完全不會觸發 webhook，唯一能觀察到的方式是讓按鈕先連到我們自己的
    /apply-click 轉址端點（見 main.py）記錄，再轉址到真正的履歷網站。
    這裡驗證 SERVICE_BASE_URL 有設定時，按鈕會改連到轉址端點並帶對的參數；
    沒設定時維持原本「直接連到履歷網站」的行為，不能讓按鈕失效。"""

    def _card_job(self, **overrides):
        job = {
            "職缺名稱(對外)": "測試門市職缺", "職缺名稱": "測試門市職缺(內部)",
            "薪資": "時薪200", "班別": "早班", "職務類別": "門市",
            "縣市": "新北市", "行政區": "新莊區",
        }
        job.update(overrides)
        return job

    def test_uses_direct_resume_link_when_service_base_url_not_configured(self):
        with patch("services.flex_service.SERVICE_BASE_URL", ""):
            card = f.create_job_flex_card([self._card_job()], "U1234", "新莊")

        apply_button = card.contents.contents[0].footer.contents[-1]
        self.assertTrue(apply_button.action.uri.startswith("https://resume.tsaipei.com.tw"))

    def test_routes_through_apply_click_endpoint_when_service_base_url_configured(self):
        with patch("services.flex_service.SERVICE_BASE_URL", "https://recruitment-bot-example.a.run.app"):
            card = f.create_job_flex_card([self._card_job()], "U1234", "新莊")

        apply_button = card.contents.contents[0].footer.contents[-1]
        uri = apply_button.action.uri
        self.assertTrue(uri.startswith("https://recruitment-bot-example.a.run.app/apply-click?"))
        self.assertIn("uid=U1234", uri)
        self.assertIn("type=Service", uri)
        self.assertIn("job=%E6%B8%AC%E8%A9%A6%E9%96%80%E5%B8%82%E8%81%B7%E7%BC%BA%28%E5%85%A7%E9%83%A8%29", uri)

    def test_trailing_slash_on_service_base_url_does_not_produce_double_slash(self):
        with patch("services.flex_service.SERVICE_BASE_URL", "https://recruitment-bot-example.a.run.app/"):
            card = f.create_job_flex_card([self._card_job()], "U1234", "新莊")

        apply_button = card.contents.contents[0].footer.contents[-1]
        self.assertTrue(apply_button.action.uri.startswith("https://recruitment-bot-example.a.run.app/apply-click?"))
        self.assertNotIn("run.app//apply-click", apply_button.action.uri)


if __name__ == "__main__":
    unittest.main()
