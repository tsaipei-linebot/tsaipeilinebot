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


if __name__ == "__main__":
    unittest.main()
