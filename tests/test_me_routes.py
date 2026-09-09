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


class EarningsAndDeductionsFromFormTests(unittest.TestCase):
    """_earnings_and_deductions_from_form() 把表單裡動態新增的加項/扣項列
    （同一個 name 出現多次）組成 {名稱: 金額} 字典，見
    templates/salary_repayment_form.html 的「+ 新增一筆」按鈕。"""

    class _FakeForm(dict):
        def getlist(self, key):
            return dict.get(self, key, [])

    def test_pairs_labels_with_amounts(self):
        form = self._FakeForm(
            earning_label=["加班費", "獎金"],
            earning_amount=["500", "1000"],
            deduction_label=["誤餐費"],
            deduction_amount=["100"],
        )
        earnings, deductions = me_routes._earnings_and_deductions_from_form(form)
        self.assertEqual(earnings, {"加班費": 500.0, "獎金": 1000.0})
        self.assertEqual(deductions, {"誤餐費": 100.0})

    def test_blank_label_row_is_skipped(self):
        form = self._FakeForm(earning_label=["", "獎金"], earning_amount=["999", "1000"])
        earnings, _ = me_routes._earnings_and_deductions_from_form(form)
        self.assertEqual(earnings, {"獎金": 1000.0})

    def test_non_numeric_amount_row_is_skipped(self):
        form = self._FakeForm(earning_label=["加班費"], earning_amount=["不是數字"])
        earnings, _ = me_routes._earnings_and_deductions_from_form(form)
        self.assertEqual(earnings, {})

    def test_no_rows_returns_empty_dicts(self):
        earnings, deductions = me_routes._earnings_and_deductions_from_form(self._FakeForm())
        self.assertEqual(earnings, {})
        self.assertEqual(deductions, {})


if __name__ == "__main__":
    unittest.main()
