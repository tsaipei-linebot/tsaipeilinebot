"""台灣就業通（2026-09-26 新增）：API/職缺頁解析、Firestore 合併、每小時主流程、
/salesdev?tab=taiwanjobs 畫面。開發環境連不到台灣就業通，全部用假資料；XML 的標籤
寫法（`<COMPNAME（公司名稱）>`＋CDATA）照使用者實際跑成功的腳本。"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp

_stub_gcp.install()

from salesdev import repository
from salesdev import taiwanjobs_pipeline
from salesdev import taiwanjobs_repository as tj_repo
from salesdev.scrapers import taiwanjobs
from salesdev.scrapers.base import DeadlineReached
from tests._fake_firestore import FakeFirestore


def _record(company, title, employer, hire, city="桃園市龜山區"):
    return (
        "<Data>"
        f"<OCCU_DESC（職務名稱）><![CDATA[{title}]]></OCCU_DESC（職務名稱）>"
        f"<COMPNAME（公司名稱）><![CDATA[{company}]]></COMPNAME（公司名稱）>"
        f"<URL_QUERY（職缺資料URL）>http://job.taiwanjobs.gov.tw/Internet/jobwanted/JobDetail.aspx?EMPLOYER_ID={employer}&amp;HIRE_ID={hire}</URL_QUERY（職缺資料URL）>"
        "<JOB_PERSON（雇用人數）>3</JOB_PERSON（雇用人數）>"
        "<NT_L（薪資範圍下限）>28590</NT_L（薪資範圍下限）><NT_U（薪資範圍上限）>35000</NT_U（薪資範圍上限）>"
        f"<CITYNAME（工作地點）>{city}</CITYNAME（工作地點）>"
        "</Data>"
    )


def _xml(*records):
    return "<?xml version=\"1.0\"?>\r\n<Root>" + "".join(records) + "</Root>"


def _detail(email="hr@example.com.tw", name="王小姐", phone="03-3281234#12"):
    return f"""<html><head><script>var x='a@b.js';</script><style>.c{{}}</style></head><body>
    <div>聯絡人員：{name}</div><div>電話：{phone}</div>
    <div>電子信箱：<a href="mailto:{email}">{email}</a></div>
    <div>應徵地址：桃園市龜山區華亞二路100號</div>
    <footer>客服信箱 service@taiwanjobs.gov.tw 圖示 logo@2x.png</footer></body></html>"""


class _Resp:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"


class FakeClient:
    """api: {zipno: xml 文字}；pages: {職缺網址片段 HIRE_ID: html 或 Exception}。
    deadline_after：打到第幾次就丟 DeadlineReached。"""

    def __init__(self, api=None, pages=None, deadline_after=None):
        self.api = api or {}
        self.pages = pages or {}
        self.deadline_after = deadline_after
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if self.deadline_after is not None and len(self.calls) > self.deadline_after:
            raise DeadlineReached()
        if url == taiwanjobs.API_URL:
            return _Resp(self.api.get(params.get("zipno", ""), _xml()))
        hire = url.rsplit("HIRE_ID=", 1)[-1]
        page = self.pages.get(hire, RuntimeError("HTTP 404"))
        if isinstance(page, Exception):
            raise page
        return _Resp(page)


class ParseTests(unittest.TestCase):
    def test_api_records(self):
        jobs = taiwanjobs.parse_api_records(_xml(_record("德勝科技股份有限公司", "產線作業員 &amp; 包裝", "111", "222")))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["company_name"], "德勝科技股份有限公司")
        self.assertEqual(job["job_title"], "產線作業員 & 包裝")
        self.assertTrue(job["job_url"].startswith("https://"))
        self.assertEqual((job["employer_id"], job["hire_id"]), ("111", "222"))
        self.assertEqual((job["headcount"], job["salary_low"], job["city"]), ("3", "28590", "桃園市龜山區"))

    def test_records_without_company_or_url_are_skipped(self):
        xml = _xml("<Data><OCCU_DESC（職務名稱）>作業員</OCCU_DESC（職務名稱）></Data>")
        self.assertEqual(taiwanjobs.parse_api_records(xml), [])

    def test_title_matching(self):
        self.assertEqual(taiwanjobs.title_matches("SMT 技術員(夜班)", ["作業員", "技術員"]), "技術員")
        self.assertEqual(taiwanjobs.title_matches("smt操作", ["SMT"]), "SMT")
        self.assertEqual(taiwanjobs.title_matches("門市人員", ["作業員"]), "")

    def test_contact_page(self):
        contact = taiwanjobs.parse_contact(_detail())
        self.assertEqual(contact["emails"], ["hr@example.com.tw"])
        self.assertEqual(contact["contact_name"], "王小姐")
        self.assertEqual(contact["contact_phone"], "03-3281234#12")
        self.assertEqual(contact["job_address"], "桃園市龜山區華亞二路100號")

    def test_all_emails_kept_and_deduped(self):
        html = _detail(email="Amy@Gmail.com") + "<p>或寄 hr@factory.com.tw、amy@gmail.com。</p>"
        self.assertEqual(taiwanjobs.parse_contact(html)["emails"], ["Amy@Gmail.com", "hr@factory.com.tw"])

    def test_page_without_contact(self):
        contact = taiwanjobs.parse_contact("<html><body>聯絡人員：無 電話：0800-777-888</body></html>")
        self.assertEqual((contact["emails"], contact["contact_name"], contact["contact_phone"]), ([], "", ""))


class _FakeDbMixin:
    def setUp(self):
        self.db = FakeFirestore()
        patcher = mock.patch.object(repository, "get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)


class RepositoryTests(_FakeDbMixin, unittest.TestCase):
    def _jobs(self):
        return [
            {"company_name": "德勝科技股份有限公司", "job_title": "作業員", "job_url": "u1", "employer_id": "111", "hire_id": "1", "city": "桃園市"},
            {"company_name": "德勝科技股份有限公司", "job_title": "技術員", "job_url": "u2", "employer_id": "111", "hire_id": "2", "city": "新北市"},
            {"company_name": "天泰人力資源管理顧問有限公司", "job_title": "作業員", "job_url": "u3", "employer_id": "999", "hire_id": "3"},
        ]

    def test_listing_creates_jobs_and_companies(self):
        stats = tj_repo.upsert_listing(self._jobs(), seen_date="2026-09-26")
        self.assertEqual(stats, {"new_jobs": 3, "new_companies": 2})
        companies = self.db.docs(tj_repo.COMPANIES_COLLECTION)
        self.assertEqual(companies["111"]["job_count"], 2)
        self.assertEqual(companies["111"]["latest_job_titles"], ["作業員", "技術員"])
        self.assertTrue(companies["999"]["is_dispatch"])
        self.assertEqual(len(tj_repo.pending_detail_jobs()), 3)
        again = tj_repo.upsert_listing(self._jobs(), seen_date="2026-09-27")
        self.assertEqual(again, {"new_jobs": 0, "new_companies": 0})
        self.assertEqual(companies["111"]["first_seen"], "2026-09-26")

    def test_contacts_are_merged_per_company(self):
        tj_repo.upsert_listing(self._jobs())
        jobs = {j["hire_id"]: j for j in tj_repo.pending_detail_jobs()}
        tj_repo.save_job_contact(jobs["1"], {"emails": ["hr@ds.com.tw"], "contact_name": "王小姐", "contact_phone": "03-1"})
        tj_repo.save_job_contact(jobs["2"], {"emails": ["hr@ds.com.tw", "amy@gmail.com"], "contact_name": "李先生"})
        company = self.db.docs(tj_repo.COMPANIES_COLLECTION)["111"]
        self.assertEqual(company["emails"], ["hr@ds.com.tw", "amy@gmail.com"])
        self.assertEqual(company["contact_names"], ["王小姐", "李先生"])
        self.assertEqual(tj_repo.email_source(company, "amy@gmail.com"), "u2")
        self.assertEqual(tj_repo.count_jobs_by_status(), {"pending": 1, "done": 2, "failed": 0})
        # 已經讀過的，重抓清單也不會變回待讀
        tj_repo.upsert_listing(self._jobs())
        self.assertEqual(len(tj_repo.pending_detail_jobs()), 1)

    def test_failures_stop_after_limit(self):
        tj_repo.upsert_listing(self._jobs()[:1])
        for _ in range(tj_repo.DETAIL_MAX_FAILURES):
            tj_repo.mark_job_failed(tj_repo.pending_detail_jobs()[0], "HTTP 500")
        self.assertEqual(tj_repo.pending_detail_jobs(), [])
        self.assertEqual(tj_repo.count_jobs_by_status()["failed"], 1)

    def test_settings(self):
        self.assertEqual(tj_repo.get_settings()["zipcodes"], taiwanjobs.DEFAULT_ZIPCODES)
        tj_repo.save_settings(["作業員"], ["330"], "少凱")
        settings = tj_repo.get_settings()
        self.assertEqual((settings["keywords"], settings["zipcodes"]), (["作業員"], ["330"]))


class PipelineTests(_FakeDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        tj_repo.save_settings(["作業員", "技術員"], ["330", "333"], "少凱")

    def _api(self):
        return {
            "330": _xml(
                _record("德勝科技股份有限公司", "產線作業員", "111", "1"),
                _record("德勝科技股份有限公司", "門市人員", "111", "9"),
                _record("泰藝電子股份有限公司", "SMT技術員", "222", "2"),
            ),
            "333": _xml(_record("德勝科技股份有限公司", "技術員", "111", "3")),
        }

    def test_first_run_lists_then_reads_pages(self):
        client = FakeClient(
            api=self._api(),
            pages={"1": _detail("hr@ds.com.tw"), "2": _detail("amy@gmail.com", "陳小姐"), "3": "<html>沒有聯絡資料</html>"},
        )
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=client)
        self.assertTrue(summary["listing_done"])
        self.assertEqual((summary["api_jobs"], summary["matched_jobs"], summary["new_companies"]), (4, 3, 2))
        self.assertEqual((summary["pages_read"], summary["pages_with_email"]), (3, 2))
        self.assertEqual(summary["errors"], [])
        companies = {c["id"]: c for c in tj_repo.list_companies()}
        self.assertEqual(companies["111"]["emails"], ["hr@ds.com.tw"])
        self.assertEqual(companies["222"]["emails"], ["amy@gmail.com"])
        self.assertIsNotNone(tj_repo.latest_run())

        # 下一次（清單還不用重抓）只讀待讀的，這次沒有
        client2 = FakeClient(api=self._api())
        summary2 = taiwanjobs_pipeline.run_taiwanjobs(client=client2)
        self.assertFalse(summary2["listing_refreshed"])
        self.assertEqual(client2.calls, [])

    def test_listing_resumes_after_deadline(self):
        # 第 1 次請求（330）成功，第 2 次（333）時間到
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=FakeClient(api=self._api(), deadline_after=1))
        self.assertTrue(summary["deadline_hit"])
        self.assertFalse(summary["listing_done"])
        self.assertEqual(tj_repo.get_settings()["listing_cursor"], 1)
        self.assertEqual(summary["pages_pending"], 2)

        client = FakeClient(api=self._api(), pages={"1": _detail(), "2": _detail(), "3": _detail()})
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=client)
        self.assertEqual(summary["listing_from"], 1)
        self.assertTrue(summary["listing_done"])
        self.assertEqual(client.calls[0][1]["zipno"], "333")
        self.assertEqual(summary["pages_read"], 3)
        self.assertNotIn("listing_cursor", tj_repo.get_settings())

    def test_failed_page_is_retried_later(self):
        client = FakeClient(api=self._api(), pages={"1": _detail(), "2": RuntimeError("HTTP 500"), "3": _detail()})
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=client)
        self.assertEqual((summary["pages_read"], summary["pages_failed"]), (2, 1))
        self.assertEqual(len(tj_repo.pending_detail_jobs()), 1)

    def test_empty_api_is_reported(self):
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=FakeClient())
        self.assertTrue(any("職缺 API 沒有回傳任何職缺" in e for e in summary["errors"]))

    def test_refresh_request_forces_listing(self):
        taiwanjobs_pipeline.run_taiwanjobs(client=FakeClient(api=self._api(), pages={"1": _detail(), "2": _detail(), "3": _detail()}))
        tj_repo.request_listing_refresh()
        summary = taiwanjobs_pipeline.run_taiwanjobs(client=FakeClient(api=self._api()))
        self.assertTrue(summary["listing_refreshed"])


class PagesTests(_FakeDbMixin, unittest.TestCase):
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
        tj_repo.upsert_listing(
            [
                {"company_name": "<b>有信箱</b>工業股份有限公司", "job_title": "作業員", "job_url": "https://x/JobDetail.aspx?EMPLOYER_ID=1&HIRE_ID=1", "employer_id": "1", "hire_id": "1"},
                {"company_name": "沒信箱有限公司", "job_title": "技術員", "job_url": "u2", "employer_id": "2", "hire_id": "2"},
                {"company_name": "天泰人力資源管理顧問有限公司", "job_title": "作業員", "job_url": "u3", "employer_id": "3", "hire_id": "3"},
            ]
        )
        jobs = {j["hire_id"]: j for j in tj_repo.pending_detail_jobs()}
        tj_repo.save_job_contact(jobs["1"], {"emails": ["hr@abc.com.tw"], "contact_name": "王小姐"})
        tj_repo.save_job_contact(jobs["3"], {"emails": ["job@tt.com.tw"]})
        self.client = TestClient(main.app)

    def test_default_shows_companies_with_email(self):
        page = self.client.get("/salesdev?tab=taiwanjobs").text
        self.assertIn("&lt;b&gt;有信箱&lt;/b&gt;", page)
        self.assertIn("hr@abc.com.tw", page)
        self.assertIn('href="https://x/JobDetail.aspx?EMPLOYER_ID=1&amp;HIRE_ID=1"', page)
        self.assertIn("天泰人力資源管理顧問有限公司", page)
        self.assertNotIn("沒信箱有限公司", page)
        self.assertIn("還沒有執行紀錄", page)

    def test_filters(self):
        self.assertIn("沒信箱有限公司", self.client.get("/salesdev?tab=taiwanjobs&email=no").text)
        page = self.client.get("/salesdev?tab=taiwanjobs&email=all&dispatch=hide").text
        self.assertIn("沒信箱有限公司", page)
        self.assertNotIn("天泰人力資源管理顧問有限公司", page)

    def test_settings_and_refresh_by_admin(self):
        resp = self.client.post(
            "/salesdev/taiwanjobs/settings",
            data={"keywords": "作業員、SMT", "zipcodes": "330, 333 abc 12 330"},
            follow_redirects=False,
        )
        self.assertIn("msg=", resp.headers["location"])
        settings = tj_repo.get_settings()
        self.assertEqual((settings["keywords"], settings["zipcodes"]), (["作業員", "SMT"], ["330", "333"]))
        resp = self.client.post("/salesdev/taiwanjobs/refresh", follow_redirects=False)
        self.assertIn("msg=", resp.headers["location"])

    def test_settings_validation(self):
        for data in ({"keywords": "", "zipcodes": "330"}, {"keywords": "作業員", "zipcodes": "abc"}):
            resp = self.client.post("/salesdev/taiwanjobs/settings", data=data, follow_redirects=False)
            self.assertIn("err=", resp.headers["location"])

    def test_staff_cannot_change_settings(self):
        with mock.patch.object(self.platform_accounts, "current_account", return_value=self.STAFF):
            self.assertNotIn("下一次執行就重抓職缺清單", self.client.get("/salesdev?tab=taiwanjobs").text)
            for url in ("/salesdev/taiwanjobs/settings", "/salesdev/taiwanjobs/refresh"):
                resp = self.client.post(url, data={"keywords": "x", "zipcodes": "330"}, follow_redirects=False)
                self.assertIn("err=", resp.headers["location"])

    def test_load_error_still_renders(self):
        with mock.patch.object(tj_repo, "list_companies", side_effect=RuntimeError("Firestore 掛了")):
            page = self.client.get("/salesdev?tab=taiwanjobs").text
        self.assertIn("讀取資料時發生錯誤", page)

    def test_export_has_taiwanjobs_sheet(self):
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(self.client.get("/salesdev/export.xlsx").content))
        self.assertEqual(workbook["台灣就業通"].max_row, 4)

    def test_trigger_endpoint_requires_secret(self):
        self.assertEqual(self.client.post("/internal/salesdev/taiwanjobs/run").status_code, 403)


if __name__ == "__main__":
    unittest.main()
