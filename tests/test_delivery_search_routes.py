import os
import sys
from unittest import mock
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import search_routes


class _FakeRequest:
    def __init__(self):
        self.session = {"user": {"username": "staff1", "name": "小明", "modules": ["delivery"], "rank": "specialist"}}


class SearchPageTests(unittest.TestCase):
    """查詢人員頁面（2026-09-13 加上廠商/狀態篩選）：姓名關鍵字、廠商代碼
    只要有一個有值就會查，兩者都沒給就不查（避免一次列出全公司所有人）；
    不合法的廠商代碼／狀態代碼要被忽略，不能讓使用者亂帶參數繞過驗證。"""

    def test_no_keyword_and_no_vendor_skips_search(self):
        with mock.patch.object(search_routes.repository, "search_personnel") as mock_search:
            with mock.patch.object(search_routes, "templates") as mock_templates:
                search_routes.search_page(_FakeRequest(), keyword="", vendor="", status="", redirect=None)
        mock_search.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["results"], [])

    def test_vendor_only_triggers_search(self):
        with mock.patch.object(search_routes.repository, "search_personnel", return_value=[]) as mock_search:
            with mock.patch.object(search_routes, "templates"):
                search_routes.search_page(_FakeRequest(), keyword="", vendor="shopee", status="", redirect=None)
        mock_search.assert_called_once_with("", vendor="shopee", employment_status="")

    def test_keyword_only_triggers_search(self):
        with mock.patch.object(search_routes.repository, "search_personnel", return_value=[]) as mock_search:
            with mock.patch.object(search_routes, "templates"):
                search_routes.search_page(_FakeRequest(), keyword="王小明", vendor="", status="", redirect=None)
        mock_search.assert_called_once_with("王小明", vendor="", employment_status="")

    def test_invalid_vendor_is_ignored_and_skips_search(self):
        with mock.patch.object(search_routes.repository, "search_personnel") as mock_search:
            with mock.patch.object(search_routes, "templates") as mock_templates:
                search_routes.search_page(_FakeRequest(), keyword="", vendor="not-a-real-vendor", status="", redirect=None)
        mock_search.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["filter_vendor"], "")

    def test_invalid_status_is_ignored(self):
        person = {"id": "p1", "name": "王小明", "vendor": "shopee", "employment_status": "employed"}
        with mock.patch.object(search_routes.repository, "search_personnel", return_value=[person]) as mock_search:
            with mock.patch.object(search_routes.repository, "missing_documents", return_value=[]):
                with mock.patch.object(search_routes, "templates") as mock_templates:
                    search_routes.search_page(_FakeRequest(), keyword="王小明", vendor="", status="not-a-real-status", redirect=None)
        mock_search.assert_called_once_with("王小明", vendor="", employment_status="")
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["filter_status"], "")

    def test_result_rows_include_employment_status_display(self):
        person = {"id": "p1", "name": "王小明", "vendor": "shopee", "employment_status": "resigned"}
        with mock.patch.object(search_routes.repository, "search_personnel", return_value=[person]):
            with mock.patch.object(search_routes.repository, "missing_documents", return_value=[]):
                with mock.patch.object(search_routes, "templates") as mock_templates:
                    search_routes.search_page(_FakeRequest(), keyword="", vendor="shopee", status="", redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        row = context["results"][0]
        self.assertEqual(row["employment_status_name"], "離職")
        self.assertEqual(row["employment_status_badge_class"], "badge-resigned")

    def test_redirect_present_skips_everything(self):
        fake_redirect = object()
        with mock.patch.object(search_routes.repository, "search_personnel") as mock_search:
            result = search_routes.search_page(_FakeRequest(), keyword="王小明", vendor="shopee", status="", redirect=fake_redirect)
        mock_search.assert_not_called()
        self.assertIs(result, fake_redirect)


if __name__ == "__main__":
    unittest.main()
