"""104 產線徵才公司（2026-09-25 新增）：解析、彙總、員工人數、統一編號、
每週主流程、/salesdev?tab=hiring 畫面。開發環境連不到 104，全部用假資料。"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

from salesdev import hiring_pipeline, repository
from salesdev.scrapers import hiring_104
from salesdev.scrapers.base import DeadlineReached
from tests._fake_firestore import FakeFirestore


def _item(job_id, cust="1a2x6bkd1c", name="德勝科技股份有限公司", industry="其他電子零組件相關業", **extra):
    item = {
        "custName": name,
        "jobName": f"作業員{job_id}",
        "jobNo": "1",
        "link": {"job": f"//www.104.com.tw/job/{job_id}?jobsource=x", "cust": f"//www.104.com.tw/company/{cust}"},
        "jobAddrNoDesc": "桃園市龜山區",
        "coIndustryDesc": industry,
    }
    item.update(extra)
    return item


class _Resp:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    """依網址回傳假資料。search_pages: {(keyword, page): payload}；companies:
    {cust_id: ajax payload 或 Exception}；html: {cust_id: 公司頁 HTML}。
    deadline_after：打到第幾次請求就丟 DeadlineReached。"""

    def __init__(self, search_pages=None, companies=None, html=None, deadline_after=None):
        self.search_pages = search_pages or {}
        self.companies = companies or {}
        self.html = html or {}
        self.deadline_after = deadline_after
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if self.deadline_after is not None and len(self.calls) > self.deadline_after:
            raise DeadlineReached()
        if url == hiring_104.SEARCH_API:
            return _Resp(self.search_pages.get((params["keyword"], params["page"]), {"data": []}))
        cust_id = url.rstrip("/").rsplit("/", 1)[-1]
        if "/ajax/" in url:
            payload = self.companies.get(cust_id, RuntimeError("HTTP 404"))
            if isinstance(payload, Exception):
                raise payload
            return _Resp(payload)
        if cust_id in self.html:
            return _Resp(text=self.html[cust_id])
        raise RuntimeError("HTTP 404")


class ParseItemTests(unittest.TestCase):
    def test_factory_job_is_kept_with_company_page_id(self):
        job, reason = hiring_104.parse_item(_item("7kcx5"))
        self.assertEqual(reason, "")
        self.assertEqual(job["cust_id"], "1a2x6bkd1c")
        self.assertEqual(job["job_id"], "7kcx5")
        self.assertEqual(job["company_url"], "https://www.104.com.tw/company/1a2x6bkd1c")
        self.assertEqual(job["industry"], "其他電子零組件相關業")

    def test_dispatch_and_own_company_are_skipped(self):
        self.assertEqual(hiring_104.parse_item(_item("a", name="天泰人力銀行_鼎利國際事業有限公司"))[1], "dispatch")
        self.assertEqual(hiring_104.parse_item(_item("a", name="材霈有限公司"))[1], "dispatch")

    def test_non_manufacturing_industry_is_skipped_but_missing_industry_is_kept(self):
        self.assertEqual(hiring_104.parse_item(_item("a", industry="餐飲業"))[1], "industry")
        job, reason = hiring_104.parse_item(_item("a", industry=""))
        self.assertEqual(reason, "")
        self.assertEqual(job["industry"], "")

    def test_search_sends_industry_filter(self):
        client = FakeClient()
        hiring_104.collect_hiring_jobs(client, ["作業員"], max_pages=1)
        self.assertEqual(client.calls[0][1]["indcat"], "1001000000,1002000000")
        self.assertNotIn("area", client.calls[0][1])


class CollectTests(unittest.TestCase):
    def test_pages_are_followed_and_duplicates_dropped(self):
        client = FakeClient(
            search_pages={
                ("作業員", 1): {"data": {"list": [_item("j1"), _item("j2", name="天泰人力銀行")], "totalPage": 2}},
                ("作業員", 2): {"data": {"list": [_item("j3", industry="餐飲業")], "totalPage": 2}},
                ("技術員", 1): {"data": [_item("j1")]},
            }
        )
        jobs, stats = hiring_104.collect_hiring_jobs(client, ["作業員", "技術員"], max_pages=3)
        self.assertEqual([j["job_id"] for j in jobs], ["j1"])
        self.assertEqual(stats["raw_items"], 4)
        self.assertEqual(stats["dispatch_skipped"], 1)
        self.assertEqual(stats["industry_skipped"], 1)
        self.assertFalse(stats["deadline_hit"])

    def test_deadline_keeps_what_was_found(self):
        client = FakeClient(
            search_pages={("作業員", 1): {"data": {"list": [_item("j1")], "totalPage": 5}}}, deadline_after=1
        )
        jobs, stats = hiring_104.collect_hiring_jobs(client, ["作業員"], max_pages=5)
        self.assertEqual(len(jobs), 1)
        self.assertTrue(stats["deadline_hit"])


class EmployeeCountTests(unittest.TestCase):
    def test_parse_employee_count(self):
        self.assertEqual(hiring_104.parse_employee_count("250人"), 250)
        self.assertEqual(hiring_104.parse_employee_count("1,200 人"), 1200)
        self.assertEqual(hiring_104.parse_employee_count("500人以上"), 500)
        self.assertEqual(hiring_104.parse_employee_count(320), 320)
        self.assertIsNone(hiring_104.parse_employee_count("暫不提供"))
        self.assertIsNone(hiring_104.parse_employee_count(""))
        self.assertIsNone(hiring_104.parse_employee_count("0人"))

    def test_ajax_json_nested_field(self):
        client = FakeClient(companies={"c1": {"data": {"empNo": "350人", "industryDesc": "半導體製造業"}}})
        info = hiring_104.fetch_company_info(client, "c1")
        self.assertEqual(info["employee_count"], 350)
        self.assertEqual(info["industry"], "半導體製造業")

    def test_falls_back_to_html(self):
        client = FakeClient(html={"c1": "<div>員工人數：1,050 人</div>"})
        self.assertEqual(hiring_104.fetch_company_info(client, "c1")["employee_count"], 1050)

    def test_nothing_found_returns_none(self):
        self.assertIsNone(hiring_104.fetch_company_info(FakeClient(), "c1")["employee_count"])

    def test_deadline_propagates(self):
        with self.assertRaises(DeadlineReached):
            hiring_104.fetch_company_info(FakeClient(deadline_after=0), "c1")


class TaxIdMatchTests(unittest.TestCase):
    def test_names_are_normalized(self):
        self.assertEqual(hiring_pipeline.registered_name("Garmin_台灣國際航電股份有限公司"), "台灣國際航電股份有限公司")
        self.assertEqual(hiring_pipeline.registered_name("臺灣 某某 有限公司"), "台灣某某有限公司")
        self.assertEqual(hiring_pipeline.registry_company_key("德勝科技股份有限公司二廠"), "德勝科技股份有限公司")

    def test_ambiguous_names_are_not_guessed(self):
        records = [
            {"name": "德勝科技股份有限公司", "tax_id": "11111111"},
            {"name": "德勝科技股份有限公司二廠", "tax_id": "11111111"},
            {"name": "同名有限公司", "tax_id": "22222222"},
            {"name": "同名有限公司", "tax_id": "33333333"},
            {"name": "沒統編有限公司", "tax_id": ""},
        ]
        result = hiring_pipeline.match_tax_ids({"德勝科技股份有限公司", "同名有限公司", "沒統編有限公司"}, records)
        self.assertEqual(result, {"德勝科技股份有限公司": "11111111"})


class _FakeDbMixin:
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(repository, "get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)


class RepositoryTests(_FakeDbMixin, unittest.TestCase):
    def _jobs(self):
        return [
            {"cust_id": "c1", "company_name": "德勝", "job_id": "j1", "job_title": "作業員", "area": "桃園市龜山區", "keyword": "作業員", "industry": "電子"},
            {"cust_id": "c1", "company_name": "德勝", "job_id": "j2", "job_title": "作業員", "area": "新北市樹林區", "keyword": "技術員"},
            {"cust_id": "c2", "company_name": "泰藝", "job_id": "j3", "job_title": "品檢員", "area": "新竹縣湖口鄉"},
        ]

    def test_aggregate_counts_jobs_per_company(self):
        companies = {c["cust_id"]: c for c in repository.aggregate_hiring_jobs(self._jobs())}
        self.assertEqual(companies["c1"]["latest_job_count"], 2)
        self.assertEqual(companies["c1"]["latest_job_titles"], ["作業員"])
        self.assertEqual(companies["c1"]["areas"], ["桃園市龜山區", "新北市樹林區"])
        self.assertEqual(companies["c1"]["keywords"], ["作業員", "技術員"])

    def test_upsert_keeps_employee_count_and_tax_id(self):
        repository.upsert_hiring_companies(repository.aggregate_hiring_jobs(self._jobs()), seen_date="2026-09-28")
        repository.update_hiring_company("104_c1", {"employee_count": 300, "employee_checked_at": "2026-09-28", "tax_id": "11111111"})
        stats = repository.upsert_hiring_companies(
            repository.aggregate_hiring_jobs(self._jobs()[:1]), seen_date="2026-10-05"
        )
        self.assertEqual(stats, {"new": 0, "updated": 1})
        doc = self.db.docs(repository.HIRING_COLLECTION)["104_c1"]
        self.assertEqual((doc["employee_count"], doc["tax_id"]), (300, "11111111"))
        self.assertEqual((doc["first_seen"], doc["last_seen"], doc["latest_job_count"]), ("2026-09-28", "2026-10-05", 1))
        self.assertEqual(doc["industry"], "電子")

    def test_employee_retry_stops_after_max_failures(self):
        repository.upsert_hiring_companies(repository.aggregate_hiring_jobs(self._jobs()))
        repository.update_hiring_company("104_c2", {"employee_fail_count": repository.HIRING_EMPLOYEE_MAX_FAILURES})
        self.assertEqual([c["id"] for c in repository.hiring_companies_needing_employee_count()], ["104_c1"])

    def test_settings_defaults_and_save(self):
        self.assertEqual(repository.get_hiring_settings()["keywords"], hiring_104.DEFAULT_KEYWORDS)
        repository.save_hiring_settings(["作業員"], 200, 3, "少凱")
        settings = repository.get_hiring_settings()
        self.assertEqual((settings["keywords"], settings["min_employees"], settings["max_pages"]), (["作業員"], 200, 3))

    def test_size_bucket(self):
        self.assertEqual(repository.hiring_size_bucket({"employee_count": 100}, 100), "big")
        self.assertEqual(repository.hiring_size_bucket({"employee_count": 99}, 100), "small")
        self.assertEqual(repository.hiring_size_bucket({"employee_count": None}, 100), "unknown")


class PipelineTests(_FakeDbMixin, unittest.TestCase):
    def _run(self, client, registry=None, g0v=None):
        registry = registry if registry is not None else [{"name": "德勝科技股份有限公司二廠", "tax_id": "11111111"}]
        fake_factory_watch = mock.Mock()
        fake_factory_watch.iter_registry_records.return_value = iter(registry)
        with mock.patch.dict(sys.modules, {"services.factory_watch_service": fake_factory_watch}), \
                mock.patch("services.company_registry_lookup.lookup_company", side_effect=g0v or (lambda name: None)):
            import services

            with mock.patch.object(services, "factory_watch_service", fake_factory_watch, create=True):
                return hiring_pipeline.run_weekly_hiring_scan(client=client)

    def _search(self):
        return {
            ("作業員", 1): {
                "data": [
                    _item("j1", cust="c1", name="德勝科技股份有限公司"),
                    _item("j2", cust="c2", name="小工廠有限公司"),
                    _item("j3", cust="c3", name="泰藝電子股份有限公司"),
                ]
            }
        }

    def test_end_to_end(self):
        client = FakeClient(
            search_pages=self._search(),
            companies={"c1": {"data": {"empNo": "350人"}}, "c2": {"data": {"empNo": "30人"}}},
        )
        summary = self._run(client, g0v=lambda name: {"name": "泰藝電子股份有限公司", "tax_id": "22222222"} if name.startswith("泰藝") else None)
        docs = self.db.docs(repository.HIRING_COLLECTION)
        self.assertEqual(summary["companies_found"], 3)
        self.assertEqual(summary["new_companies"], 3)
        self.assertEqual((summary["employee_checked"], summary["employee_failed"]), (2, 1))
        self.assertEqual(docs["104_c1"]["employee_count"], 350)
        self.assertEqual(docs["104_c3"]["employee_fail_count"], 1)
        self.assertEqual((docs["104_c1"]["tax_id"], docs["104_c1"]["tax_id_source"]), ("11111111", "登記工廠名錄"))
        self.assertEqual((docs["104_c3"]["tax_id"], docs["104_c3"]["tax_id_source"]), ("22222222", "g0v 公司資料庫"))
        self.assertEqual(docs["104_c2"]["tax_id"], "")
        self.assertEqual(summary["tax_id_matched"], 2)
        self.assertEqual(summary["errors"], [])
        self.assertIsNotNone(repository.latest_hiring_run())

    def test_blocked_104_is_reported(self):
        summary = self._run(FakeClient())
        self.assertEqual(summary["companies_found"], 0)
        self.assertIn("104 沒有回傳任何職缺", summary["errors"][0])

    def test_employee_counts_all_failing_is_reported(self):
        summary = self._run(FakeClient(search_pages=self._search()))
        self.assertEqual(summary["employee_failed"], 3)
        self.assertTrue(any("員工人數 3 間都取不到" in e for e in summary["errors"]))

    def test_deadline_during_employee_lookup_leaves_rest_for_next_run(self):
        client = FakeClient(search_pages=self._search(), companies={"c1": {"data": {"empNo": "350人"}}}, deadline_after=6)
        # 5 個關鍵字各搜 1 次（第 1 頁之後沒有資料），第 6 次請求查 c1 成功，第 7 次時間到
        summary = self._run(client)
        self.assertTrue(summary["deadline_hit"])
        self.assertEqual(summary["employee_checked"], 1)
        self.assertEqual(summary["employee_pending"], 2)

    def test_registry_failure_does_not_stop_the_run(self):
        fake_factory_watch = mock.Mock()
        fake_factory_watch.iter_registry_records.side_effect = RuntimeError("名錄下載失敗")
        import services

        with mock.patch.object(services, "factory_watch_service", fake_factory_watch, create=True), \
                mock.patch.dict(sys.modules, {"services.factory_watch_service": fake_factory_watch}), \
                mock.patch("services.company_registry_lookup.lookup_company", return_value=None):
            summary = hiring_pipeline.run_weekly_hiring_scan(client=FakeClient(search_pages=self._search()))
        self.assertEqual(summary["companies_found"], 3)
        self.assertTrue(any("登記工廠名錄失敗" in e for e in summary["errors"]))


class HiringPagesTests(_FakeDbMixin, unittest.TestCase):
    ADMIN = {"username": "boss", "name": "少凱", "modules": ["salesdev"], "is_platform_admin": True, "rank": ""}
    STAFF = {"username": "amy", "name": "Amy", "modules": ["salesdev"], "is_platform_admin": False, "rank": ""}

    def setUp(self):
        super().setUp()
        import main
        import platform_accounts
        from fastapi.testclient import TestClient

        self.platform_accounts = platform_accounts
        patcher = mock.patch.object(platform_accounts, "current_account", return_value=self.ADMIN)
        patcher.start()
        self.addCleanup(patcher.stop)
        repository.upsert_hiring_companies(
            repository.aggregate_hiring_jobs(
                [
                    {"cust_id": "c1", "company_name": "<b>大廠</b>股份有限公司", "job_id": "j1", "job_title": "作業員", "company_url": "https://www.104.com.tw/company/c1"},
                    {"cust_id": "c2", "company_name": "小廠有限公司", "job_id": "j2", "job_title": "技術員"},
                    {"cust_id": "c3", "company_name": "未知廠有限公司", "job_id": "j3", "job_title": "品檢"},
                ]
            )
        )
        repository.update_hiring_company("104_c1", {"employee_count": 500, "employee_checked_at": "x", "tax_id": "11111111", "tax_id_source": "登記工廠名錄"})
        repository.update_hiring_company("104_c2", {"employee_count": 20, "employee_checked_at": "x"})
        self.client = TestClient(main.app)

    def test_default_shows_big_companies_only(self):
        page = self.client.get("/salesdev?tab=hiring").text
        self.assertIn("&lt;b&gt;大廠&lt;/b&gt;", page)
        self.assertIn("11111111", page)
        self.assertNotIn("小廠有限公司", page)
        self.assertNotIn("未知廠有限公司", page)
        self.assertIn("還沒有每週抓取紀錄", page)
        self.assertIn("儲存搜尋條件", page)

    def test_load_error_still_renders(self):
        with mock.patch.object(repository, "list_hiring_companies", side_effect=RuntimeError("Firestore 掛了")):
            page = self.client.get("/salesdev?tab=hiring").text
        self.assertIn("讀取資料時發生錯誤", page)
        self.assertIn("作業員、技術員、包裝員、倉管、品檢", page)

    def test_size_filters(self):
        self.assertIn("未知廠有限公司", self.client.get("/salesdev?tab=hiring&size=unknown").text)
        self.assertIn("小廠有限公司", self.client.get("/salesdev?tab=hiring&size=small").text)
        page = self.client.get("/salesdev?tab=hiring&size=all").text
        for name in ("大廠", "小廠有限公司", "未知廠有限公司"):
            self.assertIn(name, page)

    def test_settings_saved_by_admin(self):
        resp = self.client.post(
            "/salesdev/hiring/settings",
            data={"keywords": "作業員、技術員, 作業員", "min_employees": "600", "max_pages": "2"},
            follow_redirects=False,
        )
        self.assertIn("msg=", resp.headers["location"])
        settings = repository.get_hiring_settings()
        self.assertEqual((settings["keywords"], settings["min_employees"]), (["作業員", "技術員"], 600))
        self.assertNotIn("大廠", self.client.get("/salesdev?tab=hiring").text)

    def test_settings_validation(self):
        for data in (
            {"keywords": "", "min_employees": "100", "max_pages": "5"},
            {"keywords": "作業員", "min_employees": "abc", "max_pages": "5"},
            {"keywords": "作業員", "min_employees": "100", "max_pages": "50"},
        ):
            resp = self.client.post("/salesdev/hiring/settings", data=data, follow_redirects=False)
            self.assertIn("err=", resp.headers["location"])
        self.assertEqual(repository.get_hiring_settings()["min_employees"], 100)

    def test_staff_cannot_change_settings(self):
        with mock.patch.object(self.platform_accounts, "current_account", return_value=self.STAFF):
            self.assertNotIn("儲存搜尋條件", self.client.get("/salesdev?tab=hiring").text)
            resp = self.client.post(
                "/salesdev/hiring/settings", data={"keywords": "x", "min_employees": "1", "max_pages": "1"}, follow_redirects=False
            )
        self.assertIn("err=", resp.headers["location"])
        self.assertEqual(repository.get_hiring_settings()["keywords"], hiring_104.DEFAULT_KEYWORDS)

    def test_export_has_hiring_sheet(self):
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(self.client.get("/salesdev/export.xlsx").content))
        self.assertEqual(workbook["104產線徵才公司"].max_row, 4)

    def test_trigger_endpoint_requires_secret(self):
        self.assertEqual(self.client.post("/internal/salesdev/hiring/run").status_code, 403)
        self.assertEqual(
            self.client.post("/internal/salesdev/hiring/run", headers={"X-Salesdev-Scrape-Secret": "wrong"}).status_code, 403
        )


if __name__ == "__main__":
    unittest.main()
