import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from platform_templating import taipei_time


class TaipeiTimeFilterTests(unittest.TestCase):
    def test_converts_utc_to_taipei_time(self):
        # UTC 13:20 是台灣時間（UTC+8）21:20，合約產生器等模組存的
        # created_at 都是 datetime.now(timezone.utc)，畫面上要看到的是
        # 這個轉換後的時間，不是原始 UTC 時間。
        value = datetime(2026, 9, 17, 13, 20, 57, tzinfo=timezone.utc)
        self.assertEqual(taipei_time(value), "2026-09-17 21:20")

    def test_naive_datetime_is_treated_as_utc(self):
        # Firestore 讀回來一般都有 tzinfo，但這裡防守一下沒有 tzinfo 的
        # datetime（一律當成 UTC 處理），避免直接呼叫 astimezone() 出錯。
        value = datetime(2026, 9, 17, 13, 20, 57)
        self.assertEqual(taipei_time(value), "2026-09-17 21:20")

    def test_crosses_midnight_into_next_day(self):
        value = datetime(2026, 9, 17, 20, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(taipei_time(value), "2026-09-18 04:00")

    def test_none_returns_empty_string(self):
        self.assertEqual(taipei_time(None), "")

    def test_non_datetime_value_returned_unchanged(self):
        self.assertEqual(taipei_time("已送出"), "已送出")

    def test_custom_format(self):
        value = datetime(2026, 9, 17, 13, 20, 57, tzinfo=timezone.utc)
        self.assertEqual(taipei_time(value, "%Y/%m/%d"), "2026/09/17")


if __name__ == "__main__":
    unittest.main()
