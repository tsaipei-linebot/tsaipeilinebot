import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class DeliveryRoutingSmokeTests(unittest.TestCase):
    """只涵蓋不需要真的打 Firestore 的路由（頁面渲染 / 登入前導向 / 靜態檔），
    確保 /delivery 這個掛載的子系統至少能正常啟動、路由能對得起來。
    需要實際讀寫人員/補款/病假資料的路徑（會呼叫 Firestore）不在這裡涵蓋，
    留給有 GCP 憑證的環境做整合測試。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_vendor_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/delivery/vendor/shopee", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_login_page_renders(self):
        resp = self.client.get("/delivery/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("配送部系統", resp.text)

    def test_static_css_is_served(self):
        resp = self.client.get("/delivery/static/style.css")
        self.assertEqual(resp.status_code, 200)

    def test_recruitment_bot_routes_still_work_unaffected(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.json())


if __name__ == "__main__":
    unittest.main()
