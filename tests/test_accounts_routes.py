import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

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


if __name__ == "__main__":
    unittest.main()
