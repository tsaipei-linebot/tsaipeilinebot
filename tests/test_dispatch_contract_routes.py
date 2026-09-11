import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_contract_routes
import main
from fastapi.testclient import TestClient


class DispatchContractRoutingSmokeTests(unittest.TestCase):
    """/dispatch-contracts 是派遣契約產生器的獨立模組，跟
    test_project_contract_routes.py 的既有分工一致，只涵蓋不需要真的打
    Firestore／GCS 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_new_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/dispatch-contracts/new", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_download_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts/abc123/download", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")


class RequireAccessDependencyTests(unittest.TestCase):
    """dispatch_contract_routes._require_access() 是 /dispatch-contracts 的
    權限檢查，直接單元測試回傳值（跟 test_project_contract_routes.py 同一種
    寫法）——任何有 dispatch_contracts 模組權限的帳號都能進來，不分
    「專員」/「主管」角色。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = dispatch_contract_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/dispatch-contracts")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "modules": {}, "is_platform_admin": False}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_module_access_returns_none(self):
        account = {"username": "bob", "modules": {"dispatch_contracts": "staff"}, "is_platform_admin": False}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "modules": {}, "is_platform_admin": True}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class SubmitValidationTests(unittest.TestCase):
    """POST /dispatch-contracts/new 的表單驗證跟成功流程——mock 掉
    render_contract_docx／save_submission／dispatch_contract_storage，
    不真的套版、不真的打 Firestore/GCS，只驗證路由本身的邏輯（缺欄位擋下、
    成功時呼叫順序跟回傳的下載檔頭）。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user, form_data):
            self.session = SubmitValidationTests._FakeSession({"user": user})
            self._form_data = form_data

        async def form(self):
            return self._form_data

    def _account(self):
        return {"username": "bob", "modules": {"dispatch_contracts": "staff"}, "is_platform_admin": False}

    def _multidict(self, pairs):
        # Starlette 的 FormData 支援重複 key（getlist），這裡用最簡單的
        # 假物件模擬同樣的介面，不用真的拉 starlette FormData 進來測。
        class _Fake:
            def __init__(self, pairs):
                self._pairs = pairs

            def get(self, key, default=None):
                for k, v in self._pairs:
                    if k == key:
                        return v
                return default

            def getlist(self, key):
                return [v for k, v in self._pairs if k == key]

        return _Fake(pairs)

    def test_missing_client_name_blocks_submit(self):
        form = self._multidict([
            ("work_address", "台北市"), ("work_content", "內容"),
            ("enabled_columns", "title"),
            ("shift_title", "日班"),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                        self._FakeRequest(self._account(), form), redirect=None,
                    ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("客戶名稱", context["error"])

    def test_no_enabled_columns_blocks_submit(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市"), ("work_content", "內容"),
            ("shift_title", "日班"),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                        self._FakeRequest(self._account(), form), redirect=None,
                    ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("欄位", context["error"])

    def test_all_rows_blank_blocks_submit(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市"), ("work_content", "內容"),
            ("enabled_columns", "title"),
            ("shift_title", ""),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                        self._FakeRequest(self._account(), form), redirect=None,
                    ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("班別", context["error"])

    def test_successful_submit_renders_saves_and_returns_docx(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市內湖區"), ("work_content", "收銀"),
            ("pay_cycle", "每月1號"),
            ("enabled_columns", "title"), ("enabled_columns", "wage"),
            ("shift_title", "日班"), ("shift_wage", "196/hr"),
            ("clause_leave_policy", ""), ("clause_overtime_allowance_note", ""),
            ("clause_dress_deposit_note", ""), ("clause_benefits_note", ""), ("clause_onboarding_note", ""),
        ])
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=fake_bytes) as mock_render:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=False):
                    result = asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                        self._FakeRequest(self._account(), form), redirect=None,
                    ))
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args.kwargs
        self.assertEqual(render_kwargs["shifts"], [{"title": "日班", "hours": "－", "wage": "196/hr", "bonus": "－", "overtime": "－"}])
        mock_save.assert_called_once()
        self.assertEqual(result.body, fake_bytes)
        self.assertIn("attachment", result.headers["content-disposition"])
        self.assertIn("pchome", result.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
