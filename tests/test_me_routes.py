import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import main
import me_routes
from fastapi.testclient import TestClient


class MeRoutingSmokeTests(unittest.TestCase):
    """/me 是登入後每個帳號都自動有的個人化頁面，跟 test_portal.py／
    test_accounts_routes.py 的既有分工一致，只涵蓋不需要真的打 Firestore／
    Google Sheets API 的部分：未登入時的導向。已登入才看得到的內容（依
    申請人/主管邏輯篩選）留給有 GCP 憑證的環境做整合測試。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/me", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/me")

    def test_new_salary_repayment_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/me/salary-repayment/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/me")

    def test_create_salary_repayment_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/me/salary-repayment/new", data={"name": "李小華"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/me")


class RequireLoginDependencyTests(unittest.TestCase):
    """me_routes._require_login() 是 /me 的登入檢查，直接單元測試回傳值，
    不用真的透過 TestClient 跑一次 HTTP（跟 test_portal.py 的
    RequireLoginDependencyTests 是同一種寫法）。這裡沒有模組權限判斷——任何
    登入的帳號都能打開 /me，資料才依申請人/主管邏輯個人化篩選。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireLoginDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = me_routes._require_login(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/me")

    def test_with_session_returns_none(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = me_routes._require_login(self._FakeRequest(account))
        self.assertIsNone(result)


class AmountsFromFormTests(unittest.TestCase):
    """_amounts_from_form() 把加項/扣項固定十個欄位（名稱照抄現有 Netlify
    表單畫面，見 services/salary_repayment_submit_service.py 的
    EARNING_FIELDS/DEDUCTION_FIELDS）組成 {中文名稱: 金額} 字典，0 或空白
    或打錯的欄位不列進字典（該項目本來就是選填，同仁不用每格都填 0）。"""

    class _FakeForm(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    def test_only_nonzero_fields_included(self):
        form = self._FakeForm({"earning_hours": "500", "earning_salary": "0"})
        result = me_routes._amounts_from_form(form, me_routes.EARNING_FIELDS, "earning")
        self.assertEqual(result, {"工時/天數": 500.0})

    def test_blank_field_treated_as_zero(self):
        form = self._FakeForm({"earning_hours": ""})
        result = me_routes._amounts_from_form(form, me_routes.EARNING_FIELDS, "earning")
        self.assertEqual(result, {})

    def test_non_numeric_field_treated_as_zero(self):
        form = self._FakeForm({"earning_hours": "不是數字"})
        result = me_routes._amounts_from_form(form, me_routes.EARNING_FIELDS, "earning")
        self.assertEqual(result, {})

    def test_missing_fields_default_to_zero(self):
        result = me_routes._amounts_from_form(self._FakeForm(), me_routes.EARNING_FIELDS, "earning")
        self.assertEqual(result, {})

    def test_deduction_fields_use_deduction_prefix(self):
        form = self._FakeForm({"deduction_labor_insurance": "300"})
        result = me_routes._amounts_from_form(form, me_routes.DEDUCTION_FIELDS, "deduction")
        self.assertEqual(result, {"勞保費": 300.0})


if __name__ == "__main__":
    unittest.main()
