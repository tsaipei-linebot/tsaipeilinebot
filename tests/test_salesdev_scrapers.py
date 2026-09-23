import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from salesdev.scrapers import jobs_104, jobs_1111, jobs_chickpt


class Jobs104ParseTests(unittest.TestCase):
    def _item(self, **overrides):
        item = {
            "custName": "天泰人力銀行_鼎利國際事業有限公司",
            "jobName": "(R)桃鶯路 簡單組包",
            "jobNo": "15370882",
            "link": {"job": "//www.104.com.tw/job/95s6n?jobsource=2018indexpoc", "cust": "//www.104.com.tw/company/1a2x6blud9"},
            "jobAddrNoDesc": "桃園市桃園區",
            "jobAddress": "桃鶯路",
            "appearDate": "20260911",
            "descSnippet": "聯絡電話 03-4604549",
        }
        item.update(overrides)
        return item

    def test_job_id_comes_from_url_so_old_sheet_rows_match(self):
        lead = jobs_104.parse_item(self._item())
        self.assertEqual(lead.job_id, "95s6n")
        self.assertEqual(lead.job_url, "https://www.104.com.tw/job/95s6n")
        self.assertEqual(lead.work_address, "桃園市桃園區桃鶯路")
        self.assertEqual(lead.phone, "03-4604549")

    def test_non_dispatch_company_is_skipped(self):
        self.assertIsNone(jobs_104.parse_item(self._item(custName="統一超商股份有限公司")))

    def test_missing_street_leaves_address_empty(self):
        lead = jobs_104.parse_item(self._item(jobAddress=""))
        self.assertEqual(lead.work_address, "")
        self.assertEqual(lead.area, "桃園市桃園區")


class Jobs1111ParseTests(unittest.TestCase):
    def test_parse(self):
        lead = jobs_1111.parse_item({"companyName": "北大人力資源管理顧問有限公司", "jobId": 132534310, "title": "長期理貨 楊梅"})
        self.assertEqual(lead.job_id, "132534310")
        self.assertEqual(lead.job_url, "https://www.1111.com.tw/job/132534310")


class ChickptTests(unittest.TestCase):
    def _html(self, posting):
        return (
            "<html><head><script type=\"application/ld+json\">"
            + json.dumps(posting, ensure_ascii=False)
            + "</script></head></html>"
        )

    def test_extracts_job_posting_without_beautifulsoup(self):
        posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "包裝員", "hiringOrganization": {"name": "來寶國際人力派遣有限公司"}}
        self.assertEqual(jobs_chickpt.extract_job_posting(self._html(posting))["title"], "包裝員")

    def test_extracts_from_graph(self):
        data = {"@graph": [{"@type": "WebPage"}, {"@type": "JobPosting", "title": "撿貨員"}]}
        self.assertEqual(jobs_chickpt.extract_job_posting(self._html(data))["title"], "撿貨員")

    def test_utf8_decoding_fixes_emoji(self):
        """小雞上工沒宣告 charset，requests 預設用 latin-1 解碼 → 表情符號變亂碼。"""

        class FakeResp:
            content = "🔥日領5200".encode("utf-8")
            encoding = "ISO-8859-1"

            @property
            def text(self):
                return self.content.decode(self.encoding)

        self.assertEqual(jobs_chickpt._utf8_text(FakeResp()), "🔥日領5200")

    def test_street_with_its_own_county_is_not_glued_to_locality(self):
        posting = {"jobLocation": {"address": {"addressRegion": "彰化縣", "addressLocality": "彰化市", "streetAddress": "新北市三重區自強路5段110號"}}}
        self.assertEqual(jobs_chickpt.posting_full_address(posting), "新北市三重區自強路5段110號")

    def test_street_without_county_is_prefixed(self):
        posting = {"jobLocation": {"address": {"addressRegion": "桃園市", "addressLocality": "楊梅區", "streetAddress": "獅二路10號"}}}
        self.assertEqual(jobs_chickpt.posting_full_address(posting), "桃園市楊梅區獅二路10號")

    def test_parse_posting_skips_non_dispatch(self):
        posting = {"title": "店員", "hiringOrganization": {"name": "某某餐廳"}}
        self.assertIsNone(jobs_chickpt.parse_posting("https://www.chickpt.com.tw/job-abc", posting))


if __name__ == "__main__":
    unittest.main()
