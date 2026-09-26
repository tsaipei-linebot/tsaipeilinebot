import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
import salesdev_routes
from fastapi.testclient import TestClient


class SalesdevRoutingSmokeTests(unittest.TestCase):
    """未登入時的導向。已登入的頁面內容見 test_salesdev_pages.py（用記憶體版
    Firestore）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/salesdev", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/salesdev")

    def test_help_page_redirects_to_login_when_not_authenticated(self):
        """使用說明頁（2026-09-18 新增）跟 /salesdev 共用同一個
        _require_access。"""
        resp = self.client.get("/salesdev/help", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/salesdev")

    def test_select_post_redirects_to_login_when_not_authenticated(self):
        """/salesdev/select（2026-09-17 新增的「勾選送出」路由）跟 /salesdev
        共用同一個 _require_access，未登入時一樣要導去登入頁，不能繞過
        權限檢查直接寫入資料。"""
        resp = self.client.post(
            "/salesdev/select",
            data={"group_ids": ["g1"]},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/salesdev")

    def test_other_new_routes_require_login(self):
        """2026-09-24 改版新增的路由都要擋未登入。"""
        for method, path in (
            ("get", "/salesdev/groups/g1"),
            ("post", "/salesdev/groups/g1/review"),
            ("post", "/salesdev/groups/g1/note"),
            ("post", "/salesdev/groups/g1/contact-log"),
            ("post", "/salesdev/jobs/j1/internal"),
            ("get", "/salesdev/export.xlsx"),
            ("post", "/salesdev/import-sheet"),
        ):
            resp = getattr(self.client, method)(path, follow_redirects=False)
            self.assertEqual(resp.status_code, 303, path)
            self.assertEqual(resp.headers["location"], "/login?next=/salesdev", path)

    def test_scrape_trigger_requires_secret(self):
        resp = self.client.post("/internal/salesdev/scrape/run", headers={"X-Salesdev-Scrape-Secret": "wrong"})
        self.assertEqual(resp.status_code, 403)


class RequireAccessDependencyTests(unittest.TestCase):
    """salesdev_routes._require_access() 是 /salesdev 的權限檢查，直接單元
    測試回傳值，不用真的透過 TestClient 跑一次 HTTP（跟 test_portal.py 的
    RequireLoginDependencyTests 是同一種寫法）。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = salesdev_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/salesdev")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = salesdev_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")

    def test_module_checked_but_not_platform_admin_is_redirected(self):
        """2026-09-26 起只有全平台管理員能進來：舊帳號資料裡就算還勾著這個模組
        （不管職級），也一律擋回首頁。"""
        for rank in ("specialist", "manager"):
            account = {"username": "bob", "name": "Bob", "modules": ["salesdev"], "rank": rank, "is_platform_admin": False}
            result = salesdev_routes._require_access(self._FakeRequest(account))
            self.assertEqual(result.headers["location"], "/portal", rank)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "name": "Boss", "modules": {}, "is_platform_admin": True}
        result = salesdev_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
