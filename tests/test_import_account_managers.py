import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from scripts.import_account_managers import plan_import


def _account(username, name, manager_usernames=None):
    return {"username": username, "name": name, "manager_usernames": manager_usernames or []}


class PlanImportTests(unittest.TestCase):
    """一次性匯入腳本的規劃邏輯（不碰 Firestore／Sheets API），確保：姓名
    比對正確、對不上的姓名跟同名同姓的情況會被清楚回報而不是亂猜。"""

    def test_matches_employee_and_manager_by_name(self):
        org_rows = [{"員工姓名": "王小明", "主管姓名": "李小華"}]
        accounts = [_account("wang", "王小明"), _account("li", "李小華")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(
            plan["updates"],
            [{"username": "wang", "name": "王小明", "manager_usernames": ["li"], "manager_names": ["李小華"]}],
        )
        self.assertEqual(plan["unmatched_manager_names"], [])
        self.assertEqual(plan["unmatched_employee_names"], [])

    def test_multiple_managers_comma_separated(self):
        org_rows = [{"員工姓名": "王小明", "主管姓名": "李小華,張大同"}]
        accounts = [_account("wang", "王小明"), _account("li", "李小華"), _account("chang", "張大同")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"][0]["manager_usernames"], ["li", "chang"])

    def test_self_referencing_manager_is_dropped_not_treated_as_own_manager(self):
        org_rows = [{"員工姓名": "李小華", "主管姓名": "李小華"}]
        accounts = [_account("li", "李小華")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"], [{"username": "li", "name": "李小華", "manager_usernames": [], "manager_names": []}])

    def test_employee_with_no_matching_account_is_reported_and_skipped(self):
        org_rows = [{"員工姓名": "查無此人", "主管姓名": "李小華"}]
        accounts = [_account("li", "李小華")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"], [])
        self.assertEqual(plan["unmatched_employee_names"], ["查無此人"])

    def test_manager_with_no_matching_account_is_reported_and_dropped(self):
        org_rows = [{"員工姓名": "王小明", "主管姓名": "查無此主管"}]
        accounts = [_account("wang", "王小明")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"][0]["manager_usernames"], [])
        self.assertEqual(plan["unmatched_manager_names"], ["查無此主管"])

    def test_duplicate_employee_names_are_reported_and_skipped_entirely(self):
        org_rows = [{"員工姓名": "王小明", "主管姓名": ""}]
        accounts = [_account("wang1", "王小明"), _account("wang2", "王小明")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"], [])
        self.assertEqual(plan["duplicate_names"], ["王小明"])

    def test_duplicate_manager_name_is_reported_and_not_guessed(self):
        org_rows = [{"員工姓名": "王小明", "主管姓名": "李小華"}]
        accounts = [_account("wang", "王小明"), _account("li1", "李小華"), _account("li2", "李小華")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"][0]["manager_usernames"], [])
        self.assertTrue(any("李小華" in name for name in plan["unmatched_manager_names"]))

    def test_row_with_blank_employee_name_is_ignored(self):
        org_rows = [{"員工姓名": "", "主管姓名": "李小華"}]
        accounts = [_account("li", "李小華")]
        plan = plan_import(org_rows, accounts)
        self.assertEqual(plan["updates"], [])
        self.assertEqual(plan["unmatched_employee_names"], [])


if __name__ == "__main__":
    unittest.main()
