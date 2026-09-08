import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from services import salary_repayment_service as svc


class RowsToDictsTests(unittest.TestCase):
    def test_empty_values_returns_empty_list(self):
        self.assertEqual(svc.rows_to_dicts([]), [])

    def test_header_only_returns_empty_list(self):
        self.assertEqual(svc.rows_to_dicts([["姓名", "主管"]]), [])

    def test_short_row_padded_with_empty_strings(self):
        rows = [["姓名", "主管"], ["王小明"]]
        self.assertEqual(svc.rows_to_dicts(rows), [{"姓名": "王小明", "主管": ""}])


class ParseNameListTests(unittest.TestCase):
    def test_splits_and_strips_comma_separated_names(self):
        self.assertEqual(svc.parse_name_list("王小明, 李小華"), ["王小明", "李小華"])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(svc.parse_name_list(""), [])

    def test_single_name_returns_one_item_list(self):
        self.assertEqual(svc.parse_name_list("王小明"), ["王小明"])


class BuildManagerLookupFromAccountsTests(unittest.TestCase):
    """主管關係現在讀系統帳號的 manager_usernames 欄位（在 /accounts 網頁上
    設定），不再讀外部試算表的「主管姓名」文字欄位——這裡驗證帳號資料轉換
    成姓名清單的邏輯正確。"""

    def test_maps_employee_to_manager_names(self):
        accounts = [
            {"username": "wang", "name": "王小明", "manager_usernames": ["li", "chang"]},
            {"username": "li", "name": "李小華", "manager_usernames": []},
            {"username": "chang", "name": "張大同", "manager_usernames": []},
        ]
        self.assertEqual(
            svc.build_manager_lookup_from_accounts(accounts),
            {"王小明": ["李小華", "張大同"], "李小華": [], "張大同": []},
        )

    def test_manager_username_with_no_matching_account_is_dropped(self):
        accounts = [{"username": "wang", "name": "王小明", "manager_usernames": ["ghost"]}]
        self.assertEqual(svc.build_manager_lookup_from_accounts(accounts), {"王小明": []})

    def test_missing_manager_usernames_key_defaults_to_empty(self):
        accounts = [{"username": "wang", "name": "王小明"}]
        self.assertEqual(svc.build_manager_lookup_from_accounts(accounts), {"王小明": []})


class BuildLineIdNameLookupTests(unittest.TestCase):
    def test_maps_line_id_to_name(self):
        org_rows = [{"員工姓名": "王小明", "員工 LINE ID": "Uabc123"}]
        self.assertEqual(svc.build_line_id_name_lookup(org_rows), {"Uabc123": "王小明"})

    def test_row_missing_line_id_is_skipped(self):
        org_rows = [{"員工姓名": "王小明", "員工 LINE ID": ""}]
        self.assertEqual(svc.build_line_id_name_lookup(org_rows), {})


class FilterVisibleRecordsTests(unittest.TestCase):
    def setUp(self):
        self.manager_lookup = {"王小明": ["李小華", "張大同"], "陳大文": ["張大同"]}
        self.records = [
            {"申請人姓名": "王小明", "備註": "own"},
            {"申請人姓名": "陳大文", "備註": "managed-by-shared-manager"},
            {"申請人姓名": "不相關的人", "備註": "unrelated"},
        ]

    def test_own_submission_is_visible(self):
        visible = svc.filter_visible_records(self.records, self.manager_lookup, "王小明")
        self.assertEqual([r["備註"] for r in visible], ["own"])

    def test_manager_sees_subordinates_submission(self):
        visible = svc.filter_visible_records(self.records, self.manager_lookup, "張大同")
        self.assertEqual(
            sorted(r["備註"] for r in visible),
            ["managed-by-shared-manager", "own"],
        )

    def test_unrelated_viewer_sees_nothing(self):
        visible = svc.filter_visible_records(self.records, self.manager_lookup, "路人甲")
        self.assertEqual(visible, [])

    def test_record_with_blank_applicant_is_ignored(self):
        records = [{"申請人姓名": "", "備註": "blank"}]
        visible = svc.filter_visible_records(records, self.manager_lookup, "王小明")
        self.assertEqual(visible, [])


class ResolveApproverNameTests(unittest.TestCase):
    def test_resolves_known_line_id_to_name(self):
        record = {"核准主管": "Uabc123"}
        lookup = {"Uabc123": "李小華"}
        self.assertEqual(svc._resolve_approver_name(record, lookup), "李小華")

    def test_unknown_line_id_falls_back_to_raw_value(self):
        record = {"核准主管": "Uunknown"}
        self.assertEqual(svc._resolve_approver_name(record, {}), "Uunknown")

    def test_blank_approver_returns_empty_string(self):
        self.assertEqual(svc._resolve_approver_name({"核准主管": ""}, {}), "")


class GetMyRepaymentRecordsTests(unittest.TestCase):
    """實際打 Google Sheets API 的路徑需要真的 ADC，留給有 GCP 憑證的環境做
    整合測試（跟 factory_watch_service／salesdev_sheet_service 的既有分工
    一致）。這裡只測試不需要網路連線就能確定行為的部分：試算表 ID 沒設定
    時要回傳清楚的中文錯誤，而不是讓例外炸出去變成 500 錯誤頁。"""

    def test_returns_friendly_error_when_sheet_id_not_configured(self):
        original = svc.SALARY_REPAYMENT_SHEET_ID
        svc.SALARY_REPAYMENT_SHEET_ID = ""
        try:
            records, error = svc.get_my_repayment_records("王小明")
        finally:
            svc.SALARY_REPAYMENT_SHEET_ID = original
        self.assertEqual(records, [])
        self.assertIn("SALARY_REPAYMENT_SHEET_ID", error)


if __name__ == "__main__":
    unittest.main()
