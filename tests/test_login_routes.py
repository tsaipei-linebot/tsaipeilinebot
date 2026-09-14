import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import login_routes
import main
from fastapi.testclient import TestClient


class SafeNextPathTests(unittest.TestCase):
    """_safe_next_path() 防止 next 參數被拿來做開放式轉址（open redirect）：
    只接受同站的相對路徑，其餘一律退回 /portal。"""

    def test_empty_defaults_to_portal(self):
        self.assertEqual(login_routes._safe_next_path(""), "/portal")

    def test_relative_path_is_kept(self):
        self.assertEqual(login_routes._safe_next_path("/management/announcements"), "/management/announcements")

    def test_absolute_external_url_is_rejected(self):
        self.assertEqual(login_routes._safe_next_path("https://evil.example/phish"), "/portal")

    def test_protocol_relative_url_is_rejected(self):
        self.assertEqual(login_routes._safe_next_path("//evil.example/phish"), "/portal")

    def test_path_not_starting_with_slash_is_rejected(self):
        self.assertEqual(login_routes._safe_next_path("evil.example"), "/portal")


class LoginPageRoutingTests(unittest.TestCase):
    """/login、/logout 的基本行為：頁面正常出現、登出會清掉 session 並導回
    登入頁。任何要真的驗證帳密的分支（成功、失敗都一樣）都會呼叫
    authenticate() 打 Firestore，留給有 GCP 憑證的環境做整合測試，跟其他
    路由測試的既有分工一致。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_login_page_loads(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_login_page_keeps_next_param(self):
        resp = self.client.get("/login", params={"next": "/management/announcements"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('value="/management/announcements"', resp.text)

    def test_logout_clears_session_and_redirects_to_login(self):
        resp = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login")


class LoginSubmitLockoutMessageTests(unittest.TestCase):
    """2026-09-14 新增登入防暴力破解機制後，密碼比對失敗時要分辨是「單純
    密碼打錯」還是「帳號已被鎖定」，顯示不同的提示文字給同仁看——見
    platform_accounts.py 的 is_locked_out()／authenticate() 說明。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self):
            self.session = LoginSubmitLockoutMessageTests._FakeSession()

    def test_wrong_password_shows_generic_error(self):
        with mock.patch.object(login_routes.platform_accounts, "authenticate", return_value=None):
            with mock.patch.object(login_routes.platform_accounts, "is_locked_out", return_value=False):
                result = login_routes.login_submit(
                    self._FakeRequest(), username="bob", password="wrong", next="/portal"
                )
        self.assertEqual(result.status_code, 401)
        self.assertIn("帳號或密碼錯誤", result.body.decode("utf-8"))

    def test_locked_account_shows_lockout_message_instead(self):
        with mock.patch.object(login_routes.platform_accounts, "authenticate", return_value=None):
            with mock.patch.object(login_routes.platform_accounts, "is_locked_out", return_value=True):
                result = login_routes.login_submit(
                    self._FakeRequest(), username="bob", password="wrong", next="/portal"
                )
        self.assertEqual(result.status_code, 401)
        self.assertIn("已暫時鎖定", result.body.decode("utf-8"))
        self.assertNotIn("帳號或密碼錯誤", result.body.decode("utf-8"))

    def test_successful_login_does_not_check_lockout(self):
        account = {"username": "bob", "name": "小明", "is_platform_admin": False}
        request = self._FakeRequest()
        with mock.patch.object(login_routes.platform_accounts, "authenticate", return_value=account):
            with mock.patch.object(login_routes.platform_accounts, "is_locked_out") as mock_locked:
                result = login_routes.login_submit(request, username="bob", password="correct", next="/portal")
        self.assertEqual(result.status_code, 303)
        mock_locked.assert_not_called()
        self.assertEqual(request.session["user"], account)


class StopImpersonationRouteTests(unittest.TestCase):
    """POST /impersonate/stop（2026-09-13 新增）：把 session 換回管理員原本
    的帳號，見 accounts_routes.py 的 impersonate_account()。"""

    class _FakeRequest:
        def __init__(self, session):
            self.session = session

    def test_restores_admin_account_from_impersonator(self):
        admin = {"username": "boss", "name": "老闆", "is_platform_admin": True}
        staff = {"username": "staff1", "name": "小明", "is_platform_admin": False}
        request = self._FakeRequest({"user": staff, "impersonator": admin})
        result = login_routes.stop_impersonation(request)
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/accounts"))
        self.assertEqual(request.session["user"], admin)
        self.assertNotIn("impersonator", request.session)

    def test_noop_when_not_impersonating(self):
        staff = {"username": "staff1", "name": "小明", "is_platform_admin": False}
        request = self._FakeRequest({"user": staff})
        result = login_routes.stop_impersonation(request)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(request.session["user"], staff)


if __name__ == "__main__":
    unittest.main()
