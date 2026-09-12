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


class FormatCleanLocationMultipleDistrictMatchTests(unittest.TestCase):
    """實測回報案例：一筆職缺涵蓋範圍很廣（同時橫跨很多縣市/行政區），使用者
    只問籠統的縣市層級地點（例如「台南」）時，這筆職缺剛好在該縣市底下有
    不只一個行政區有缺（下營、佳里）。原本的寫法「找到第一個符合就回傳」
    只會顯示下營，完全看不出佳里也有——這裡驗證改成「全部列出」後兩個都會
    顯示，不再只看到第一個。"""

    def test_lists_all_matching_districts_not_just_the_first(self):
        job = {
            "縣市": "宜蘭縣,桃園市,高雄市,基隆市,新竹縣,嘉義縣,彰化縣,台中市,台北市,新竹市,屏東縣,台南市,澎湖縣,雲林縣,苗栗縣",
            "行政區": "新竹縣竹北市,台南市下營區,台南市佳里區",
            "行業別": "服務業",
        }
        self.assertEqual(f.format_clean_location(job, "台南"), "台南市下營區、台南市佳里區")

    def test_single_matching_district_is_unaffected(self):
        job = {"縣市": "桃園市", "行政區": "桃園市八德區,桃園市蘆竹區", "行業別": "服務業"}
        self.assertEqual(f.format_clean_location(job, "八德"), "八德區")


class FormatCleanLocationSameCountyScopeTests(unittest.TestCase):
    """實測回報案例：「同縣市退讓建議」功能推薦的職缺，如果本身涵蓋範圍橫跨
    很多縣市（例如同時橫跨 15 個縣市），卡片的地點欄位會把「全部」縣市都印
    出來，跟文字回覆（只講「桃園市的...有相關職缺」）兜不起來，使用者反映
    「明確詢問八德，卡片中的地點應該只要列出桃園市的其他區，不應該把所有
    縣市放進來」。這裡驗證新增的 same_county_scope 參數：只用指定縣市底下
    的行政區組字，不受職缺橫跨其他縣市影響。"""

    def _broad_job(self, district):
        return {
            "縣市": "宜蘭縣,桃園市,高雄市,基隆市,新竹縣,嘉義縣,彰化縣,台中市,台北市,新竹市,屏東縣,台南市,澎湖縣,雲林縣,苗栗縣",
            "行政區": district,
            "行業別": "服務業",
        }

    def test_scopes_down_to_target_county_when_few_districts(self):
        job = self._broad_job("台南市下營區,台南市佳里區")
        self.assertEqual(f.format_clean_location(job, same_county_scope="台南市"), "台南市（下營區、佳里區）")

    def test_falls_back_to_generic_suffix_when_scoped_county_still_has_many_districts(self):
        job = self._broad_job(
            "桃園市桃園區,桃園市蘆竹區,桃園市大園區,桃園市中壢區,桃園市平鎮區,"
            "桃園市新屋區,桃園市楊梅區,桃園市觀音區,桃園市龜山區,桃園市大溪區"
        )
        self.assertEqual(f.format_clean_location(job, same_county_scope="桃園市"), "桃園市 各區據點（自選區域）")

    def test_scoped_county_with_no_matching_district_returns_bare_county_name(self):
        job = self._broad_job("台南市下營區")
        self.assertEqual(f.format_clean_location(job, same_county_scope="桃園市"), "桃園市")

    def test_normal_single_county_job_unaffected(self):
        # 一般（非跨縣市）的職缺傳 same_county_scope 進來，結果要跟原本沒帶
        # 這個參數、單純用行政區數量級距判斷時一致，不能因為新參數而改變。
        job = {"縣市": "桃園市", "行政區": "桃園市蘆竹區,桃園市龜山區", "行業別": "服務業"}
        self.assertEqual(
            f.format_clean_location(job, same_county_scope="桃園市"),
            f.format_clean_location(job),
        )


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


class CreateJobFlexCardSameCountyScopeTests(unittest.TestCase):
    """驗證 create_job_flex_card() 有把 same_county_scope 參數往下傳給
    format_clean_location()，「同縣市退讓建議」卡片才能正確縮小地點顯示範圍
    （見 handlers/message_handler.py 的縣市退讓建議分支）。"""

    def test_same_county_scope_narrows_displayed_location(self):
        job = {
            "職缺名稱(對外)": "測試職缺", "職缺名稱": "測試職缺",
            "薪資": "時薪200", "班別": "早班", "職務類別": "門市",
            "縣市": "宜蘭縣,桃園市,高雄市,基隆市,新竹縣,嘉義縣,彰化縣,台中市,台北市,新竹市,屏東縣,台南市,澎湖縣,雲林縣,苗栗縣",
            "行政區": "台南市下營區,台南市佳里區",
        }
        card = f.create_job_flex_card([job], "user1", "", same_county_scope="台南市")
        texts = _detail_texts(card.contents.contents[0])
        self.assertTrue(any("📍 地點：台南市（下營區、佳里區）" in t for t in texts))
        self.assertFalse(any("宜蘭縣" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
