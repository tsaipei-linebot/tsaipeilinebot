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


class ColIndexToLetterTests(unittest.TestCase):
    """2026-09-17 新增：「勾選要反查」功能要把 0-indexed 的欄位位置換成
    Google Sheets 的欄位字母（A1 表示法）才能組出要讀寫的儲存格範圍。"""

    def test_single_letter_columns(self):
        self.assertEqual(svc._col_index_to_letter(0), "A")
        self.assertEqual(svc._col_index_to_letter(25), "Z")

    def test_double_letter_columns(self):
        self.assertEqual(svc._col_index_to_letter(26), "AA")
        self.assertEqual(svc._col_index_to_letter(27), "AB")
        self.assertEqual(svc._col_index_to_letter(51), "AZ")


class MarkRowsSelectedForReverseLookupTests(unittest.TestCase):
    """實際打 Google Sheets API 的路徑（含批次讀取目前狀態、批次寫回）留給
    有 GCP 憑證的環境做整合測試，這裡只測試不需要網路連線就能確定行為的
    部分。"""

    def test_no_row_numbers_returns_zero_without_calling_api(self):
        count, error = svc.mark_rows_selected_for_reverse_lookup("Leads", [])
        self.assertEqual(count, 0)
        self.assertIsNone(error)

    def test_returns_friendly_error_when_sheet_id_not_configured(self):
        original = svc.SALESDEV_SHEET_ID
        svc.SALESDEV_SHEET_ID = ""
        try:
            count, error = svc.mark_rows_selected_for_reverse_lookup("Leads", [2, 3])
        finally:
            svc.SALESDEV_SHEET_ID = original
        self.assertEqual(count, 0)
        self.assertIn("SALESDEV_SHEET_ID", error)


if __name__ == "__main__":
    unittest.main()
