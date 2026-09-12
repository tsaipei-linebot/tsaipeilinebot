import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.report_accounts_for_rank_setup import build_report


def _fake_doc(doc_id, modules):
    doc = mock.Mock()
    doc.id = doc_id
    doc.to_dict.return_value = {"modules": modules}
    return doc


class BuildReportTests(unittest.TestCase):
    """這支腳本純唯讀，用來幫使用者找出「改版前是主管、還沒設定新職級」
    的帳號——關鍵是要讀 Firestore 原始文件裡舊格式的角色值，不能透過
    `list_accounts()` 正規化過的資料（那已經看不出原本是不是 admin）。"""

    def test_legacy_admin_of_admin_aware_module_is_flagged(self):
        accounts = [
            {"username": "alice", "name": "Alice", "department": "管理部", "rank": "",
             "modules": ["delivery"], "is_platform_admin": False},
        ]
        with mock.patch("platform_accounts.list_accounts", return_value=accounts):
            with mock.patch(
                "scripts.report_accounts_for_rank_setup.users_ref",
                return_value=mock.Mock(stream=mock.Mock(return_value=[_fake_doc("alice", {"delivery": "admin"})])),
            ):
                report = build_report()
        self.assertEqual(report[0]["was_admin_of"], ["新北所(配送組)系統"])

    def test_legacy_staff_is_not_flagged(self):
        accounts = [
            {"username": "bob", "name": "Bob", "department": "管理部", "rank": "",
             "modules": ["delivery"], "is_platform_admin": False},
        ]
        with mock.patch("platform_accounts.list_accounts", return_value=accounts):
            with mock.patch(
                "scripts.report_accounts_for_rank_setup.users_ref",
                return_value=mock.Mock(stream=mock.Mock(return_value=[_fake_doc("bob", {"delivery": "staff"})])),
            ):
                report = build_report()
        self.assertEqual(report[0]["was_admin_of"], [])

    def test_module_without_admin_concept_is_ignored_even_if_marked_admin(self):
        # project_contracts 不在 _ADMIN_AWARE_MODULES 裡（本來就沒有角色
        # 區分），就算舊資料裡意外寫了 "admin" 也不該被列出來。
        accounts = [
            {"username": "carol", "name": "Carol", "department": "業務部", "rank": "",
             "modules": ["project_contracts"], "is_platform_admin": False},
        ]
        with mock.patch("platform_accounts.list_accounts", return_value=accounts):
            with mock.patch(
                "scripts.report_accounts_for_rank_setup.users_ref",
                return_value=mock.Mock(stream=mock.Mock(return_value=[_fake_doc("carol", {"project_contracts": "admin"})])),
            ):
                report = build_report()
        self.assertEqual(report[0]["was_admin_of"], [])

    def test_platform_admin_and_rank_pass_through(self):
        accounts = [
            {"username": "boss", "name": "老闆", "department": "", "rank": "",
             "modules": [], "is_platform_admin": True},
        ]
        with mock.patch("platform_accounts.list_accounts", return_value=accounts):
            with mock.patch(
                "scripts.report_accounts_for_rank_setup.users_ref",
                return_value=mock.Mock(stream=mock.Mock(return_value=[_fake_doc("boss", {})])),
            ):
                report = build_report()
        self.assertTrue(report[0]["is_platform_admin"])
        self.assertEqual(report[0]["department"], "（尚未設定）")
        self.assertEqual(report[0]["rank"], "（尚未設定）")


if __name__ == "__main__":
    unittest.main()
