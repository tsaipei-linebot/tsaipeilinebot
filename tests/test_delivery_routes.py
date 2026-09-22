import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import delivery.auth as delivery_auth
import main
from fastapi.testclient import TestClient


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user


def _module_ticked_account():
    return {"username": "grace", "name": "Grace", "department": "管理部", "modules": ["delivery"], "is_platform_admin": False}


def _department_only_account():
    return {"username": "fay", "name": "Fay", "department": "新北所(配送組)", "modules": [], "is_platform_admin": False}


def _unrelated_account():
    return {"username": "bob", "name": "Bob", "department": "新北所", "modules": [], "is_platform_admin": False}


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

    def test_help_page_redirects_to_login_when_not_authenticated(self):
        """使用說明頁（2026-09-18 新增）走跟主頁同一組 login_required，
        跟 /portal 卡片顯不顯示「使用說明」按鈕是同一組權限判斷。"""
        resp = self.client.get("/delivery/help", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/delivery/login"))

    def test_login_page_renders(self):
        resp = self.client.get("/delivery/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("新北所(配送組)系統", resp.text)

    def test_static_css_is_served(self):
        resp = self.client.get("/delivery/static/style.css")
        self.assertEqual(resp.status_code, 200)

    def test_recruitment_bot_routes_still_work_unaffected(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.json())


class HasDeliveryAccessTests(unittest.TestCase):
    """has_delivery_access()（2026-09-22 新增，見 delivery/auth.py 開頭的
    說明）：模組打勾 or 帳號部門是「新北所(配送組)」，兩者符合其一即可，
    不再只認模組打勾——跟財務部/桃園所/高雄所/加退保同一套「部門字串
    比對」做法。"""

    def test_module_ticked_account_allowed(self):
        self.assertTrue(delivery_auth.has_delivery_access(_module_ticked_account()))

    def test_department_only_account_allowed(self):
        self.assertTrue(delivery_auth.has_delivery_access(_department_only_account()))

    def test_unrelated_account_denied(self):
        self.assertFalse(delivery_auth.has_delivery_access(_unrelated_account()))

    def test_fullwidth_parens_in_department_still_matches(self):
        account = {"username": "ivy", "name": "Ivy", "department": "新北所（配送組）", "modules": [], "is_platform_admin": False}
        self.assertTrue(delivery_auth.has_delivery_access(account))

    def test_no_account_denied(self):
        self.assertFalse(delivery_auth.has_delivery_access(None))


class DeliveryLoginRequiredDependencyTests(unittest.TestCase):
    """login_required()：沒登入導去 /delivery/login；登入了但兩種存取
    方式都不符合導回 /portal；符合其一就放行——跟 finance_routes._require_access()
    等其他「部門字串比對」路由同一種寫法。"""

    def test_no_session_redirects_to_delivery_login(self):
        result = delivery_auth.login_required(_FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/delivery/login")

    def test_unrelated_department_redirects_to_portal(self):
        result = delivery_auth.login_required(_FakeRequest(_unrelated_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_department_only_account_allowed_through(self):
        result = delivery_auth.login_required(_FakeRequest(_department_only_account()))
        self.assertIsNone(result)

    def test_module_ticked_account_allowed_through(self):
        result = delivery_auth.login_required(_FakeRequest(_module_ticked_account()))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
