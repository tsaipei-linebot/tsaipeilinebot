import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from services import salesdev_sheet_service as svc


class NormalizeRowTests(unittest.TestCase):
    def test_pads_short_row_with_empty_strings(self):
        self.assertEqual(svc._normalize_row(["a"], 3), ["a", "", ""])

    def test_leaves_full_length_row_unchanged(self):
        self.assertEqual(svc._normalize_row(["a", "b"], 2), ["a", "b"])


class FetchSheetTabsTests(unittest.TestCase):
    """實際打 Google Sheets API 的路徑需要真的 ADC，留給有 GCP 憑證的環境
    做整合測試（跟 factory_watch_service 的既有分工一致）。這裡只測試不需要
    網路連線就能確定行為的部分：試算表 ID 沒設定時要回傳清楚的中文錯誤，
    而不是讓例外炸出去變成 500 錯誤頁。"""

    def test_returns_friendly_error_when_sheet_id_not_configured(self):
        original = svc.SALESDEV_SHEET_ID
        svc.SALESDEV_SHEET_ID = ""
        try:
            tabs, error = svc.fetch_sheet_tabs()
        finally:
            svc.SALESDEV_SHEET_ID = original
        self.assertEqual(tabs, [])
        self.assertIn("SALESDEV_SHEET_ID", error)


if __name__ == "__main__":
    unittest.main()
