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
    """查詢人員頁（2026-09-24 改版成主要人員清單）：一打開就查（不用先打
    關鍵字），篩選是姓名/電話/廠商/狀態；不合法的廠商/狀態代碼要被忽略。"""

    def _run(self, people=None, debt=None, **params):
        kwargs = {"name": "", "phone": "", "vendor": "", "status": ""}
        kwargs.update(params)
        with mock.patch.object(search_routes.repository, "search_personnel", return_value=people or []) as mock_search:
            with mock.patch.object(search_routes.repository, "list_equipment_debt", return_value=debt or []):
                with mock.patch.object(search_routes, "templates") as mock_templates:
                    search_routes.search_page(_FakeRequest(), redirect=None, **kwargs)
        return mock_search, mock_templates.TemplateResponse.call_args[0][2]

    def test_opens_with_a_list_without_any_filter(self):
        mock_search, context = self._run()
        mock_search.assert_called_once_with(name="", phone="", vendor="", employment_status="")
        self.assertFalse(context["has_filters"])
        self.assertEqual(context["back_url"], "/delivery/search")

    def test_filters_are_passed_through_and_kept_in_back_url(self):
        mock_search, context = self._run(name=" 王小明 ", phone="0912", vendor="shopee", status="employed")
        mock_search.assert_called_once_with(name="王小明", phone="0912", vendor="shopee", employment_status="employed")
        self.assertTrue(context["back_url"].startswith("/delivery/search?"))
        self.assertIn("vendor=shopee", context["back_url"])

    def test_invalid_vendor_and_status_are_ignored(self):
        mock_search, context = self._run(vendor="not-a-real-vendor", status="not-a-real-status")
        mock_search.assert_called_once_with(name="", phone="", vendor="", employment_status="")
        self.assertEqual(context["filter_vendor"], "")
        self.assertEqual(context["filter_status"], "")

    def test_rows_include_status_expiry_items_and_equipment_owed(self):
        person = {"id": "p1", "name": "王小明", "vendor": "ud", "employment_status": "employed"}
        debt = [{"personnel_id": "p1", "quantity_owed": 2}, {"personnel_id": "other", "quantity_owed": 5}]
        _, context = self._run(people=[person], debt=debt)
        row = context["results"][0]
        self.assertEqual(row["employment_status"], "employed")
        self.assertEqual(row["employment_status_name"], "在職")
        self.assertEqual([d["name"] for d in row["doc_statuses"]], ["良民證"])
        self.assertEqual(row["equipment_owed"], 2)

    def test_redirect_present_skips_everything(self):
        fake_redirect = object()
        with mock.patch.object(search_routes.repository, "search_personnel") as mock_search:
            result = search_routes.search_page(_FakeRequest(), name="王小明", redirect=fake_redirect)
        mock_search.assert_not_called()
        self.assertIs(result, fake_redirect)


if __name__ == "__main__":
    unittest.main()
