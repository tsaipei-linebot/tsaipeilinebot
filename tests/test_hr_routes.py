import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
from fastapi.testclient import TestClient


class HrRoutingSmokeTests(unittest.TestCase):
    """跟 test_management_routes.py 是同一種涵蓋範圍：只測不需要真的打
    Firestore 的路由（頁面渲染/登入前導向），確保 /hr 這個新掛載的子系統
    至少能正常啟動、路由能對得起來，也確認跟既有的配送部/管理部系統、
    根路由完全不互相影響。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def _assert_redirects_to_login(self, path):
        resp = self.client.get(path, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/hr/login"))

    def test_home_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/")

    def test_help_page_redirects_to_login_when_not_authenticated(self):
        """使用說明頁（2026-09-18 新增）走跟主頁同一組 login_required。"""
        self._assert_redirects_to_login("/hr/help")

    def test_incidents_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/incidents")

    def test_health_checks_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/health-checks")

    def test_care_logs_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/care-logs")

    def test_licenses_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/licenses")

    def test_trainings_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/trainings")

    def test_insurance_home_redirects_to_login_when_not_authenticated(self):
        """每日加退保（2026-09-22 新增）跟其他功能一樣先過 login_required，
        沒登入一律導去登入頁，不會洩漏這個功能存在與否。"""
        self._assert_redirects_to_login("/hr/insurance")

    def test_insurance_upload_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/upload")

    def test_insurance_history_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/history")

    def test_insurance_summary_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/summary")

    def test_insurance_download_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/download")

    def test_login_page_renders(self):
        resp = self.client.get("/hr/login")
        self.assertEqual(resp.status_code, 200)


class HrReminderEndpointTests(unittest.TestCase):
    """兩個排程提醒端點比照管理部門號繳費提醒的密鑰保護作法：沒帶對 header
    一律 403，不會真的去查 Firestore 或推播。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_incident_reminder_forbidden_without_secret_configured(self):
        resp = self.client.post("/hr/api/incident-weekly-reminder-check")
        self.assertEqual(resp.status_code, 403)

    def test_incident_reminder_forbidden_with_wrong_secret(self):
        import hr.routes.reminder_routes as reminder_routes

        original = reminder_routes.HR_INCIDENT_REMINDER_SECRET
        reminder_routes.HR_INCIDENT_REMINDER_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/hr/api/incident-weekly-reminder-check",
                headers={"X-Hr-Incident-Reminder-Secret": "wrong-secret"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            reminder_routes.HR_INCIDENT_REMINDER_SECRET = original

    def test_license_reminder_forbidden_without_secret_configured(self):
        resp = self.client.post("/hr/api/license-reminder-check")
        self.assertEqual(resp.status_code, 403)

    def test_license_reminder_forbidden_with_wrong_secret(self):
        import hr.routes.reminder_routes as reminder_routes

        original = reminder_routes.HR_LICENSE_REMINDER_SECRET
        reminder_routes.HR_LICENSE_REMINDER_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/hr/api/license-reminder-check",
                headers={"X-Hr-License-Reminder-Secret": "wrong-secret"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            reminder_routes.HR_LICENSE_REMINDER_SECRET = original


if __name__ == "__main__":
    unittest.main()
