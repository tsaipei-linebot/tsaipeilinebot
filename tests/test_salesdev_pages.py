"""/salesdev 已登入之後的頁面：用記憶體版 Firestore 實際跑一次畫面渲染與
表單送出（2026-09-24 改版新增）。"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

import main
import platform_accounts
from fastapi.testclient import TestClient
from salesdev import repository
from tests._fake_firestore import FakeFirestore

ADMIN = {"username": "boss", "name": "少凱", "modules": ["salesdev"], "is_platform_admin": True, "rank": ""}
STAFF = {"username": "amy", "name": "Amy", "modules": ["salesdev"], "is_platform_admin": False, "rank": ""}


def _lead(job_id, title, address, company="悅盛人力資源有限公司"):
    return {"source": "chickpt", "job_id": job_id, "job_title": title, "company_name": company,
            "job_url": f"https://www.chickpt.com.tw/job-{job_id}", "work_address": address}


class SalesdevPagesTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        for patcher in (
            mock.patch.object(repository, "get_db", return_value=self.db),
            mock.patch.object(platform_accounts, "current_account", return_value=ADMIN),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        repository.upsert_jobs(
            [
                _lead("A1", "日薪5940先別滑", "台灣桃園市桃園區桃鶯路xx號"),
                _lead("A2", "<b>黃仁勳</b>沒發的", "台灣桃園市桃園區桃鶯路0號"),
                _lead("X1", "人力仲介行政人員", "", company="智邦人力資源管理顧問有限公司"),
            ]
        )
        repository.upsert_factories([{"dedup_key": "tax:1", "name": "新工廠", "address": "台中市"}])
        self.group_id = repository.list_groups()[0]["id"]
        self.client = TestClient(main.app)

    def test_home_lists_one_row_per_location(self):
        resp = self.client.get("/salesdev")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("桃園市桃園區桃鶯路", resp.text)
        self.assertIn("還沒有自動抓取紀錄", resp.text)
        self.assertNotIn("人力仲介行政人員", resp.text)
        self.assertIn("匯入舊試算表資料", resp.text)

    def test_titles_are_html_escaped(self):
        resp = self.client.get(f"/salesdev/groups/{self.group_id}")
        self.assertIn("&lt;b&gt;黃仁勳&lt;/b&gt;", resp.text)

    def test_staff_does_not_see_import_button_and_cannot_import(self):
        with mock.patch.object(platform_accounts, "current_account", return_value=STAFF):
            self.assertNotIn("匯入舊試算表資料", self.client.get("/salesdev").text)
            resp = self.client.post("/salesdev/import-sheet", follow_redirects=False)
        self.assertIn("err=", resp.headers["location"])

    def test_other_tabs_render(self):
        self.assertIn("人力仲介行政人員", self.client.get("/salesdev?tab=internal").text)
        self.assertIn("新工廠", self.client.get("/salesdev?tab=factories").text)

    def test_select_then_filter_by_status(self):
        resp = self.client.post("/salesdev/select", data={"group_ids": [self.group_id]}, follow_redirects=False)
        self.assertIn("msg=", resp.headers["location"])
        self.assertEqual(repository.get_group(self.group_id)["review_status"], repository.STATUS_SELECTED)
        self.assertIn("桃園市桃園區桃鶯路", self.client.get("/salesdev", params={"status": "已勾選待反查"}).text)
        self.assertNotIn("桃園市桃園區桃鶯路</td>", self.client.get("/salesdev", params={"status": "待審查"}).text)

    def test_detail_page_forms(self):
        self.client.post(f"/salesdev/groups/{self.group_id}/review", data={"review_status": "已反查（待人工確認）", "client_company": "某某科技", "client_phone": "03-1234567"})
        self.client.post(f"/salesdev/groups/{self.group_id}/note", data={"note": "這條路上兩家工廠"})
        self.client.post(f"/salesdev/groups/{self.group_id}/contact-log", data={"text": "打給王小姐"})
        page = self.client.get(f"/salesdev/groups/{self.group_id}").text
        for text in ("某某科技", "03-1234567", "這條路上兩家工廠", "打給王小姐", "少凱"):
            self.assertIn(text, page)

    def test_move_job_out_and_back(self):
        resp = self.client.post("/salesdev/jobs/chickpt_A1/internal", data={"value": "1", "back": "group"}, follow_redirects=False)
        self.assertTrue(resp.headers["location"].startswith(f"/salesdev/groups/{self.group_id}"))
        self.assertEqual(repository.get_group(self.group_id)["job_count"], 1)
        self.client.post("/salesdev/jobs/chickpt_A1/internal", data={"value": "0"})
        self.assertEqual(repository.get_group(self.group_id)["job_count"], 2)

    def test_unknown_group_redirects_with_message(self):
        resp = self.client.get("/salesdev/groups/nope", follow_redirects=False)
        self.assertIn("err=", resp.headers["location"])

    def test_export_downloads_xlsx(self):
        import openpyxl

        resp = self.client.get("/salesdev/export.xlsx")
        self.assertEqual(resp.status_code, 200)
        workbook = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertEqual(workbook["全部職缺"].max_row, 4)


if __name__ == "__main__":
    unittest.main()
