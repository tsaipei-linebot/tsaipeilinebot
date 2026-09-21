import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.rider_csv_import import parse_shift_posting_csv


class ParseShiftPostingCsvTests(unittest.TestCase):
    """批次匯入報班時段的 CSV 解析（2026-09-21 新增），純函式不碰
    Firestore，跟 test_delivery_csv_import.py 的人員匯入測試同一種寫法。"""

    LOCATIONS = {"台北車站": {"lat": 25.0478, "lng": 121.5170}}

    def test_valid_row_with_all_fields(self):
        content = (
            "地點,開始時間,結束時間,需求人數,服務半徑\n"
            "台北車站,2024-01-31 09:00,2024-01-31 18:00,3,8\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["ok"])
        self.assertEqual(row["location_name"], "台北車站")
        self.assertEqual(row["lat"], 25.0478)
        self.assertEqual(row["lng"], 121.5170)
        self.assertEqual(row["capacity"], 3)
        self.assertEqual(row["radius_km"], 8)
        self.assertLess(row["start_at"], row["end_at"])

    def test_missing_radius_defaults_to_config_value(self):
        content = ("地點,開始時間,結束時間,需求人數\n" "台北車站,2024-01-31 09:00,2024-01-31 18:00,3\n").encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        from delivery.config import RIDER_DEFAULT_SEARCH_RADIUS_KM

        self.assertEqual(rows[0]["radius_km"], RIDER_DEFAULT_SEARCH_RADIUS_KM)

    def test_datetime_accepts_t_separator(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n" "台北車站,2024-01-31T09:00,2024-01-31T18:00,3\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])

    def test_missing_required_header_returns_header_error(self):
        content = "地點,開始時間\n台北車站,2024-01-31 09:00\n".encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNotNone(header_error)
        self.assertEqual(rows, [])

    def test_unknown_location_is_reported_as_error(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n" "查無地點,2024-01-31 09:00,2024-01-31 18:00,3\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("查無地點", rows[0]["error"])

    def test_invalid_datetime_is_reported_as_error(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n" "台北車站,不是時間,2024-01-31 18:00,3\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("格式看不懂", rows[0]["error"])

    def test_end_before_start_is_reported_as_error(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n" "台北車站,2024-01-31 18:00,2024-01-31 09:00,3\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("結束時間要晚於開始時間", rows[0]["error"])

    def test_non_positive_capacity_is_reported_as_error(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n" "台北車站,2024-01-31 09:00,2024-01-31 18:00,0\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("需求人數", rows[0]["error"])

    def test_non_positive_radius_is_reported_as_error(self):
        content = (
            "地點,開始時間,結束時間,需求人數,服務半徑\n" "台北車站,2024-01-31 09:00,2024-01-31 18:00,3,0\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("服務半徑", rows[0]["error"])

    def test_blank_row_is_skipped_silently(self):
        content = (
            "地點,開始時間,結束時間,需求人數\n"
            ",,,\n"
            "台北車站,2024-01-31 09:00,2024-01-31 18:00,3\n"
        ).encode("utf-8")
        rows, header_error = parse_shift_posting_csv(content, self.LOCATIONS)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
