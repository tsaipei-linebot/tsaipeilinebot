import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import accounts_routes
import main
from fastapi.testclient import TestClient


class AccountsRoutingSmokeTests(unittest.TestCase):
    """/accounts 是全平台管理員專用的帳號權限管理頁面，跟 delivery/management
    的路由測試一樣，只涵蓋不需要真的打 Firestore 的部分：未登入時的導向。
    需要模擬「已登入且是全平台管理員」才能測到的頁面內容，留給有 GCP 憑證
    的環境做整合測試（跟 test_delivery_routes.py 的既有分工一致）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_accounts_list_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/accounts/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_new_account_form_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/accounts/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_reorder_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.post("/accounts/reorder", json={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))


class _FakeJsonRequest:
    """假的 Request，只提供 reorder_accounts() 用得到的 async json() 方法，
    不需要真的建立一個 Starlette Request 物件。"""

    def __init__(self, payload=None, raise_value_error=False):
        self._payload = payload
        self._raise = raise_value_error

    async def json(self):
        if self._raise:
            raise ValueError("invalid json")
        return self._payload


class ReorderAccountsRouteTests(unittest.TestCase):
    """帳號權限管理頁面拖曳排序存檔的 /accounts/reorder 端點——驗證壞資料
    （不是合法 JSON、缺 department/usernames）擋在 reorder_department() 之前，
    合法請求才會呼叫 platform_accounts.reorder_department()。"""

    def test_invalid_json_returns_400(self):
        result = asyncio.run(
            accounts_routes.reorder_accounts(_FakeJsonRequest(raise_value_error=True), redirect=None)
        )
        self.assertEqual(result.status_code, 400)

    def test_missing_department_returns_400(self):
        result = asyncio.run(
            accounts_routes.reorder_accounts(_FakeJsonRequest({"usernames": ["u1"]}), redirect=None)
        )
        self.assertEqual(result.status_code, 400)

    def test_usernames_not_a_list_returns_400(self):
        result = asyncio.run(
            accounts_routes.reorder_accounts(
                _FakeJsonRequest({"department": "桃園所", "usernames": "u1"}), redirect=None
            )
        )
        self.assertEqual(result.status_code, 400)

    def test_valid_payload_calls_reorder_department(self):
        with mock.patch.object(accounts_routes.platform_accounts, "reorder_department") as mock_reorder:
            result = asyncio.run(
                accounts_routes.reorder_accounts(
                    _FakeJsonRequest({"department": "桃園所", "usernames": ["u2", "u1"]}), redirect=None
                )
            )
        mock_reorder.assert_called_once_with("桃園所", ["u2", "u1"])
        self.assertEqual(result.status_code, 200)

    def test_redirect_present_skips_reorder(self):
        fake_redirect = object()
        with mock.patch.object(accounts_routes.platform_accounts, "reorder_department") as mock_reorder:
            result = asyncio.run(
                accounts_routes.reorder_accounts(_FakeJsonRequest({}), redirect=fake_redirect)
            )
        mock_reorder.assert_not_called()
        self.assertIs(result, fake_redirect)


class _FakeFormData:
    """假的 request.form() 回傳值，只提供這裡用得到的 get()／getlist()。"""

    def __init__(self, pairs):
        self._pairs = pairs

    def get(self, key, default=None):
        for k, v in self._pairs:
            if k == key:
                return v
        return default

    def getlist(self, key):
        return [v for k, v in self._pairs if k == key]


class ModulesFromFormTests(unittest.TestCase):
    """2026-09-12 改版：模組欄位從「每個模組選不開放/專員/主管」簡化成
    勾選「開放/不開放」，_modules_from_form() 回傳的是開放了哪些模組代碼
    的清單，不是模組→角色的字典。"""

    def test_only_checked_modules_are_included(self):
        form = _FakeFormData([("module_delivery", "on"), ("module_management", ""), ("module_hr", "on")])
        self.assertEqual(set(accounts_routes._modules_from_form(form)), {"delivery", "hr"})

    def test_no_checked_modules_returns_empty_list(self):
        form = _FakeFormData([])
        self.assertEqual(accounts_routes._modules_from_form(form), [])


class RankFromFormTests(unittest.TestCase):
    def test_strips_whitespace(self):
        form = _FakeFormData([("rank", "  supervisor  ")])
        self.assertEqual(accounts_routes._rank_from_form(form), "supervisor")

    def test_missing_rank_returns_empty_string(self):
        form = _FakeFormData([])
        self.assertEqual(accounts_routes._rank_from_form(form), "")


class DepartmentManagersTests(unittest.TestCase):
    """新增/編輯帳號表單選了部門後，「所屬主管」自動預帶這個部門目前職級
    副主任（含）以上的帳號——同一個單位不會有兩個同職級的人，但可能同時
    有主任＋副主任兩位，兩位都要列進候選名單。"""

    def test_groups_manager_rank_accounts_by_department(self):
        accounts = [
            {"username": "u1", "department": "管理部", "rank": "supervisor"},
            {"username": "u2", "department": "管理部", "rank": "deputy_supervisor"},
            {"username": "u3", "department": "管理部", "rank": "specialist"},
            {"username": "u4", "department": "財務部", "rank": "manager"},
        ]
        result = accounts_routes._department_managers(accounts)
        self.assertEqual(set(result["管理部"]), {"u1", "u2"})
        self.assertEqual(result["財務部"], ["u4"])
        self.assertNotIn("u3", result.get("管理部", []))

    def test_excludes_given_username(self):
        accounts = [
            {"username": "u1", "department": "管理部", "rank": "manager"},
            {"username": "u2", "department": "管理部", "rank": "manager"},
        ]
        result = accounts_routes._department_managers(accounts, exclude_username="u1")
        self.assertEqual(result["管理部"], ["u2"])

    def test_ignores_accounts_without_department_or_manager_rank(self):
        accounts = [
            {"username": "u1", "department": "", "rank": "manager"},
            {"username": "u2", "department": "管理部", "rank": "specialist"},
        ]
        result = accounts_routes._department_managers(accounts)
        self.assertEqual(result, {})


class CreateAccountSubmitValidationTests(unittest.TestCase):
    """新增帳號：部門要在部門主檔清單裡才算合法選項，職級要是清單裡的
    代碼——2026-09-12 新增，取代原本只檢查「部門不能空白」的規則。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, form_data):
            self._form_data = form_data
            self.session = CreateAccountSubmitValidationTests._FakeSession(
                {"user": {"username": "boss", "name": "老闆", "is_platform_admin": True}}
            )

        async def form(self):
            return self._form_data

    def _valid_pairs(self, **overrides):
        pairs = {
            "username": "newuser", "password": "pw12345", "name": "新同仁",
            "department": "管理部", "rank": "specialist",
        }
        pairs.update(overrides)
        return list(pairs.items())

    def test_department_not_in_master_list_blocks_submit(self):
        form = _FakeFormData(self._valid_pairs(department="不存在的部門"))
        with mock.patch.object(accounts_routes.platform_departments, "department_name_exists", return_value=False):
            with mock.patch.object(accounts_routes.platform_departments, "list_departments", return_value=[]):
                with mock.patch.object(accounts_routes.platform_accounts, "list_accounts", return_value=[]):
                    with mock.patch.object(accounts_routes, "templates") as mock_templates:
                        with mock.patch.object(accounts_routes.platform_accounts, "create_account") as mock_create:
                            asyncio.run(accounts_routes.create_account_submit(self._FakeRequest(form), redirect=None))
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("部門", context["error"])

    def test_invalid_rank_blocks_submit(self):
        form = _FakeFormData(self._valid_pairs(rank="not-a-real-rank"))
        with mock.patch.object(accounts_routes.platform_departments, "department_name_exists", return_value=True):
            with mock.patch.object(accounts_routes.platform_departments, "list_departments", return_value=[]):
                with mock.patch.object(accounts_routes.platform_accounts, "list_accounts", return_value=[]):
                    with mock.patch.object(accounts_routes, "templates") as mock_templates:
                        with mock.patch.object(accounts_routes.platform_accounts, "create_account") as mock_create:
                            asyncio.run(accounts_routes.create_account_submit(self._FakeRequest(form), redirect=None))
        mock_create.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("職級", context["error"])

    def test_valid_submission_creates_account_with_list_modules_and_rank(self):
        form = _FakeFormData(self._valid_pairs() + [("module_delivery", "on")])
        with mock.patch.object(accounts_routes.platform_departments, "department_name_exists", return_value=True):
            with mock.patch.object(accounts_routes.platform_accounts, "account_exists", return_value=False):
                with mock.patch.object(accounts_routes.platform_accounts, "create_account") as mock_create:
                    result = asyncio.run(accounts_routes.create_account_submit(self._FakeRequest(form), redirect=None))
        mock_create.assert_called_once_with(
            "newuser", "pw12345", "新同仁", ["delivery"],
            manager_usernames=[], department="管理部", rank="specialist",
        )
        self.assertEqual(result.status_code, 303)


class ImpersonateAccountRouteTests(unittest.TestCase):
    """全平台管理員切換帳號視角（2026-09-13 新增）：POST /accounts/{username}/
    impersonate 把 session["user"] 換成目標帳號，原本的管理員帳號存進
    session["impersonator"]，之後 stop_impersonation()（login_routes.py）
    再換回來。"""

    class _FakeRequest:
        def __init__(self, admin_account):
            self.session = {"user": admin_account}

    ADMIN = {"username": "boss", "name": "老闆", "is_platform_admin": True}
    STAFF = {"username": "staff1", "name": "小明", "is_platform_admin": False}
    OTHER_ADMIN = {"username": "boss2", "name": "老闆二號", "is_platform_admin": True}

    def test_impersonate_swaps_session_user_and_stores_impersonator(self):
        request = self._FakeRequest(dict(self.ADMIN))
        with mock.patch.object(accounts_routes.platform_accounts, "get_account", return_value=dict(self.STAFF)):
            result = accounts_routes.impersonate_account("staff1", request, redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/portal"))
        self.assertEqual(request.session["user"], self.STAFF)
        self.assertEqual(request.session["impersonator"], self.ADMIN)

    def test_impersonate_missing_target_redirects_with_error(self):
        request = self._FakeRequest(dict(self.ADMIN))
        with mock.patch.object(accounts_routes.platform_accounts, "get_account", return_value=None):
            result = accounts_routes.impersonate_account("ghost", request, redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertIn("not_found", result.headers["location"])
        self.assertEqual(request.session["user"], self.ADMIN)
        self.assertNotIn("impersonator", request.session)

    def test_impersonate_platform_admin_target_is_blocked(self):
        request = self._FakeRequest(dict(self.ADMIN))
        with mock.patch.object(accounts_routes.platform_accounts, "get_account", return_value=dict(self.OTHER_ADMIN)):
            result = accounts_routes.impersonate_account("boss2", request, redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertIn("cannot_impersonate_admin", result.headers["location"])
        self.assertEqual(request.session["user"], self.ADMIN)
        self.assertNotIn("impersonator", request.session)

    def test_redirect_present_skips_impersonation(self):
        fake_redirect = object()
        request = self._FakeRequest(dict(self.ADMIN))
        with mock.patch.object(accounts_routes.platform_accounts, "get_account") as mock_get_account:
            result = accounts_routes.impersonate_account("staff1", request, redirect=fake_redirect)
        mock_get_account.assert_not_called()
        self.assertIs(result, fake_redirect)


if __name__ == "__main__":
    unittest.main()
