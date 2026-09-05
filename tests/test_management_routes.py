import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class ManagementRoutingSmokeTests(unittest.TestCase):
    """跟 test_delivery_routes.py 是同一種涵蓋範圍：只測不需要真的打
    Firestore 的路由（頁面渲染/登入前導向），確保 /management 這個新掛載
    的子系統至少能正常啟動、路由能對得起來，也確認跟既有的配送部系統、
    根路由完全不互相影響。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_announcements_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/announcements", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_kpi_reports_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/kpi-reports", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_client_visits_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/client-visits", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_staff_directory_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/staff-directory", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_org_chart_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/staff-directory/org-chart", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_assets_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/assets", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_asset_detail_page_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/management/assets/some-id", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_new_asset_form_path_is_not_shadowed_by_detail_route(self):
        # /assets/new 要能正確配到「新增資產」的路由，而不是被
        # /assets/{asset_id} 這個動態路由攔截、把 "new" 當成 asset_id。
        resp = self.client.get("/management/assets/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))

    def test_login_page_renders(self):
        resp = self.client.get("/management/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("管理部", resp.text)

    def test_delivery_and_root_routes_still_work_unaffected(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.json())

        resp = self.client.get("/delivery/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))


if __name__ == "__main__":
    unittest.main()
