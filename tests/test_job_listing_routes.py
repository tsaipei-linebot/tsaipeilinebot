import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import job_listing_routes
import main
from fastapi.testclient import TestClient


class JobListingRoutingSmokeTests(unittest.TestCase):
    """/job-listings 是職缺維護的獨立模組，跟 test_salesdev_routes.py 的既有
    分工一致，只涵蓋不需要真的打 Firestore／GAS 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/job-listings", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/job-listings")

    def test_search_api_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/job-listings/api/jobs", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/job-listings")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/job-listings", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/job-listings")


class RequireAccessDependencyTests(unittest.TestCase):
    """job_listing_routes._require_access() 是 /job-listings 的權限檢查，
    直接單元測試回傳值（跟 test_salesdev_routes.py 同一種寫法）——任何有
    job_listings 模組權限的帳號都能進來，不分「專員」/「主管」角色，實際
    上能不能改某一筆職缺是 GAS 那邊依原刊登人/其主管逐筆判斷，見
    services/job_listing_submit_service.py 開頭的說明。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = job_listing_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/job-listings")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = job_listing_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_staff_module_access_returns_none(self):
        account = {"username": "bob", "name": "Bob", "modules": {"job_listings": "staff"}, "is_platform_admin": False}
        result = job_listing_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_logged_in_with_admin_module_access_returns_none(self):
        account = {"username": "carol", "name": "Carol", "modules": {"job_listings": "admin"}, "is_platform_admin": False}
        result = job_listing_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "name": "Boss", "modules": {}, "is_platform_admin": True}
        result = job_listing_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class EmptyFormValuesTests(unittest.TestCase):
    """_empty_form_values() 一定要幫每個多選欄位補上空陣列，不然樣板拿到
    完全沒有這個 key 的字典時，Jinja2 對 `opt in form.industry` 這種判斷
    會直接噴例外（見 job_listing_routes.py 的說明）。"""

    def test_includes_all_multi_select_fields_as_empty_lists(self):
        result = job_listing_routes._empty_form_values()
        for name in job_listing_routes._MULTI_SELECT_FIELD_NAMES:
            self.assertEqual(result[name], [])


class RequiredFieldsTests(unittest.TestCase):
    """2026-09-09 使用者明確要求：職缺維護表單裡除了「備註說明」跟「職缺
    圖檔上傳」，其餘欄位都要真的必填、沒填不能送出——不再照抄原本 Netlify
    表單「畫面標 * 但其實沒真的擋」的行為。這裡驗證必填清單真的涵蓋全部
    多選欄位（不是只有原本真的有擋的那五個）。"""

    def test_all_multi_select_fields_are_required(self):
        self.assertEqual(
            sorted(job_listing_routes._REQUIRED_MULTI_SELECT_FIELDS),
            sorted(job_listing_routes._MULTI_SELECT_FIELD_NAMES),
        )

    def test_notes_stays_optional(self):
        self.assertNotIn("notes", job_listing_routes._REQUIRED_TEXT_FIELD_NAMES)


if __name__ == "__main__":
    unittest.main()
