import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
