import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.repository import (
    _normalize_vehicle_no,
    resolve_vehicle_rider_cooperation_type,
    vehicle_event_error,
    vehicle_matches_filters,
)
from delivery.vehicle_report import handle_vehicle_report, parse_vehicle_report


class ParseVehicleReportTests(unittest.TestCase):
    def test_checkout_message_parses(self):
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：2026-8-26\n"
            "結束日期：\n"
            "車號：ERV-2360\n"
            "服務門市：臺北市北投區八仙里公舘路423巷6弄"
        )
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["event_type"], "checkout")
        self.assertEqual(result["vendor"], "ud")
        self.assertEqual(result["personnel_name"], "李睿哲")
        self.assertEqual(result["vehicle_no"], "ERV-2360")
        self.assertEqual(result["event_date"], "2026-08-26")
        self.assertEqual(result["location"], "臺北市北投區八仙里公舘路423巷6弄")
        # 2026-09-16 新增：電話/待維修/備註都是選填，沒填時要是空字串/False，
        # 不能讓解析失敗。
        self.assertEqual(result["phone"], "")
        self.assertEqual(result["note"], "")
        self.assertFalse(result["needs_maintenance"])

    def test_optional_phone_note_and_maintenance_fields_parse(self):
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：2026-8-26\n"
            "結束日期：\n"
            "車號：ERV-2360\n"
            "服務門市：台北市\n"
            "電話：0912345678\n"
            "待維修：是\n"
            "備註：輪胎有點磨損"
        )
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["phone"], "0912345678")
        self.assertTrue(result["needs_maintenance"])
        self.assertEqual(result["note"], "輪胎有點磨損")

    def test_needs_maintenance_accepts_shi_or_you_as_synonyms(self):
        # 2026-09-17 起「待維修」跟意外事件回報的是否類欄位一樣，
        # 「是」「有」互通當肯定詞，其餘一律視為沒有勾選。
        for raw, expected in [
            ("否", False), ("無", False), ("", False), ("要", False),
            ("是", True), (" 是 ", True), ("有", True), (" 有 ", True),
        ]:
            text = (
                "車輛管理\n"
                f"廠商：UD\n姓名：李睿哲\n開始日期：2026-8-26\n結束日期：\n"
                f"車號：ERV-2360\n服務門市：台北市\n待維修：{raw}"
            )
            result = parse_vehicle_report(text)
            self.assertEqual(result["needs_maintenance"], expected, f"raw={raw!r}")

    def test_return_message_parses(self):
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：\n"
            "結束日期：2026-8-25\n"
            "車號：ERV-6956\n"
            "還車地點：臺北市北投區八仙里公舘路423巷6弄"
        )
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["event_type"], "return")
        self.assertEqual(result["event_date"], "2026-08-25")
        self.assertEqual(result["vehicle_no"], "ERV-6956")

    def test_literal_blank_placeholder_in_end_date_is_treated_as_empty(self):
        # 同仁複製範本時，把說明用的「（空白）」文字也一起貼進「結束日期」
        # 欄位（範本原本的用意是提醒「這欄不用填」），這應該當成沒填，正常
        # 判斷成領車，而不是回覆「日期格式看不懂」。
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：2026-8-26\n"
            "結束日期：（空白）\n"
            "車號：ERV-2360\n"
            "服務門市：台北市..."
        )
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["event_type"], "checkout")
        self.assertEqual(result["event_date"], "2026-08-26")

    def test_literal_blank_placeholder_variants(self):
        for placeholder in ["空白", "(空白)", "（空白）", " 空白 "]:
            text = (
                "車輛管理\n"
                f"廠商：UD\n姓名：李睿哲\n開始日期：2026-8-26\n結束日期：{placeholder}\n"
                "車號：ERV-2360\n服務門市：台北市"
            )
            result = parse_vehicle_report(text)
            self.assertTrue(result["ok"], f"placeholder {placeholder!r} should parse ok, got {result}")

    def test_tolerates_template_header_lines(self):
        text = (
            "✅ 回報格式（照填即可）\n"
            "請用以下格式回覆\n"
            "車輛管理\n"
            "廠商：蝦皮三輪\n"
            "姓名：王小明\n"
            "開始日期：2026-1-2\n"
            "結束日期：\n"
            "車號：ABC-1234\n"
            "服務門市：某某門市"
        )
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["vendor"], "shopee")

    def test_both_dates_filled_is_ambiguous(self):
        text = "車輛管理\n廠商：UD\n姓名：李睿哲\n開始日期：2026-1-1\n結束日期：2026-1-2\n車號：ERV-1\n服務門市：x"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_dates")

    def test_neither_date_filled_is_missing_fields(self):
        text = "車輛管理\n廠商：UD\n姓名：李睿哲\n開始日期：\n結束日期：\n車號：ERV-1\n服務門市：x"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_fields")

    def test_missing_vehicle_no_is_missing_fields(self):
        text = "車輛管理\n廠商：UD\n姓名：李睿哲\n開始日期：2026-1-1\n結束日期：\n服務門市：x"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_fields")

    def test_invalid_vendor(self):
        text = "車輛管理\n廠商：黑貓\n姓名：李睿哲\n開始日期：2026-1-1\n結束日期：\n車號：ERV-1\n服務門市：x"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_vendor")

    def test_invalid_date_format(self):
        text = "車輛管理\n廠商：UD\n姓名：李睿哲\n開始日期：昨天\n結束日期：\n車號：ERV-1\n服務門市：x"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_date")

    def test_unrelated_text_is_not_a_report(self):
        # 群組裡的日常聊天完全沒有「車輛管理」這個啟動關鍵字，不該被當成
        # 「格式錯誤」對待（那樣同仁在群組裡聊天會一直被機器人回覆格式錯誤
        # 訊息）。
        result = parse_vehicle_report("你好，請問明天有班嗎？")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_a_report")

    def test_missing_trigger_line_is_not_a_report_even_with_all_fields_filled(self):
        # 就算欄位全部填對，只要沒有單獨一行的「車輛管理」啟動關鍵字，一律
        # 當成不是在回報、完全不回覆——這是刻意的嚴格設計，避免同仁在群組
        # 聊天時剛好提到「車號」之類的字眼被誤判成回報。
        text = "廠商：UD\n姓名：李睿哲\n開始日期：2026-8-26\n結束日期：\n車號：ERV-2360\n服務門市：台北市"
        result = parse_vehicle_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_a_report")

    def test_handle_vehicle_report_stays_silent_for_unrelated_text(self):
        self.assertEqual(handle_vehicle_report("早安，今天天氣不錯"), "")

    def test_vehicle_no_normalized_to_uppercase(self):
        # 同仁在群組手打車號時大小寫不一定跟網頁登記的一樣（例如打成小寫的
        # erv-2360），解析出來要正規化成大寫，才能跟 repository 那邊查詢時
        # 用的正規化方式（_normalize_vehicle_no）一致，避免明明車輛存在卻
        # 查不到。
        text = "車輛管理\n廠商：UD\n姓名：李睿哲\n開始日期：2026-8-26\n結束日期：\n車號：erv-2360\n服務門市：台北市"
        result = parse_vehicle_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["vehicle_no"], "ERV-2360")


class VehicleEventErrorTests(unittest.TestCase):
    def _vehicle(self, **overrides):
        base = {"vehicle_no": "ERV-1", "vendor": "ud", "status": "available"}
        base.update(overrides)
        return base

    def test_vehicle_not_found(self):
        self.assertEqual(vehicle_event_error(None, "ud", "checkout"), "vehicle_not_found")

    def test_vendor_mismatch(self):
        vehicle = self._vehicle(vendor="ud")
        self.assertEqual(vehicle_event_error(vehicle, "shopee", "checkout"), "vendor_mismatch")

    def test_checkout_allowed_when_available(self):
        vehicle = self._vehicle(status="available")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "checkout"), "")

    def test_checkout_blocked_when_in_use(self):
        vehicle = self._vehicle(status="in_use")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "checkout"), "not_available")

    def test_checkout_blocked_when_maintenance(self):
        # 2026-09-16：跟「使用中被別人領走」拆成不同錯誤代碼，訊息才能講
        # 清楚車子是本來就已經在等維修、不是被別人領走了。
        vehicle = self._vehicle(status="maintenance")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "checkout"), "already_maintenance")

    def test_return_allowed_when_in_use(self):
        vehicle = self._vehicle(status="in_use")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "return"), "")

    def test_return_blocked_when_available(self):
        vehicle = self._vehicle(status="available")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "return"), "not_in_use")

    def test_return_blocked_when_maintenance(self):
        vehicle = self._vehicle(status="maintenance")
        self.assertEqual(vehicle_event_error(vehicle, "ud", "return"), "not_in_use")


class VehicleMatchesFiltersTests(unittest.TestCase):
    def _vehicle(self, **overrides):
        base = {"vehicle_no": "ERV-1234", "vendor": "ud", "status": "available", "wheel_type": "three_wheel"}
        base.update(overrides)
        return base

    def test_no_filters_matches(self):
        self.assertTrue(vehicle_matches_filters(self._vehicle()))

    def test_wheel_type_filter_matches(self):
        self.assertTrue(vehicle_matches_filters(self._vehicle(wheel_type="two_wheel"), wheel_type_filter="two_wheel"))

    def test_wheel_type_filter_excludes_non_matching(self):
        self.assertFalse(vehicle_matches_filters(self._vehicle(wheel_type="three_wheel"), wheel_type_filter="two_wheel"))

    def test_wheel_type_filter_treats_missing_field_as_three_wheel(self):
        # 舊資料（新增這個欄位之前建立的車輛）Firestore 文件裡沒有這個欄位，
        # 用「三輪」篩選時應該要找得到這些舊資料，不能因為欄位缺席就漏掉。
        vehicle_without_field = {"vehicle_no": "ERV-9999", "vendor": "ud", "status": "available"}
        self.assertTrue(vehicle_matches_filters(vehicle_without_field, wheel_type_filter="three_wheel"))
        self.assertFalse(vehicle_matches_filters(vehicle_without_field, wheel_type_filter="two_wheel"))

    def test_service_area_filter_matches(self):
        self.assertTrue(vehicle_matches_filters(self._vehicle(service_area="taipei"), service_area_filter="taipei"))

    def test_service_area_filter_excludes_non_matching(self):
        self.assertFalse(vehicle_matches_filters(self._vehicle(service_area="taipei"), service_area_filter="tainan"))

    def test_service_area_filter_excludes_vehicle_with_no_area_set(self):
        vehicle_without_area = {"vehicle_no": "ERV-9999", "vendor": "ud", "status": "available"}
        self.assertFalse(vehicle_matches_filters(vehicle_without_area, service_area_filter="taipei"))

    def test_vendor_filter_excludes_non_matching(self):
        self.assertFalse(vehicle_matches_filters(self._vehicle(), vendor_filter="shopee"))

    def test_status_filter_excludes_non_matching(self):
        self.assertFalse(vehicle_matches_filters(self._vehicle(), status_filter="in_use"))

    def test_vehicle_no_filter_matches_substring(self):
        self.assertTrue(vehicle_matches_filters(self._vehicle(), vehicle_no_filter="1234"))

    def test_vehicle_no_filter_excludes_non_matching(self):
        self.assertFalse(vehicle_matches_filters(self._vehicle(), vehicle_no_filter="9999"))

    def test_vehicle_no_filter_is_case_insensitive(self):
        # 網頁上車號一律正規化成大寫存放，但同仁在搜尋框打小寫也應該找得到。
        self.assertTrue(vehicle_matches_filters(self._vehicle(), vehicle_no_filter="erv"))


class NormalizeVehicleNoTests(unittest.TestCase):
    def test_uppercases_and_strips(self):
        self.assertEqual(_normalize_vehicle_no("  erv-2360 "), "ERV-2360")

    def test_already_normalized_is_unchanged(self):
        self.assertEqual(_normalize_vehicle_no("ERV-2360"), "ERV-2360")

    def test_none_and_empty(self):
        self.assertEqual(_normalize_vehicle_no(""), "")
        self.assertEqual(_normalize_vehicle_no(None), "")


class EventErrorMessagesTests(unittest.TestCase):
    """2026-09-16：車輛已經是待維修狀態時又被回報領車，訊息要講清楚是
    「本來就在等維修」，不是套用「使用中」那句籠統的訊息。"""

    def test_already_maintenance_message_differs_from_not_available(self):
        from delivery.vehicle_report import EVENT_ERROR_MESSAGES

        self.assertIn("already_maintenance", EVENT_ERROR_MESSAGES)
        self.assertIn("待維修", EVENT_ERROR_MESSAGES["already_maintenance"])
        self.assertNotEqual(EVENT_ERROR_MESSAGES["already_maintenance"], EVENT_ERROR_MESSAGES["not_available"])


class ParseErrorMessagesIncludeExampleTests(unittest.TestCase):
    """格式錯誤的回覆訊息，都要附上正確範例，同仁不用另外去找範本。"""

    def test_missing_fields_message_includes_example(self):
        text = "車輛管理\n廠商：UD"
        reply = handle_vehicle_report(text)
        self.assertIn("正確範例", reply)
        self.assertIn("開始日期：2026-8-26", reply)

    def test_invalid_vendor_message_includes_example(self):
        text = (
            "車輛管理\n"
            "廠商：不存在的廠商\n"
            "姓名：李睿哲\n"
            "開始日期：2026-8-26\n"
            "結束日期：\n"
            "車號：ERV-2360\n"
            "服務門市：臺北市北投區八仙里公舘路423巷6弄"
        )
        reply = handle_vehicle_report(text)
        self.assertIn("正確範例", reply)

    def test_ambiguous_dates_message_includes_example(self):
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：2026-8-26\n"
            "結束日期：2026-8-27\n"
            "車號：ERV-2360\n"
            "服務門市：臺北市北投區八仙里公舘路423巷6弄"
        )
        reply = handle_vehicle_report(text)
        self.assertIn("正確範例", reply)

    def test_invalid_date_message_includes_example(self):
        text = (
            "車輛管理\n"
            "廠商：UD\n"
            "姓名：李睿哲\n"
            "開始日期：不是日期\n"
            "結束日期：\n"
            "車號：ERV-2360\n"
            "服務門市：臺北市北投區八仙里公舘路423巷6弄"
        )
        reply = handle_vehicle_report(text)
        self.assertIn("正確範例", reply)


class ResolveVehicleRiderCooperationTypeTests(unittest.TestCase):
    """騎手身份（2026-09-18 新增）：車輛主檔的 current_holder 是自由輸入
    文字，反查對應人員資料優先用姓名+電話比對，車輛沒填電話時才退而用
    姓名+廠商比對；找不到人、或人員沒設合作方式都回傳 None。"""

    def test_no_current_holder_returns_none_without_any_lookup(self):
        with mock.patch.object(repository, "find_active_personnel_by_name_and_phone") as mock_phone:
            with mock.patch.object(repository, "find_personnel_by_name_vendor") as mock_vendor:
                result = resolve_vehicle_rider_cooperation_type({"vendor": "shopee", "current_holder": ""})
        self.assertIsNone(result)
        mock_phone.assert_not_called()
        mock_vendor.assert_not_called()

    def test_uses_name_and_phone_match_when_phone_present(self):
        vehicle = {"vendor": "shopee", "current_holder": "小明", "current_holder_phone": "0912345678"}
        person = {"id": "p1", "cooperation_type": "two_wheel_contract"}
        coop = {"id": "two_wheel_contract", "name": "二輪承攬"}
        with mock.patch.object(repository, "find_active_personnel_by_name_and_phone", return_value=person) as mock_phone:
            with mock.patch.object(repository, "find_personnel_by_name_vendor") as mock_vendor:
                with mock.patch.object(repository, "get_cooperation_type", return_value=coop):
                    result = resolve_vehicle_rider_cooperation_type(vehicle)
        mock_phone.assert_called_once_with("小明", "0912345678")
        mock_vendor.assert_not_called()
        self.assertEqual(result, coop)

    def test_falls_back_to_name_and_vendor_when_no_phone(self):
        vehicle = {"vendor": "shopee", "current_holder": "小明"}
        person = {"id": "p1", "cooperation_type": "two_wheel_employed"}
        coop = {"id": "two_wheel_employed", "name": "二輪雇傭"}
        with mock.patch.object(repository, "find_active_personnel_by_name_and_phone") as mock_phone:
            with mock.patch.object(repository, "find_personnel_by_name_vendor", return_value=person) as mock_vendor:
                with mock.patch.object(repository, "get_cooperation_type", return_value=coop):
                    result = resolve_vehicle_rider_cooperation_type(vehicle)
        mock_phone.assert_not_called()
        mock_vendor.assert_called_once_with("shopee", "小明")
        self.assertEqual(result, coop)

    def test_phone_match_miss_does_not_fall_back_to_vendor_match(self):
        """車輛主檔有填電話，但比對不到人員時，不會再退而用姓名+廠商比對
        （避免同名同姓比對錯人）——只有「完全沒填電話」才會走這個備援。"""
        vehicle = {"vendor": "shopee", "current_holder": "小明", "current_holder_phone": "0912345678"}
        with mock.patch.object(repository, "find_active_personnel_by_name_and_phone", return_value=None):
            with mock.patch.object(repository, "find_personnel_by_name_vendor") as mock_vendor:
                result = resolve_vehicle_rider_cooperation_type(vehicle)
        mock_vendor.assert_not_called()
        self.assertIsNone(result)

    def test_no_matching_person_returns_none(self):
        vehicle = {"vendor": "shopee", "current_holder": "小明"}
        with mock.patch.object(repository, "find_personnel_by_name_vendor", return_value=None):
            result = resolve_vehicle_rider_cooperation_type(vehicle)
        self.assertIsNone(result)

    def test_matched_person_without_cooperation_type_returns_none(self):
        vehicle = {"vendor": "shopee", "current_holder": "小明"}
        person = {"id": "p1", "cooperation_type": ""}
        with mock.patch.object(repository, "find_personnel_by_name_vendor", return_value=person):
            with mock.patch.object(repository, "get_cooperation_type", return_value=None) as mock_get:
                result = resolve_vehicle_rider_cooperation_type(vehicle)
        mock_get.assert_called_once_with("")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
