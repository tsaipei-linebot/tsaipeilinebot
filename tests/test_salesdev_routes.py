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
    """/salesdev 是唯讀彙整頁面，跟 test_accounts_routes.py 的既有分工一致，
    只涵蓋不需要真的打 Firestore／Google Sheets API 的部分：未登入時的導向。
    需要模擬「已登入且有 salesdev 模組權限」才能測到的頁面內容，留給有 GCP
    憑證的環境做整合測試。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/salesdev", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/salesdev")


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

    def test_logged_in_with_module_access_returns_none(self):
        account = {"username": "bob", "name": "Bob", "modules": {"salesdev": "staff"}, "is_platform_admin": False}
        result = salesdev_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "name": "Boss", "modules": {}, "is_platform_admin": True}
        result = salesdev_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
