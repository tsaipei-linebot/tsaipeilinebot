"""薪資補款搬離 GAS 階段 2 第 1 步：試算表原樣同步進 Firestore＋讀取來源開關（2026-09-25）。

測試資料全部是假的。"""
import os
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
from services import salary_repayment_service as svc
from services import salary_repayment_store as store
from tests._fake_firestore import FakeFirestore

ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}
FINANCE = {"username": "carol", "name": "Carol", "department": "財務部", "is_platform_admin": False, "modules": []}

HEADER = ["補款單號", "申請時間", "申請人姓名", "員工姓名", "實補總額", "審核狀態", "核准主管"]
ORG = [["員工姓名", "員工 LINE ID"], ["李主管", "Uli"]]


def record_values(*rows):
    return [HEADER] + [list(r) for r in rows]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(store, "get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def records(self):
        return self.db.docs(store.RECORDS_COLLECTION)


class SyncTests(StoreTestCase):
    def test_cells_are_copied_as_is(self):
        store.sync_from_sheet(ORG, record_values(["S001", "2026/9/1 上午 10:00:00", "王小明", "陳小華", "1,200 元", "已核准", "Uli"]), ADMIN)
        doc = self.records()["S001"]
        self.assertEqual(doc["fields"]["實補總額"], "1,200 元")  # 不轉數字、不清理
        self.assertEqual(doc["fields"]["申請時間"], "2026/9/1 上午 10:00:00")
        self.assertEqual((doc["row_number"], doc["source"]), (2, "sheet"))

    def test_short_rows_padded_and_blank_rows_skipped(self):
        store.sync_from_sheet(ORG, record_values(["S001", "t"], [], ["", "", ""], ["S002"]), ADMIN)
        self.assertEqual(sorted(self.records()), ["S001", "S002"])
        self.assertEqual(self.records()["S001"]["fields"]["審核狀態"], "")
        self.assertEqual(self.records()["S002"]["row_number"], 5)

    def test_blank_and_duplicate_ids_are_kept_and_reported(self):
        result = store.sync_from_sheet(ORG, record_values(["S001", "a"], ["", "b"], ["S001", "c"], ["A/B", "d"]), ADMIN)
        self.assertEqual(sorted(self.records()), ["S001", "row-3", "row-4", "row-5"])
        self.assertEqual(self.records()["row-4"]["fields"]["申請時間"], "c")
        self.assertEqual(result["blank_id_rows"], [3])
        self.assertEqual(result["duplicate_ids"], [{"id": "S001", "rows": [4]}])
        self.assertEqual(result["records"]["total"], 4)

    def test_resync_counts_and_deletes_rows_removed_from_sheet(self):
        store.sync_from_sheet(ORG, record_values(["S001", "a"], ["S002", "b"], ["S003", "c"]), ADMIN)
        result = store.sync_from_sheet(ORG, record_values(["S001", "a"], ["S003", "改過"], ["S004", "d"]), ADMIN)
        self.assertEqual(
            {k: result["records"][k] for k in ("added", "updated", "unchanged", "deleted")},
            {"added": 1, "updated": 1, "unchanged": 1, "deleted": 1},
        )
        self.assertNotIn("S002", self.records())
        self.assertEqual(self.records()["S003"]["fields"]["申請時間"], "改過")

    def test_platform_created_records_are_never_deleted_by_sync(self):
        self.db.collection(store.RECORDS_COLLECTION).document("P001").set(
            {"fields": {"補款單號": "P001"}, "row_number": 0, "source": "platform"})
        store.sync_from_sheet(ORG, record_values(["S001", "a"]), ADMIN)
        self.assertIn("P001", self.records())

    def test_blank_header_gets_a_name(self):
        store.sync_from_sheet([["員工姓名", "", "員工 LINE ID"], ["李主管", "x", "Uli"]], record_values(["S001"]), ADMIN)
        org = list(self.db.docs(store.ORG_COLLECTION).values())[0]
        self.assertEqual(org["fields"]["（第 2 欄）"], "x")

    def test_state_records_last_sync(self):
        store.sync_from_sheet(ORG, record_values(["S001"]), ADMIN)
        state = store.get_state()
        self.assertEqual(state["last_synced_by"], "老闆")
        self.assertEqual(state["record_headers"], HEADER)
        self.assertEqual(state["last_result"]["org"]["added"], 1)

    def test_many_rows_are_written_in_several_batches(self):
        rows = [[f"S{i:04d}", "t"] for i in range(900)]
        store.sync_from_sheet(ORG, record_values(*rows), ADMIN)
        self.assertEqual(len(self.records()), 900)


class ReadSourceTests(StoreTestCase):
    def test_default_is_sheet(self):
        self.assertEqual(store.read_source(), "sheet")

    def test_switch_and_back(self):
        store.set_read_source("firestore", ADMIN)
        self.assertEqual(store.read_source(), "firestore")
        store.set_read_source("sheet", ADMIN)
        self.assertEqual(store.read_source(), "sheet")

    def test_broken_firestore_falls_back_to_sheet(self):
        with mock.patch.object(store, "get_db", side_effect=RuntimeError("down")):
            self.assertEqual(store.read_source(), "sheet")

    def test_load_rows_keeps_sheet_order(self):
        store.sync_from_sheet(ORG, record_values(["S009", "a"], ["S001", "b"]), ADMIN)
        org_rows, record_rows, error = store.load_rows()
        self.assertIsNone(error)
        self.assertEqual([r["補款單號"] for r in record_rows], ["S009", "S001"])
        self.assertEqual(org_rows, [{"員工姓名": "李主管", "員工 LINE ID": "Uli"}])


class ServiceReadsFromSwitchTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        store.sync_from_sheet(ORG, record_values(
            ["S001", "2026-09-01", "王小明", "陳小華", "100", "已核准", "Uli"],
            ["S002", "2026-09-02", "王小明", "陳小華", "200", "尚未審核", ""],
        ), ADMIN)

    def test_sheet_mode_does_not_touch_firestore_rows(self):
        with mock.patch.object(svc, "_fetch_sheet_rows", return_value=([], [], "讀試算表")) as sheet:
            records, error = svc.get_all_approved_repayment_records()
        sheet.assert_called_once()
        self.assertEqual(error, "讀試算表")

    def test_firestore_mode_gives_same_shape_as_sheet(self):
        store.set_read_source("firestore", ADMIN)
        with mock.patch.object(svc, "_fetch_sheet_rows") as sheet:
            approved, error = svc.get_all_approved_repayment_records()
            with mock.patch.object(platform_accounts, "list_accounts", return_value=[{"username": "w", "name": "王小明"}]):
                mine, _ = svc.get_my_repayment_records("王小明")
        sheet.assert_not_called()
        self.assertIsNone(error)
        self.assertEqual([(r["補款單號"], r["核准主管"]) for r in approved], [("S001", "李主管")])
        self.assertEqual([r["補款單號"] for r in mine], ["S002", "S001"])


class MigrationRouteTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.account = ADMIN
        patcher = mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def test_only_platform_admin(self):
        self.account = FINANCE
        self.assertEqual(self.client.get("/finance/migration", follow_redirects=False).headers["location"], "/finance")
        self.client.post("/finance/migration/source", data={"source": "firestore"}, follow_redirects=False)
        self.assertEqual(store.read_source(), "sheet")

    def test_sync_button(self):
        with mock.patch.object(finance_routes, "fetch_sheet_values", return_value=(ORG, record_values(["S001"], ["", "x"]), None)):
            resp = self.client.post("/finance/migration/sync", follow_redirects=False)
        self.assertEqual(resp.headers["location"], "/finance/migration?notice=synced")
        html = self.client.get("/finance/migration?notice=synced").text
        self.assertIn("已經從試算表同步到平台", html)
        self.assertIn("補款單號空白：試算表第 3 列", html)

    def test_sync_error_is_shown(self):
        with mock.patch.object(finance_routes, "fetch_sheet_values", return_value=([], [], "沒有權限讀取這份 Google Sheet")):
            resp = self.client.post("/finance/migration/sync")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("沒有權限讀取這份 Google Sheet", resp.text)
        self.assertEqual(self.records(), {})

    def test_cannot_switch_to_firestore_before_first_sync(self):
        resp = self.client.post("/finance/migration/source", data={"source": "firestore"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("還沒有從試算表同步過", resp.text)
        self.assertEqual(store.read_source(), "sheet")

    def test_switch_after_sync_and_finance_home_shows_source(self):
        store.sync_from_sheet(ORG, record_values(["S001"]), ADMIN)
        self.client.post("/finance/migration/source", data={"source": "firestore"})
        self.assertEqual(store.read_source(), "firestore")
        self.assertIn("目前資料來源是「平台資料」", self.client.get("/finance").text)
        self.assertIn("平台資料只到最後一次同步的那一刻", self.client.get("/finance/migration").text)

    def test_finance_staff_do_not_see_migration_link(self):
        self.account = FINANCE
        with mock.patch.object(finance_routes, "get_all_approved_repayment_records", return_value=([], None)):
            self.assertNotIn("/finance/migration", self.client.get("/finance").text)


if __name__ == "__main__":
    unittest.main()
