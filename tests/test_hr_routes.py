import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import hr.routes.file_routes as file_routes
import hr.routes.insurance_routes as insurance_routes
import main
from fastapi.testclient import TestClient


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None, path="/hr/insurance"):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user
        self.url = SimpleNamespace(path=path)


def _upload_department_account():
    return {"username": "dora", "name": "Dora", "department": "桃園所", "is_platform_admin": False, "modules": []}


def _unrelated_department_account():
    return {"username": "eric", "name": "Eric", "department": "新北所", "is_platform_admin": False, "modules": []}


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

    def _assert_redirects_to_root_login(self, path):
        """加退保上傳／查歷史／首頁自動導向（2026-09-22 改成不用勾「人資
        專區」模組權限，見 hr/routes/insurance_routes.py 的
        `_require_login()` 說明）沒登入時導去根層級的 /login，不是
        /hr/login——因為打這幾支路由的同仁不一定有人資模組權限。"""
        resp = self.client.get(path, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], f"/login?next={path}")

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
        """每日加退保首頁自動導向（2026-09-22 改成不用勾「人資專區」模組
        權限，見上面 `_assert_redirects_to_root_login()` 的說明），沒登入
        一律導去根層級登入頁，不會洩漏這個功能存在與否。"""
        self._assert_redirects_to_root_login("/hr/insurance")

    def test_insurance_upload_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_root_login("/hr/insurance/upload")

    def test_insurance_history_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_root_login("/hr/insurance/history")

    def test_insurance_help_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_root_login("/hr/insurance/help")

    def test_insurance_summary_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/summary")

    def test_insurance_download_page_redirects_to_login_when_not_authenticated(self):
        self._assert_redirects_to_login("/hr/insurance/download")

    def test_login_page_renders(self):
        resp = self.client.get("/hr/login")
        self.assertEqual(resp.status_code, 200)


class InsuranceRequireLoginDependencyTests(unittest.TestCase):
    """_require_login()（2026-09-22 新增，見 hr/routes/insurance_routes.py
    開頭的說明）：只檢查有沒有登入，不像其他 hr 頁面要求「人資專區」模組
    權限——沒登入導去根層級 /login（不是 /hr/login），`next` 帶原本要去
    的網址；有登入（不管有沒有勾人資模組）都放行，看不看得到資料交給
    `repo.can_upload()`／`is_collector()` 那層部門字串判斷。"""

    def test_no_session_redirects_to_root_login_with_next(self):
        result = insurance_routes._require_login(_FakeRequest(path="/hr/insurance/upload"))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/login?next=/hr/insurance/upload")

    def test_logged_in_without_hr_module_allowed_through(self):
        result = insurance_routes._require_login(_FakeRequest(_upload_department_account()))
        self.assertIsNone(result)


class InsuranceAccessRedirectTests(unittest.TestCase):
    """_access_redirect()：2026-09-22 改成導回 /portal（原本是 /hr/）——
    現在打這幾支路由的同仁不一定有「人資專區」模組權限，導去 /hr/ 反而會
    再被模組權限那層擋一次，變成迴圈式的「看不懂為什麼進不去」。"""

    def test_unrelated_department_redirects_to_portal(self):
        result = insurance_routes._access_redirect(_unrelated_department_account())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_upload_department_allowed_through(self):
        result = insurance_routes._access_redirect(_upload_department_account())
        self.assertIsNone(result)


class UploadPageAccessTests(unittest.TestCase):
    """upload_page()：驗證這次的重點——上傳部門的帳號不用勾「人資專區」
    模組權限也看得到上傳頁；部門字串對不上的帳號被導回 /portal。"""

    def test_upload_department_can_view_page_without_hr_module(self):
        with mock.patch.object(insurance_routes.repo, "is_day_closed", return_value=False), \
             mock.patch.object(insurance_routes.repo, "get_upload", return_value=None):
            result = insurance_routes.upload_page(
                _FakeRequest(_upload_department_account()), work_date="2026-09-22", redirect=None
            )
        self.assertEqual(result.status_code, 200)

    def test_unrelated_department_redirected_to_portal(self):
        result = insurance_routes.upload_page(_FakeRequest(_unrelated_department_account()), work_date="", redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")


class InsuranceHelpPageTests(unittest.TestCase):
    """insurance_help_page()（2026-09-22 新增）：獨立於 /hr/help 之外，
    不用「人資專區」模組權限也看得到的加退保使用說明頁。"""

    def test_upload_department_can_view_help_page(self):
        with mock.patch.object(insurance_routes, "templates") as mock_templates:
            insurance_routes.insurance_help_page(_FakeRequest(_upload_department_account()), redirect=None)
        args = mock_templates.TemplateResponse.call_args[0]
        self.assertEqual(args[1], "insurance_help.html")

    def test_unrelated_department_redirected_to_portal(self):
        result = insurance_routes.insurance_help_page(_FakeRequest(_unrelated_department_account()), redirect=None)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")


class FileRouteAccessDependencyTests(unittest.TestCase):
    """hr/routes/file_routes.py 的 `_require_access()`（2026-09-22 調整，
    見檔案開頭說明）：加退保（`hr/insurance/` 前綴）的檔案改用
    `has_insurance_access()` 判斷，不用「人資專區」模組權限；其他子功能
    的檔案維持原本 login_required（模組權限）不變，兩種前綴分開測。"""

    _INSURANCE_PATH = "hr/insurance/2026-09-22_桃園所/abc123.xlsx"
    _OTHER_PATH = "hr/health-checks/emp1/xyz789.pdf"

    def test_insurance_file_no_session_redirects_to_root_login(self):
        from urllib.parse import unquote

        result = file_routes._require_access(self._INSURANCE_PATH, _FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(unquote(result.headers["location"]), f"/login?next=/hr/files/{self._INSURANCE_PATH}")

    def test_insurance_file_allowed_for_upload_department_without_hr_module(self):
        result = file_routes._require_access(self._INSURANCE_PATH, _FakeRequest(_upload_department_account()))
        self.assertIsNone(result)

    def test_insurance_file_404_for_unrelated_department(self):
        result = file_routes._require_access(self._INSURANCE_PATH, _FakeRequest(_unrelated_department_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 404)

    def test_other_file_no_session_redirects_to_hr_login(self):
        """非加退保的檔案（體檢報告等）維持原本 login_required 的行為，
        沒登入導去 /hr/login，不是根層級 /login。"""
        result = file_routes._require_access(self._OTHER_PATH, _FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/hr/login")

    def test_other_file_blocked_without_hr_module(self):
        """部門帳號沒有勾「人資專區」模組權限，還是進不去其他子功能的
        檔案——只有加退保被放寬，其他維持原樣。"""
        result = file_routes._require_access(self._OTHER_PATH, _FakeRequest(_upload_department_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")


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
