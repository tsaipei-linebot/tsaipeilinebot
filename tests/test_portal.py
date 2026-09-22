import os
import sys
import time
import unittest
from unittest import mock

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


class AnnouncementRoutingSmokeTests(unittest.TestCase):
    """/announcements 是全平台管理員專用的公告管理頁面（2026-09-18 新增），
    跟 test_accounts_routes.py 的既有分工一致：只涵蓋不需要真的打 Firestore
    的部分（未登入時的導向），需要模擬「已登入且是全平台管理員」才能測到
    的頁面內容留給有 GCP 憑證的環境做整合測試。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_announcements_page_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/announcements", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_create_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.post("/announcements/new", data={"title": "x"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))


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


class AutoPublishAnnouncementEndpointTests(unittest.TestCase):
    """/internal/announcements/auto-publish（2026-09-19 新增）：部署後由
    GitHub Actions 呼叫，密鑰保護作法比照 /internal/sync-job-system-
    identities，沒帶對 header 一律 403，不會真的去打 Firestore。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_forbidden_without_secret_configured(self):
        resp = self.client.post(
            "/internal/announcements/auto-publish", data={"title": "x", "content": "y"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_forbidden_with_wrong_secret(self):
        original = portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET
        portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/internal/announcements/auto-publish",
                data={"title": "x", "content": "y"},
                headers={"X-Auto-Announce-Secret": "wrong-secret"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = original

    def test_missing_title_is_rejected(self):
        original = portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET
        portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = "correct-secret"
        try:
            resp = self.client.post(
                "/internal/announcements/auto-publish",
                data={"title": "  ", "content": "y"},
                headers={"X-Auto-Announce-Secret": "correct-secret"},
            )
            self.assertEqual(resp.status_code, 400)
        finally:
            portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = original

    def test_correct_secret_creates_announcement(self):
        original = portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET
        portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = "correct-secret"
        try:
            with mock.patch.object(
                portal_routes.platform_announcements, "create_announcement", return_value="ann1"
            ) as mock_create:
                resp = self.client.post(
                    "/internal/announcements/auto-publish",
                    data={"title": "系統更新：測試功能", "content": "測試內容"},
                    headers={"X-Auto-Announce-Secret": "correct-secret"},
                )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"id": "ann1"})
            mock_create.assert_called_once_with("系統更新：測試功能", "測試內容", created_by="system")
        finally:
            portal_routes.platform_announcements.AUTO_ANNOUNCE_SECRET = original


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


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user


def _admin_account():
    return {"username": "boss", "name": "老闆", "modules": [], "is_platform_admin": True}


class PortalHomeAnnouncementTests(unittest.TestCase):
    """/portal 首頁把全公司公告（附加顯示用日期）傳給樣板，任何登入帳號
    看到的都是同一份，不像卡片本身要依模組權限篩選（2026-09-18 新增）。"""

    def test_passes_active_announcements_with_display_date(self):
        now = time.time()
        announcements = [
            {"id": "a", "title": "標題", "content": "說明", "active": True, "created_at": now, "expires_at": now + 100}
        ]
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=announcements):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                portal_routes.portal_home(_FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["announcements"][0]["title"], "標題")
        self.assertIn("created_at_display", context["announcements"][0])


class PortalHomeHelpLinkTests(unittest.TestCase):
    """/portal 卡片的「使用說明」按鈕（2026-09-18 新增，2026-09-18 擴大到
    全部模組）：每個寫好說明頁的模組卡片都帶對應的 help_href。"""

    def _cards(self):
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=[]):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                portal_routes.portal_home(_FakeRequest(_admin_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        return {c["name"]: c for c in context["cards"]}

    def test_all_modules_have_help_href(self):
        cards = self._cards()
        expected = {
            "新北所(配送組)專區": "/delivery/help",
            "管理部": "/management/help",
            "人資專區": "/hr/help",
            "少凱業務開發專區": "/salesdev/help",
            "職缺維護": "/job-listings/help",
            "專案合約維護": "/project-contracts/help",
            "小雞點數自費申請": "/chicken-points/help",
            "派遣契約產生器": "/dispatch-contracts/help",
            "合約產生器": "/client-contracts/help",
            "桃園所專區": "/dispatch/taoyuan/help",
            "高雄所專區": "/dispatch/kaohsiung/help",
            "財務部專區": "/finance/help",
        }
        for name, help_href in expected.items():
            self.assertEqual(cards[name]["help_href"], help_href, name)

    def test_insurance_department_card_has_help_href(self):
        """全平台管理員不會看到 7 個部門的獨立加退保卡片（見
        PortalHomeInsuranceCardTests 的說明），要用真的符合部門的帳號
        才測得到這張卡片的 help_href。"""
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=[]):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                account = {"username": "gina", "name": "Gina", "department": "台北所(派遣組)", "is_platform_admin": False, "modules": []}
                portal_routes.portal_home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        cards = {c["name"]: c for c in context["cards"]}
        self.assertEqual(cards["台北所(派遣組)專區"]["help_href"], "/hr/insurance/help")


class PortalHomeDispatchSiteCardTests(unittest.TestCase):
    """多所派遣媒合卡片（2026-09-21 新增桃園所，2026-09-22 重構成多所
    共用＋新增高雄所）：不掛進 platform_accounts.MODULES，照部門判斷是否
    顯示，跟其他模組卡片的權限來源不一樣，另外測。"""

    def _cards(self, account):
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=[]):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                portal_routes.portal_home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        return {c["name"]: c for c in context["cards"]}

    def test_shows_for_taoyuan_department(self):
        account = {"username": "alice", "name": "Alice", "department": "桃園所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("桃園所專區", cards)
        self.assertEqual(cards["桃園所專區"]["href"], "/dispatch/taoyuan")
        self.assertNotIn("高雄所專區", cards)

    def test_shows_for_kaohsiung_department(self):
        account = {"username": "carol", "name": "Carol", "department": "高雄所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("高雄所專區", cards)
        self.assertEqual(cards["高雄所專區"]["href"], "/dispatch/kaohsiung")
        self.assertNotIn("桃園所專區", cards)

    def test_hidden_for_other_department(self):
        account = {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertNotIn("桃園所專區", cards)
        self.assertNotIn("高雄所專區", cards)

    def test_shown_for_platform_admin_regardless_of_department(self):
        """全平台管理員每個所都看得到，不是只挑一個。"""
        cards = self._cards(_admin_account())
        self.assertIn("桃園所專區", cards)
        self.assertIn("高雄所專區", cards)


class PortalHomeFinanceCardTests(unittest.TestCase):
    """財務部專區卡片（2026-09-22 新增）：跟人資部門/桃園所/高雄所同一套
    「部門字串比對」權限來源，另外測。"""

    def _cards(self, account):
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=[]):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                portal_routes.portal_home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        return {c["name"]: c for c in context["cards"]}

    def test_shows_for_finance_department(self):
        account = {"username": "carol", "name": "Carol", "department": "財務部", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("財務部專區", cards)
        self.assertEqual(cards["財務部專區"]["href"], "/finance")

    def test_hidden_for_other_department(self):
        account = {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertNotIn("財務部專區", cards)

    def test_shown_for_platform_admin_regardless_of_department(self):
        cards = self._cards(_admin_account())
        self.assertIn("財務部專區", cards)


class PortalHomeInsuranceCardTests(unittest.TestCase):
    """每日加退保部門卡片（2026-09-22 新增，同日稍晚再改成跟使用者確認的
    「{部門}專區」命名，並把新北所(配送組)/桃園所/高雄所這 3 個原本就有
    其他系統的部門併掉獨立卡片，見 portal_routes.py 這段的說明）：跟
    財務部/桃園所/高雄所同一套「部門字串比對」權限來源，但刻意不給全
    平台管理員例外——管理員自己的部門通常是空的，這張卡片點進去是靠
    帳號自己的 department 決定要看哪個部門，硬顯示只會變成點不進去的
    空卡片。用 href 判斷是不是這張獨立卡片，不是用卡片名稱後綴，因為
    「專區」這個後綴現在其他卡片（delivery/dispatch）也會用到。"""

    _INSURANCE_HREF = "/hr/insurance/upload"

    def _cards(self, account):
        with mock.patch.object(portal_routes.platform_announcements, "list_active_announcements", return_value=[]):
            with mock.patch.object(portal_routes, "templates") as mock_templates:
                portal_routes.portal_home(_FakeRequest(account), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        return {c["name"]: c for c in context["cards"]}

    def _insurance_card_names(self, cards):
        return [name for name, card in cards.items() if card["href"] == self._INSURANCE_HREF]

    def test_shows_for_upload_department_without_other_system(self):
        account = {"username": "gina", "name": "Gina", "department": "台北所(派遣組)", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("台北所(派遣組)專區", cards)
        self.assertEqual(cards["台北所(派遣組)專區"]["href"], self._INSURANCE_HREF)

    def test_shows_for_department_with_parentheses(self):
        account = {"username": "ivy", "name": "Ivy", "department": "台北所(國際組)", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("台北所(國際組)專區", cards)

    def test_hidden_for_unrelated_department(self):
        account = {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertEqual(self._insurance_card_names(cards), [])

    def test_hidden_for_insurance_collector_department(self):
        """人資部門自己不算「上傳部門」，不會多一張加退保卡片——他們是
        走「人資專區」進去看彙總，不是走部門卡片上傳。"""
        account = {"username": "hana", "name": "Hana", "department": "人資部門", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertEqual(self._insurance_card_names(cards), [])

    def test_not_shown_for_platform_admin(self):
        """刻意跟桃園所/高雄所/財務部卡片不同：管理員不會看到部門卡片
        （見上面 class docstring 的說明）。"""
        cards = self._cards(_admin_account())
        self.assertEqual(self._insurance_card_names(cards), [])

    def test_taoyuan_department_shows_dispatch_card_not_standalone_insurance_card(self):
        """桃園所這個上傳部門已經有自己的派遣媒合專區卡片，加退保併進
        那張卡片裡當一個按鈕（見 templates/dispatch_home.html），/portal
        不會多顯示一張獨立的「桃園所專區」加退保卡片。"""
        account = {"username": "dora", "name": "Dora", "department": "桃園所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("桃園所專區", cards)
        self.assertEqual(cards["桃園所專區"]["href"], "/dispatch/taoyuan")
        self.assertEqual(self._insurance_card_names(cards), [])

    def test_kaohsiung_department_shows_dispatch_card_not_standalone_insurance_card(self):
        account = {"username": "carol", "name": "Carol", "department": "高雄所", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("高雄所專區", cards)
        self.assertEqual(cards["高雄所專區"]["href"], "/dispatch/kaohsiung")
        self.assertEqual(self._insurance_card_names(cards), [])

    def test_delivery_department_shows_delivery_card_not_standalone_insurance_card(self):
        """新北所(配送組)同樣併進配送部系統首頁的按鈕，不是獨立卡片；
        帳號沒有勾「新北所(配送組)專區」模組權限，靠部門字串也能看到
        （見 delivery/auth.py 的 has_delivery_access()）。"""
        account = {"username": "fay", "name": "Fay", "department": "新北所(配送組)", "is_platform_admin": False, "modules": []}
        cards = self._cards(account)
        self.assertIn("新北所(配送組)專區", cards)
        self.assertEqual(cards["新北所(配送組)專區"]["href"], "/delivery/login")
        self.assertEqual(self._insurance_card_names(cards), [])


class AnnouncementAdminRoutesTests(unittest.TestCase):
    """公告管理路由（限全平台管理員）：直接呼叫路由函式，跳過
    require_platform_admin 依賴（redirect=None 等同已通過檢查），
    跟 test_accounts_routes.py 的既有測試風格一致。"""

    def test_create_calls_platform_announcements(self):
        with mock.patch.object(portal_routes.platform_announcements, "create_announcement") as mock_create:
            resp = portal_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="標題", content="說明", days=7, redirect=None
            )
        mock_create.assert_called_once_with("標題", "說明", created_by="boss", days=7)
        self.assertEqual(resp.status_code, 303)

    def test_blank_title_is_ignored(self):
        with mock.patch.object(portal_routes.platform_announcements, "create_announcement") as mock_create:
            portal_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="   ", content="說明", days=7, redirect=None
            )
        mock_create.assert_not_called()

    def test_non_positive_days_falls_back_to_default(self):
        with mock.patch.object(portal_routes.platform_announcements, "create_announcement") as mock_create:
            portal_routes.create_announcement_submit(
                _FakeRequest(_admin_account()), title="標題", content="", days=0, redirect=None
            )
        mock_create.assert_called_once_with("標題", "", created_by="boss", days=portal_routes.ANNOUNCEMENT_DEFAULT_DAYS)

    def test_toggle_active_calls_platform_announcements(self):
        with mock.patch.object(portal_routes.platform_announcements, "set_announcement_active") as mock_set:
            resp = portal_routes.toggle_announcement_active(
                "a", _FakeRequest(_admin_account()), active="0", redirect=None
            )
        mock_set.assert_called_once_with("a", False)
        self.assertEqual(resp.status_code, 303)

    def test_delete_calls_platform_announcements(self):
        with mock.patch.object(portal_routes.platform_announcements, "delete_announcement", return_value=True) as mock_delete:
            resp = portal_routes.delete_announcement_submit("a", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_called_once_with("a")
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
