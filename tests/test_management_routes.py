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

    def test_sim_payment_day_update_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/management/assets/some-id/sim-payment-day", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/management/login"))


class SimPaymentReminderEndpointTests(unittest.TestCase):
    """/management/api/sim-payment-reminder-check 比照配送部文件到期提醒的
    密鑰保護作法：沒帶對 header 一律 403，不會真的去查 Firestore 或推播。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_forbidden_without_secret_configured(self):
        resp = self.client.post("/management/api/sim-payment-reminder-check")
        self.assertEqual(resp.status_code, 403)

    def test_forbidden_with_wrong_secret(self):
        import management.routes.reminder_routes as reminder_routes

        original = reminder_routes.ASSET_REMINDER_SECRET
        reminder_routes.ASSET_REMINDER_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/management/api/sim-payment-reminder-check",
                headers={"X-Management-Asset-Reminder-Secret": "wrong-secret"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            reminder_routes.ASSET_REMINDER_SECRET = original


class ManagementLineWebhookTests(unittest.TestCase):
    """管理部專屬 LINE Webhook：沒設定 Channel Secret（測試環境預設狀態）
    一律回傳 503，等同這個功能還沒啟用；有設定但缺簽章 header 則是 400。
    不驗證真正的簽章比對邏輯——那是 line-bot-sdk 本身的責任。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_returns_503_when_channel_not_configured(self):
        resp = self.client.post("/management/line/callback", content=b"{}")
        self.assertEqual(resp.status_code, 503)

    def test_returns_400_when_signature_header_missing_but_configured(self):
        import management.routes.line_webhook_routes as line_webhook_routes

        original_handler = line_webhook_routes.handler
        line_webhook_routes.handler = object()  # 只需要是 truthy，不會真的被呼叫到
        try:
            resp = self.client.post("/management/line/callback", content=b"{}")
            self.assertEqual(resp.status_code, 400)
        finally:
            line_webhook_routes.handler = original_handler


if __name__ == "__main__":
    unittest.main()
