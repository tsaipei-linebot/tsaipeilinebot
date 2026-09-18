import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.vehicle_status_report import (
    EMPTY_REPORT_MESSAGE,
    build_fleet_status_report,
    build_vendor_fleet_report,
)


def _vehicle(status="available", service_area="taipei", vendor="shopee"):
    return {"vendor": vendor, "status": status, "service_area": service_area}


# 2026-09-18 起服務區域改成動態清單（見 repository.py「車輛服務區域管理」
# 那節），build_vendor_fleet_report()／build_fleet_status_report() 這兩個
# 純函式不再自己從 config.py 讀固定清單，改成由呼叫端傳入——這裡固定用一份
# 跟舊版 SERVICE_AREAS 內容相同的清單（id 對應舊代碼），確保這份測試涵蓋的
# 排序/名稱行為不變。
_SERVICE_AREAS = [
    {"id": "taipei", "name": "台北"},
    {"id": "new_taipei", "name": "新北"},
    {"id": "taoyuan", "name": "桃園"},
    {"id": "hsinchu", "name": "新竹"},
    {"id": "taichung", "name": "台中"},
    {"id": "tainan", "name": "台南"},
    {"id": "kaohsiung", "name": "高雄"},
]


class BuildVendorFleetReportTests(unittest.TestCase):
    def test_matches_user_supplied_example_exactly(self):
        # 這個測試直接照使用者提供的範例重建車輛資料，確保輸出文字一字不差
        # 對得上（含符號、欄位順序、有無前導零），這是這支功能最重要的
        # 驗收標準。
        vehicles = []
        regions = {
            "taipei": (4, 30, 3),
            "new_taipei": (8, 9, 1),
            "taoyuan": (3, 13, 2),
            "hsinchu": (1, 1, 0),
            "taichung": (6, 12, 3),
            "tainan": (1, 9, 4),
            "kaohsiung": (4, 11, 1),
        }
        for area_code, (available, in_use, maintenance) in regions.items():
            vehicles += [_vehicle(status="available", service_area=area_code)] * available
            vehicles += [_vehicle(status="in_use", service_area=area_code)] * in_use
            vehicles += [_vehicle(status="maintenance", service_area=area_code)] * maintenance

        report = build_vendor_fleet_report("蝦皮三輪", vehicles, _SERVICE_AREAS, today=date(2026, 9, 14))

        expected = (
            "📊【蝦皮三輪 車輛現況】2026/9/14\n"
            "\n"
            "總車輛數：126\n"
            "使用中：85\n"
            "空車：27\n"
            "待維修：14\n"
            "\n"
            "地區分布：\n"
            "・台北：共37台／空車4／使用中30／待維修3\n"
            "・新北：共18台／空車8／使用中9／待維修1\n"
            "・桃園：共18台／空車3／使用中13／待維修2\n"
            "・新竹：共2台／空車1／使用中1／待維修0\n"
            "・台中：共21台／空車6／使用中12／待維修3\n"
            "・台南：共14台／空車1／使用中9／待維修4\n"
            "・高雄：共16台／空車4／使用中11／待維修1"
        )
        self.assertEqual(report, expected)

    def test_region_with_no_vehicles_is_skipped(self):
        vehicles = [_vehicle(service_area="taipei")]
        report = build_vendor_fleet_report("UD", vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertIn("台北", report)
        self.assertNotIn("新北", report)

    def test_vehicles_without_service_area_go_into_unassigned_bucket(self):
        vehicles = [_vehicle(service_area="taipei"), _vehicle(service_area="")]
        report = build_vendor_fleet_report("UD", vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertIn("未分區：共1台", report)

    def test_unassigned_line_comes_after_named_regions(self):
        vehicles = [_vehicle(service_area=""), _vehicle(service_area="kaohsiung")]
        report = build_vendor_fleet_report("UD", vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertLess(report.index("高雄"), report.index("未分區"))

    def test_date_has_no_leading_zeros(self):
        vehicles = [_vehicle()]
        report = build_vendor_fleet_report("UD", vehicles, _SERVICE_AREAS, today=date(2026, 3, 5))
        self.assertIn("2026/3/5", report)
        self.assertNotIn("2026/03/05", report)


class BuildFleetStatusReportTests(unittest.TestCase):
    def test_no_vehicles_returns_empty_message(self):
        self.assertEqual(build_fleet_status_report([], _SERVICE_AREAS), EMPTY_REPORT_MESSAGE)

    def test_one_block_per_vendor_with_vehicles(self):
        vehicles = [_vehicle(vendor="shopee"), _vehicle(vendor="ud")]
        report = build_fleet_status_report(vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertIn("【蝦皮三輪 車輛現況】", report)
        self.assertIn("【UD 車輛現況】", report)

    def test_vendor_with_no_vehicles_produces_no_block(self):
        vehicles = [_vehicle(vendor="shopee")]
        report = build_fleet_status_report(vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertIn("蝦皮三輪", report)
        self.assertNotIn("【UD 車輛現況】", report)

    def test_blocks_separated_by_blank_line(self):
        vehicles = [_vehicle(vendor="shopee"), _vehicle(vendor="ud")]
        report = build_fleet_status_report(vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertIn("\n\n📊【UD", report)

    def test_vendor_order_follows_vendors_config(self):
        # VENDORS 宣告順序是 shopee, shopee_company_car,
        # shopee_employed_own_car, shopee_contract, ud, uc, sf；報告要照這個
        # 順序排，不是照車輛資料出現的順序。
        vehicles = [_vehicle(vendor="sf"), _vehicle(vendor="shopee")]
        report = build_fleet_status_report(vehicles, _SERVICE_AREAS, today=date(2026, 1, 1))
        self.assertLess(report.index("蝦皮三輪"), report.index("順豐"))


if __name__ == "__main__":
    unittest.main()
