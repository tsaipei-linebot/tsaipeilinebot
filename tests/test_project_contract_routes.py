import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import project_contract_routes
import main
from fastapi.testclient import TestClient


class ProjectContractRoutingSmokeTests(unittest.TestCase):
    """/project-contracts 是專案合約維護的獨立模組，跟 test_job_listing_routes.py
    的既有分工一致，只涵蓋不需要真的打 Firestore／GAS 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/project-contracts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/project-contracts")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/project-contracts", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/project-contracts")


class RequireAccessDependencyTests(unittest.TestCase):
    """project_contract_routes._require_access() 是 /project-contracts 的
    權限檢查，直接單元測試回傳值（跟 test_job_listing_routes.py 同一種
    寫法）——任何有 project_contracts 模組權限的帳號都能進來，不分
    「專員」/「主管」角色，兩者體驗完全一樣。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = project_contract_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/project-contracts")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_staff_module_access_returns_none(self):
        account = {"username": "bob", "name": "Bob", "modules": {"project_contracts": "staff"}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_logged_in_with_admin_module_access_returns_none(self):
        account = {"username": "carol", "name": "Carol", "modules": {"project_contracts": "admin"}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "name": "Boss", "modules": {}, "is_platform_admin": True}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
