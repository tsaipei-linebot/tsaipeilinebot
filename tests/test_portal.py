import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
import portal_routes
from fastapi.testclient import TestClient


class PortalPageTests(unittest.TestCase):
    """/portal 現在改成登入後才看得到（見 login_routes.py），未登入一律導去
    /login，不會洩漏頁面內容。已登入才看得到的卡片內容（依權限篩選、職缺
    系統銜接）需要真的有 Firestore 上的帳號，留給有 GCP 憑證的環境做整合
    測試，跟 test_accounts_routes.py 的既有分工一致。這裡只確認：未登入時
    正確導去登入頁、沒有動到既有的健康檢查路由（/）。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_health_check_unaffected(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_portal_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/portal", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/portal")

    def test_job_system_login_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/portal/job-system-login", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/portal")


class JobSystemSsoExchangeTests(unittest.TestCase):
    """/api/job-system-sso/exchange 是職缺系統的網頁自己在背景呼叫的端點，
    刻意不要求我們平台的登入 session（見 portal_routes.py 的說明），所以
    可以直接測，不需要模擬登入。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_missing_token_is_rejected(self):
        resp = self.client.get("/api/job-system-sso/exchange")
        self.assertEqual(resp.status_code, 404)

    def test_garbage_token_is_rejected(self):
        resp = self.client.get("/api/job-system-sso/exchange", params={"token": "not-a-real-token"})
        self.assertEqual(resp.status_code, 404)

    def test_valid_token_roundtrips_and_sets_cors_header(self):
        import job_portal_sso

        token = job_portal_sso.mint_sso_token("王小明", "1234")
        resp = self.client.get("/api/job-system-sso/exchange", params={"token": token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"name": "王小明", "pin": "1234"})
        self.assertEqual(
            resp.headers["access-control-allow-origin"],
            job_portal_sso.ALLOWED_EXCHANGE_ORIGIN,
        )


class SyncJobSystemIdentitiesEndpointTests(unittest.TestCase):
    """/internal/sync-job-system-identities 比照 /internal/load-test-message
    的密鑰保護作法：沒帶對 header 一律 403，不會真的去打 Google Sheets API。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_forbidden_without_secret_configured(self):
        resp = self.client.post("/internal/sync-job-system-identities")
        self.assertEqual(resp.status_code, 403)

    def test_forbidden_with_wrong_secret(self):
        import job_portal_sso

        original = job_portal_sso.SYNC_TRIGGER_SECRET
        job_portal_sso.SYNC_TRIGGER_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/internal/sync-job-system-identities",
                headers={"X-Job-Sheet-Sync-Secret": "wrong-secret"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            job_portal_sso.SYNC_TRIGGER_SECRET = original


class RequireLoginDependencyTests(unittest.TestCase):
    """portal_routes._require_login() 是 /portal 系列路由共用的登入檢查，
    直接單元測試回傳值，不用真的透過 TestClient 跑一次 HTTP。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireLoginDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_returns_redirect(self):
        result = portal_routes._require_login(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/portal")

    def test_with_session_returns_none(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = portal_routes._require_login(self._FakeRequest(account))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
