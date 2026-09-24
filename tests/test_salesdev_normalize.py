import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from salesdev import normalize


class RepairMojibakeTests(unittest.TestCase):
    """小雞上工標題的表情符號變成「ð¥」（UTF-8 被當 latin-1 解碼）。"""

    def test_garbled_emoji_is_restored(self):
        # 中文在原始資料裡是正常的，只有表情符號壞掉——模擬實際看到的樣子
        garbled = "🔥🔥".encode("utf-8").decode("latin-1") + "全台徵人"
        self.assertEqual(normalize.repair_mojibake(garbled), "🔥🔥全台徵人")

    def test_normal_text_is_untouched(self):
        self.assertEqual(normalize.repair_mojibake("日領5200 🔥 café"), "日領5200 🔥 café")

    def test_empty(self):
        self.assertEqual(normalize.repair_mojibake(None), "")


class ParseAddressTests(unittest.TestCase):
    def _parse(self, address):
        parts = normalize.parse_address(address)
        return parts["county"], parts["district"], parts["road"]

    def test_masked_door_numbers_all_land_on_the_same_road(self):
        expected = ("桃園市", "桃園區", "桃鶯路")
        for address in ("台灣桃園市桃園區桃鶯路XX號", "台灣桃園市桃園區桃鶯路437號", "桃園市桃園區桃鶯路0號", "台灣桃園市桃園區桃鶯路4**號"):
            self.assertEqual(self._parse(address), expected, address)

    def test_tai_variant_and_taiwan_prefix(self):
        self.assertEqual(self._parse("臺中市神岡區社南街5巷15弄15號"), ("台中市", "神岡區", "社南街"))

    def test_two_counties_glued_together_uses_the_last(self):
        # 刊登者把公司地址填進街道欄，被接成兩個縣市
        self.assertEqual(self._parse("台灣彰化縣彰化市新北市三重區自強路5段110號"), ("新北市", "三重區", "自強路五段"))

    def test_repeated_district_is_collapsed(self):
        self.assertEqual(self._parse("台灣新北市淡水區淡水區北新路一段18-2號"), ("新北市", "淡水區", "北新路一段"))

    def test_county_seat_named_like_a_county_is_not_mistaken(self):
        self.assertEqual(self._parse("新竹縣竹北市光復北路58號"), ("新竹縣", "竹北市", "光復北路"))

    def test_three_character_district_ending_with_shi(self):
        self.assertEqual(self._parse("台南市新市區中山路"), ("台南市", "新市區", "中山路"))

    def test_village_prefix_is_removed(self):
        self.assertEqual(self._parse("桃園市龜山區大華村頂湖一街"), ("桃園市", "龜山區", "頂湖一街"))
        self.assertEqual(self._parse("台灣台中市神岡區豐洲里豐工中路XX號"), ("台中市", "神岡區", "豐工中路"))

    def test_road_inside_parentheses(self):
        self.assertEqual(self._parse("台南市歸仁區沙崙示範場域(高發二路360號 )"), ("台南市", "歸仁區", "高發二路"))

    def test_masked_road_name_is_not_a_road(self):
        self.assertEqual(self._parse("桃園市中壢區XX路1號"), ("桃園市", "中壢區", ""))

    def test_area_only(self):
        self.assertEqual(self._parse("台北市中山區"), ("台北市", "中山區", ""))

    def test_full_width_digits(self):
        self.assertEqual(self._parse("新北市三重區自強路５段"), ("新北市", "三重區", "自強路五段"))


class GroupKeyTests(unittest.TestCase):
    def test_different_agencies_same_road_share_a_group(self):
        key_a, label_a, kind_a = normalize.group_key_for("桃園市桃園區桃鶯路", "天泰人力銀行", "(R)桃鶯路 簡單組包")
        key_b, _, _ = normalize.group_key_for("台灣桃園市桃園區桃鶯路XX號", "悅盛人力資源有限公司", "日薪5940先別滑")
        self.assertEqual(key_a, key_b)
        self.assertEqual(kind_a, "address")
        self.assertEqual(label_a, "桃園市桃園區桃鶯路")

    def test_without_address_same_company_same_title_is_a_duplicate(self):
        key_a, _, kind = normalize.group_key_for("", "皇家人力", "【急徵】大安區 家庭駕駛/司機-6 🔥")
        key_b, _, _ = normalize.group_key_for("", "皇家人力", "大安區 家庭駕駛／司機-6")
        self.assertEqual(kind, "title")
        self.assertEqual(key_a, key_b)

    def test_without_address_different_titles_are_different(self):
        key_a, _, _ = normalize.group_key_for("", "皇家人力", "大安區 家庭駕駛/司機-6")
        key_b, _, _ = normalize.group_key_for("", "皇家人力", "士林區 家庭駕駛/司機-5")
        self.assertNotEqual(key_a, key_b)

    def test_group_doc_id_is_stable_and_safe(self):
        doc_id = normalize.group_doc_id("addr:桃園市|桃園區|桃鶯路")
        self.assertEqual(doc_id, normalize.group_doc_id("addr:桃園市|桃園區|桃鶯路"))
        self.assertNotIn("/", doc_id)


class JobIdTests(unittest.TestCase):
    def test_chickpt_url_becomes_slug(self):
        self.assertEqual(normalize.job_doc_id("chickpt", "https://www.chickpt.com.tw/job-XDA1QdnqMmjx"), "chickpt_job-XDA1QdnqMmjx")

    def test_104_id_from_url_matches_between_sheet_and_scraper(self):
        self.assertEqual(normalize.job_id_from_url("104", "https://www.104.com.tw/job/7kcx5"), "7kcx5")
        self.assertEqual(normalize.job_id_from_url("104", "https://www.104.com.tw/job/7kcx5?jobsource=x"), "7kcx5")

    def test_chickpt_id_from_url_is_the_url(self):
        url = "https://www.chickpt.com.tw/job-XDA1QdnqMmjx"
        self.assertEqual(normalize.job_id_from_url("chickpt", url), url)


if __name__ == "__main__":
    unittest.main()
