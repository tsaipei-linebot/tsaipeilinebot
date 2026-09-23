import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from delivery.vehicle_filter_summary import describe_vehicle_filters


class DescribeVehicleFiltersTests(unittest.TestCase):
    """車輛清單「找不到符合的車輛」提示要把使用者剛才搜的條件唸回來，
    他才分得出是自己打錯字還是真的沒有這台車（2026-09-23 新增，原因見
    delivery/vehicle_filter_summary.py 開頭）。"""

    def test_no_filters_gives_empty_list(self):
        self.assertEqual(describe_vehicle_filters(), [])

    def test_blank_and_whitespace_only_filters_are_ignored(self):
        self.assertEqual(describe_vehicle_filters(vehicle_no="   ", vendor=""), [])

    def test_vehicle_no_says_it_is_a_partial_match(self):
        # 車號是「包含」比對（見 repository.vehicle_matches_filters），
        # 訊息要講清楚，不然使用者會以為要打完整車號才找得到
        self.assertEqual(describe_vehicle_filters(vehicle_no="ABC-123"), ["車號包含「ABC-123」"])

    def test_codes_are_shown_as_display_names(self):
        result = describe_vehicle_filters(
            vendor="shopee",
            status="maintenance",
            vendor_map={"shopee": "蝦皮三輪"},
            vehicle_status_map={"maintenance": "維修中"},
        )
        self.assertEqual(result, ["廠商「蝦皮三輪」", "狀態「維修中」"])

    def test_unknown_code_falls_back_to_the_raw_value(self):
        """對照表裡查不到就顯示原始代號——條件確實有套用，寧可顯示得醜一點
        也不要讓使用者以為沒套用到。"""
        self.assertEqual(describe_vehicle_filters(vendor="ghost", vendor_map={}), ["廠商「ghost」"])

    def test_all_filters_keep_a_stable_readable_order(self):
        result = describe_vehicle_filters(
            vehicle_no="ABC",
            vendor="shopee",
            status="available",
            wheel_type="three",
            service_area="area-1",
            cooperation_type="coop-1",
            vendor_map={"shopee": "蝦皮三輪"},
            vehicle_status_map={"available": "可派車"},
            wheel_type_map={"three": "三輪"},
            service_area_map={"area-1": "新北"},
            cooperation_type_map={"coop-1": "承攬"},
        )
        self.assertEqual(
            result,
            [
                "車號包含「ABC」",
                "廠商「蝦皮三輪」",
                "狀態「可派車」",
                "輪別「三輪」",
                "服務區域「新北」",
                "騎手身份「承攬」",
            ],
        )


if __name__ == "__main__":
    unittest.main()
