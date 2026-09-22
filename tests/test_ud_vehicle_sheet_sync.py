import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import ud_vehicle_sheet_sync as sync


class ColIndexToLetterTests(unittest.TestCase):
    def test_basic_conversions(self):
        self.assertEqual(sync._col_index_to_letter(0), "A")
        self.assertEqual(sync._col_index_to_letter(25), "Z")
        self.assertEqual(sync._col_index_to_letter(26), "AA")


class LocateNextRowTests(unittest.TestCase):
    """純函式邏輯：在表格資料裡找表頭列跟下一個空白列（見
    delivery/ud_vehicle_sheet_sync.py 的說明，這個分頁裡堆疊了好幾張表，
    要靠「掃到空白列」當作這張表的邊界，不能一路掃到底）。"""

    HEADER_ROW = ["地區", "車號", "外送員", "手機號碼", "站所", "目前使用狀況", "停車地點", "給車", "還車", "備註"]

    def test_no_header_row_returns_none(self):
        rows = [["不相干的表格"], ["a", "b"]]
        header_col, next_row = sync._locate_next_row(rows)
        self.assertIsNone(header_col)
        self.assertIsNone(next_row)

    def test_finds_header_and_first_blank_row_immediately_after(self):
        rows = [self.HEADER_ROW]
        header_col, next_row = sync._locate_next_row(rows)
        self.assertEqual(header_col["車號"], 1)
        self.assertEqual(header_col["外送員"], 2)
        # rows 只有 1 筆（表頭本身，index 0 → 試算表第 1 列），下一筆資料
        # 應該寫在試算表第 2 列。
        self.assertEqual(next_row, 2)

    def test_finds_next_row_after_existing_data(self):
        rows = [
            self.HEADER_ROW,
            ["台北", "ERV-1", "王小明", "0912345678", "NS2", "使用中", "信義區", "2026-09-20", "", ""],
            ["台北", "ERV-2", "李小華", "0987654321", "NS3", "待領用", "板橋區", "", "2026-09-21", ""],
        ]
        header_col, next_row = sync._locate_next_row(rows)
        # index 0 是表頭（第 1 列），index 1/2 是資料（第 2/3 列），下一筆
        # 應該寫在第 4 列。
        self.assertEqual(next_row, 4)

    def test_stops_at_blank_row_and_does_not_scan_into_next_table(self):
        rows = [
            self.HEADER_ROW,
            ["台北", "ERV-1", "王小明", "0912345678", "NS2", "使用中", "信義區", "2026-09-20", "", ""],
            [],
            ["這是另一張不相干的表格", "車輛編號", "文件審核狀態"],
        ]
        header_col, next_row = sync._locate_next_row(rows)
        # 第 2 列是資料、第 3 列整列空白，下一筆應該寫在第 3 列（空白列
        # 本身），不會被下面第 4 列另一張表的內容影響。
        self.assertEqual(next_row, 3)

    def test_missing_vehicle_no_column_is_not_treated_as_header(self):
        rows = [["外送員", "手機號碼"], self.HEADER_ROW]
        header_col, next_row = sync._locate_next_row(rows)
        # 第一列雖然有「外送員」，但沒有「車號」，不能當表頭；應該繼續找到
        # 第二列才是真正的表頭（第 2 列，index 1）。
        self.assertEqual(next_row, 3)


class SyncVehicleEventTests(unittest.TestCase):
    HEADER_ROW = ["地區", "車號", "外送員", "手機號碼", "站所", "目前使用狀況", "停車地點", "給車", "還車", "備註"]

    def _base_kwargs(self, **overrides):
        kwargs = dict(
            service_area_name="台北",
            vehicle_no="ERV-1",
            personnel_name="王小明",
            phone="0912345678",
            site="NS2",
            status_name="使用中",
            location="台北市信義區",
            event_type="checkout",
            event_date="2026-09-22",
            note="備註內容",
        )
        kwargs.update(overrides)
        return kwargs

    def test_returns_false_without_calling_api_when_sheet_id_not_configured(self):
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", ""):
            with mock.patch.object(sync, "_get_sheets_service") as mock_get_service:
                result = sync.sync_vehicle_event(**self._base_kwargs())
        self.assertFalse(result)
        mock_get_service.assert_not_called()

    def test_returns_false_when_gid_not_found(self):
        fake_service = mock.Mock()
        fake_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"sheetId": 999, "title": "別的分頁"}}]
        }
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", "sheet123"):
            with mock.patch.object(sync, "_get_sheets_service", return_value=fake_service):
                result = sync.sync_vehicle_event(**self._base_kwargs())
        self.assertFalse(result)

    def test_returns_false_when_header_row_not_found(self):
        fake_service = mock.Mock()
        fake_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"sheetId": sync.UD_VEHICLE_SHEET_GID, "title": "三輪車(同步材霈)"}}]
        }
        fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [["不相干的內容"]]
        }
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", "sheet123"):
            with mock.patch.object(sync, "_get_sheets_service", return_value=fake_service):
                result = sync.sync_vehicle_event(**self._base_kwargs())
        self.assertFalse(result)

    def test_writes_expected_columns_on_success(self):
        fake_service = mock.Mock()
        fake_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"sheetId": sync.UD_VEHICLE_SHEET_GID, "title": "三輪車(同步材霈)"}}]
        }
        fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [self.HEADER_ROW]
        }
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", "sheet123"):
            with mock.patch.object(sync, "_get_sheets_service", return_value=fake_service):
                result = sync.sync_vehicle_event(**self._base_kwargs(event_type="checkout"))
        self.assertTrue(result)

        batch_update = fake_service.spreadsheets.return_value.values.return_value.batchUpdate
        batch_update.assert_called_once()
        body = batch_update.call_args.kwargs["body"]
        written = {entry["range"]: entry["values"][0][0] for entry in body["data"]}
        self.assertEqual(written["'三輪車(同步材霈)'!A2"], "台北")
        self.assertEqual(written["'三輪車(同步材霈)'!B2"], "ERV-1")
        self.assertEqual(written["'三輪車(同步材霈)'!C2"], "王小明")
        self.assertEqual(written["'三輪車(同步材霈)'!D2"], "0912345678")
        self.assertEqual(written["'三輪車(同步材霈)'!E2"], "NS2")
        self.assertEqual(written["'三輪車(同步材霈)'!F2"], "使用中")
        self.assertEqual(written["'三輪車(同步材霈)'!G2"], "台北市信義區")
        # 領車事件：給車欄位有值、還車欄位是空字串。
        self.assertEqual(written["'三輪車(同步材霈)'!H2"], "2026-09-22")
        self.assertEqual(written["'三輪車(同步材霈)'!I2"], "")
        self.assertEqual(written["'三輪車(同步材霈)'!J2"], "備註內容")

    def test_return_event_only_fills_return_column(self):
        fake_service = mock.Mock()
        fake_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"sheetId": sync.UD_VEHICLE_SHEET_GID, "title": "三輪車(同步材霈)"}}]
        }
        fake_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [self.HEADER_ROW]
        }
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", "sheet123"):
            with mock.patch.object(sync, "_get_sheets_service", return_value=fake_service):
                sync.sync_vehicle_event(**self._base_kwargs(event_type="return"))

        batch_update = fake_service.spreadsheets.return_value.values.return_value.batchUpdate
        body = batch_update.call_args.kwargs["body"]
        written = {entry["range"]: entry["values"][0][0] for entry in body["data"]}
        self.assertEqual(written["'三輪車(同步材霈)'!H2"], "")
        self.assertEqual(written["'三輪車(同步材霈)'!I2"], "2026-09-22")

    def test_exceptions_are_swallowed_and_return_false(self):
        with mock.patch.object(sync, "UD_VEHICLE_SHEET_ID", "sheet123"):
            with mock.patch.object(sync, "_get_sheets_service", side_effect=RuntimeError("network down")):
                result = sync.sync_vehicle_event(**self._base_kwargs())
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
