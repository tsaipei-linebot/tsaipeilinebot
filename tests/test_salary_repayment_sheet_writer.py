"""薪資補款搬離 GAS 階段 2 第 3 步：平台寫回「薪資補款紀錄」試算表（2026-09-25）。測試資料全部是假的。"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from fastapi.testclient import TestClient

import finance_routes
import main
import platform_accounts
from services import salary_repayment_sheet_writer as writer
from services import salary_repayment_store as store
from tests._fake_firestore import FakeFirestore

HEADER = ["補款單號", "申請時間", "申請人姓名", "申請人 LINE ID", "實補總額", "備註", "審核狀態", "核准主管", "核准時間", "補款佐證(照片)"]
ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class FakeSheets:
    """記憶體版試算表：grid[0] 是第 1 列。只做這支程式用到的 API。"""

    def __init__(self, rows, title="薪資補款紀錄", gid=123, error=None):
        self.grid = [list(r) for r in rows]
        self.title, self.gid, self.error = title, gid, error
        self.writes = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def _check(self):
        if self.error:
            raise self.error

    def _parse(self, a1):
        match = re.match(r"'(.+)'!(.+)", a1)
        assert match and match.group(1) == self.title, a1
        return match.group(2)

    def get(self, spreadsheetId=None, range=None, fields=None):
        if range is None:  # spreadsheets().get()
            return _Call(lambda: {"sheets": [{"properties": {"title": self.title, "sheetId": self.gid}}]})
        cells = self._parse(range)

        def run():
            self._check()
            if cells == "1:1":
                return {"values": self.grid[:1]}
            if cells == "A:A":
                return {"values": [[r[0]] if r else [] for r in self.grid]}
            raise AssertionError(cells)
        return _Call(run)

    def _put(self, a1, values):
        match = re.match(r"([A-Z]+)(\d+)", self._parse(a1).split(":")[0])
        col = ord(match.group(1)) - 65
        row = int(match.group(2)) - 1
        for r, line in enumerate(values):
            while len(self.grid) <= row + r:
                self.grid.append([])
            target = self.grid[row + r]
            for c, value in enumerate(line):
                while len(target) <= col + c:
                    target.append("")
                target[col + c] = value

    def update(self, spreadsheetId, range, valueInputOption, body):
        def run():
            self._check()
            self.writes.append(("update", range, valueInputOption))
            self._put(range, body["values"])
            return {}
        return _Call(run)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        def run():
            self._check()
            self.writes.append(("append", range, valueInputOption))
            self.grid.extend(list(r) for r in body["values"])
            return {}
        return _Call(run)

    def batchUpdate(self, spreadsheetId, body):
        def run():
            self._check()
            if "requests" in body:
                rng = body["requests"][0]["deleteDimension"]["range"]
                assert rng["sheetId"] == self.gid
                del self.grid[rng["startIndex"]:rng["endIndex"]]
            else:
                self.writes.append(("batch", body["valueInputOption"]))
                for item in body["data"]:
                    self._put(item["range"], item["values"])
            return {}
        return _Call(run)


def sheet(*rows):
    return FakeSheets([HEADER] + [list(r) for r in rows])


class WriterTestCase(unittest.TestCase):
    def use(self, fake):
        patcher = mock.patch.object(writer, "_get_sheets_service", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class CheckWriteAccessTests(WriterTestCase):
    def test_writes_header_back_unchanged(self):
        fake = self.use(sheet(["S001", "2026-09-01"]))
        before = [list(r) for r in fake.grid]
        ok, message = writer.check_write_access()
        self.assertTrue(ok)
        self.assertIn("10 欄", message)
        self.assertEqual(fake.grid, before)
        self.assertEqual(fake.writes, [("update", "'薪資補款紀錄'!A1:J1", "RAW")])

    def test_no_permission_gives_plain_instruction(self):
        self.use(FakeSheets([HEADER], error=_HttpError(403)))
        ok, message = writer.check_write_access()
        self.assertFalse(ok)
        self.assertIn("編輯者", message)

    def test_empty_sheet(self):
        self.use(FakeSheets([]))
        self.assertFalse(writer.check_write_access()[0])


class AppendTests(WriterTestCase):
    def test_values_follow_header_positions(self):
        fake = self.use(sheet(["S001"]))
        ok, _ = writer.append_record({"審核狀態": "待審核", "補款單號": "S002", "實補總額": 1200, "申請人姓名": "王小明"})
        self.assertTrue(ok)
        self.assertEqual(fake.grid[-1], ["S002", "", "王小明", "", 1200, "", "待審核", "", "", ""])
        self.assertEqual(fake.writes[-1], ("append", "'薪資補款紀錄'!A1", "USER_ENTERED"))

    def test_unknown_fields_are_reported_not_written(self):
        fake = self.use(sheet())
        ok, message = writer.append_record({"補款單號": "S002", "不存在的欄": "x"})
        self.assertTrue(ok)
        self.assertIn("不存在的欄", message)
        self.assertEqual(len(fake.grid[-1]), len(HEADER))


class UpdateReviewTests(WriterTestCase):
    def test_approve_updates_three_cells_only(self):
        fake = self.use(sheet(["S001", "t1", "王小明", "U1", "100", "備", "待審核", "", "", ""],
                              ["S002", "t2", "陳小華", "U2", "200", "備", "待審核", "", "", ""]))
        ok, _ = writer.update_review("S002", "已核准", "U-manager", "2026-09-25 10:00:00")
        self.assertTrue(ok)
        self.assertEqual(fake.grid[2], ["S002", "t2", "陳小華", "U2", "200", "備", "已核准", "U-manager", "2026-09-25 10:00:00", ""])
        self.assertEqual(fake.grid[1][6], "待審核")

    def test_reject_deletes_the_row_like_gas(self):
        fake = self.use(sheet(["S001"], ["S002"], ["S003"]))
        ok, _ = writer.update_review("S002", "已退回")
        self.assertTrue(ok)
        self.assertEqual([r[0] for r in fake.grid], ["補款單號", "S001", "S003"])

    def test_unknown_id(self):
        self.use(sheet(["S001"]))
        ok, message = writer.update_review("S999", "已核准")
        self.assertFalse(ok)
        self.assertIn("S999", message)

    def test_header_row_is_never_matched(self):
        fake = self.use(sheet(["S001"]))
        self.assertFalse(writer.update_review("補款單號", "已退回")[0])
        self.assertEqual(len(fake.grid), 2)

    def test_errors_do_not_raise(self):
        self.use(FakeSheets([HEADER, ["S001"]], error=RuntimeError("網路斷了")))
        ok, message = writer.update_review("S001", "已核准")
        self.assertFalse(ok)
        self.assertIn("網路斷了", message)


class CheckWriteRouteTests(WriterTestCase):
    def setUp(self):
        db = FakeFirestore()
        for p in (
            mock.patch.object(store, "get_db", return_value=db),
            mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.account = ADMIN
        self.client = TestClient(main.app)

    def test_success_popup(self):
        self.use(sheet(["S001"]))
        resp = self.client.post("/finance/migration/check-write")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('class="success js-flash">平台可以寫入「薪資補款紀錄」分頁', resp.text)

    def test_failure_popup(self):
        self.use(FakeSheets([HEADER], error=_HttpError(403)))
        resp = self.client.post("/finance/migration/check-write")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("編輯者", resp.text)

    def test_admin_only(self):
        self.account = {**ADMIN, "is_platform_admin": False, "department": "財務部"}
        fake = self.use(sheet())
        self.client.post("/finance/migration/check-write", follow_redirects=False)
        self.assertEqual(fake.writes, [])


if __name__ == "__main__":
    unittest.main()
