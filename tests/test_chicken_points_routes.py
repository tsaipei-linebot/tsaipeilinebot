import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import chicken_points_routes
import main
import platform_accounts
from fastapi.testclient import TestClient


class ChickenPointsRoutingSmokeTests(unittest.TestCase):
    """/chicken-points 是小雞點數自費申請的獨立模組，只涵蓋不需要真的打
    Firestore 的部分：未登入時的導向（跟 test_project_contract_routes.py
    同一種寫法）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/chicken-points", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/chicken-points")

    def test_new_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/chicken-points/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/chicken-points")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/chicken-points/new", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/chicken-points")


class RequireAccessDependencyTests(unittest.TestCase):
    """chicken_points_routes._require_access() 是 /chicken-points 的權限
    檢查，直接單元測試回傳值（跟 test_project_contract_routes.py 同一種
    寫法）。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = chicken_points_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/chicken-points")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = chicken_points_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_staff_module_access_returns_none(self):
        account = {"username": "bob", "name": "Bob", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        result = chicken_points_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_logged_in_with_admin_module_access_returns_none(self):
        account = {"username": "carol", "name": "Carol", "modules": {"chicken_points": "admin"}, "is_platform_admin": False}
        result = chicken_points_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class RoleBasedRecordVisibilityTests(unittest.TestCase):
    """核心規則：「專員」只看自己送出過的紀錄，「主管」看得到全部同仁的
    紀錄——這是目前平台第一個真的用到專員/主管角色差異的模組。直接呼叫
    route function 本身（跟 test_portal.py／test_job_listing_routes.py
    的既有分工一致：需要真的登入 session 才能測的行為，不透過 TestClient
    真的跑一次 HTTP，而是直接單元測試，把 `redirect` 依賴的回傳值固定
    傳 None 模擬「已通過權限檢查」），順便把 templates.TemplateResponse
    mock 掉，不需要真的走一次 Jinja2 渲染。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = RoleBasedRecordVisibilityTests._FakeSession({"user": user})

    def test_staff_role_calls_list_requests_by_username(self):
        account = {"username": "bob", "name": "Bob", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "list_requests_by_username", return_value=[]) as mock_by_user:
            with mock.patch.object(chicken_points_routes, "list_all_requests") as mock_all:
                with mock.patch.object(chicken_points_routes, "templates"):
                    chicken_points_routes.chicken_points_home(self._FakeRequest(account), redirect=None)
        mock_by_user.assert_called_once_with("bob")
        mock_all.assert_not_called()

    def test_admin_role_calls_list_all_requests(self):
        account = {"username": "carol", "name": "Carol", "modules": {"chicken_points": "admin"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "list_all_requests", return_value=[]) as mock_all:
            with mock.patch.object(chicken_points_routes, "list_requests_by_username") as mock_by_user:
                with mock.patch.object(chicken_points_routes, "templates"):
                    chicken_points_routes.chicken_points_home(self._FakeRequest(account), redirect=None)
        mock_all.assert_called_once()
        mock_by_user.assert_not_called()


class DepartmentAutoFillTests(unittest.TestCase):
    """2026-09-11 使用者要求：申請部門直接沿用帳號資料的 department 欄位，
    不再讓同仁自己選。這裡驗證兩條規則：帳號沒設定部門時擋下、給清楚
    提示；帳號有部門時，送出的資料直接採用帳號的部門，不是表單另外傳的
    值（新版路由已經不再收 department 這個表單欄位）。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DepartmentAutoFillTests._FakeSession({"user": user})

    def test_new_form_shows_missing_department_message(self):
        account = {"username": "bob", "name": "Bob", "department": "", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "templates") as mock_templates:
            chicken_points_routes.chicken_points_new_form(self._FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["error"], chicken_points_routes._MISSING_DEPARTMENT_MESSAGE)

    def test_new_form_no_error_when_department_set(self):
        account = {"username": "bob", "name": "Bob", "department": "桃園所", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "templates") as mock_templates:
            chicken_points_routes.chicken_points_new_form(self._FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["error"], "")

    def test_submit_blocks_when_account_has_no_department(self):
        account = {"username": "bob", "name": "Bob", "department": "", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "templates") as mock_templates:
            with mock.patch.object(chicken_points_routes, "save_request") as mock_save:
                asyncio.run(chicken_points_routes.chicken_points_submit(
                    self._FakeRequest(account),
                    purchase_month="2026-08",
                    points="5000",
                    signed_image="data:image/png;base64,AAAA",
                    redirect=None,
                ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["error"], chicken_points_routes._MISSING_DEPARTMENT_MESSAGE)

    def test_submit_uses_account_department(self):
        account = {"username": "bob", "name": "Bob", "department": "桃園所", "modules": {"chicken_points": "staff"}, "is_platform_admin": False}
        with mock.patch.object(chicken_points_routes, "save_request") as mock_save:
            result = asyncio.run(chicken_points_routes.chicken_points_submit(
                self._FakeRequest(account),
                purchase_month="2026-08",
                points="5000",
                signed_image="data:image/png;base64,AAAA",
                redirect=None,
            ))
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args.kwargs["department"], "桃園所")
        self.assertEqual(result.status_code, 303)


if __name__ == "__main__":
    unittest.main()
