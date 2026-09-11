import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import platform_accounts
from platform_accounts import (
    ROLE_ADMIN,
    ROLE_STAFF,
    has_module_access,
    hash_password,
    module_role,
    validate_account_deletion,
    verify_password,
)


class PasswordHashingTests(unittest.TestCase):
    def test_correct_password_verifies(self):
        stored = hash_password("hello-world-123")
        self.assertTrue(verify_password("hello-world-123", stored))

    def test_wrong_password_fails(self):
        stored = hash_password("hello-world-123")
        self.assertFalse(verify_password("wrong-password", stored))

    def test_same_password_hashes_differently_each_time(self):
        self.assertNotEqual(hash_password("same-password"), hash_password("same-password"))

    def test_malformed_stored_hash_returns_false_instead_of_raising(self):
        self.assertFalse(verify_password("anything", "not-a-valid-hash"))
        self.assertFalse(verify_password("anything", ""))
        self.assertFalse(verify_password("anything", None))


class ModuleRoleTests(unittest.TestCase):
    """一個帳號可能同時橫跨好幾個部門模組，每個模組各自的角色（主管/專員）
    分開存在 modules 這個 dict 裡；全平台管理員（is_platform_admin）視同
    任何模組的管理員。"""

    def test_no_account_has_no_access(self):
        self.assertIsNone(module_role(None, "delivery"))
        self.assertFalse(has_module_access(None, "delivery"))

    def test_account_without_module_has_no_access(self):
        account = {"modules": {"management": "admin"}, "is_platform_admin": False}
        self.assertIsNone(module_role(account, "delivery"))
        self.assertFalse(has_module_access(account, "delivery"))

    def test_account_with_staff_role_in_one_module(self):
        account = {"modules": {"delivery": "staff"}, "is_platform_admin": False}
        self.assertEqual(module_role(account, "delivery"), ROLE_STAFF)
        self.assertTrue(has_module_access(account, "delivery"))

    def test_account_with_admin_role_in_one_module_only(self):
        account = {"modules": {"delivery": "admin", "management": "staff"}, "is_platform_admin": False}
        self.assertEqual(module_role(account, "delivery"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "management"), ROLE_STAFF)

    def test_platform_admin_is_admin_of_every_module_even_without_explicit_entry(self):
        account = {"modules": {}, "is_platform_admin": True}
        self.assertEqual(module_role(account, "delivery"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "management"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "some_future_module"), ROLE_ADMIN)


class ToAccountTests(unittest.TestCase):
    """帳號資料多了 manager_usernames／department 兩個欄位（在 /accounts
    網頁上設定），確認舊資料（沒有這兩個欄位）讀出來會是安全的預設值，不會
    噴 KeyError。"""

    def test_defaults_manager_usernames_and_department_when_missing(self):
        account = platform_accounts._to_account("alice", {"name": "Alice"})
        self.assertEqual(account["manager_usernames"], [])
        self.assertEqual(account["department"], "")

    def test_preserves_manager_usernames_and_department_when_present(self):
        account = platform_accounts._to_account(
            "alice", {"name": "Alice", "manager_usernames": ["bob"], "department": "業務部"}
        )
        self.assertEqual(account["manager_usernames"], ["bob"])
        self.assertEqual(account["department"], "業務部")

    def test_defaults_sort_index_to_none_when_missing(self):
        account = platform_accounts._to_account("alice", {"name": "Alice"})
        self.assertIsNone(account["sort_index"])

    def test_preserves_sort_index_when_present(self):
        account = platform_accounts._to_account("alice", {"name": "Alice", "sort_index": 3})
        self.assertEqual(account["sort_index"], 3)


class ListAccountsSortingTests(unittest.TestCase):
    """帳號權限管理頁面依部門分組顯示，同部門內先看拖曳排過序的
    （sort_index 由小到大），再看還沒排過序的（依姓名排在後面）——見
    list_accounts() 的說明。"""

    def _fake_doc(self, doc_id, data):
        doc = mock.Mock()
        doc.id = doc_id
        doc.to_dict.return_value = data
        return doc

    def test_groups_by_department_then_sort_index_then_name(self):
        docs = [
            self._fake_doc("u1", {"name": "小華", "department": "桃園所"}),
            self._fake_doc("u2", {"name": "小明", "department": "桃園所", "sort_index": 1}),
            self._fake_doc("u3", {"name": "小美", "department": "桃園所", "sort_index": 0}),
            self._fake_doc("u4", {"name": "老闆", "department": "台中所"}),
        ]
        fake_collection = mock.Mock()
        fake_collection.stream.return_value = docs
        with mock.patch.object(platform_accounts, "users_ref", return_value=fake_collection):
            result = platform_accounts.list_accounts()
        self.assertEqual([a["username"] for a in result], ["u4", "u3", "u2", "u1"])


class ReorderDepartmentTests(unittest.TestCase):
    """reorder_department() 是帳號權限管理頁面拖曳排序存檔的核心邏輯：
    只更新真的屬於這個部門的帳號，其餘（不相干的 username、屬於別部門的
    帳號）一律忽略，不完全信任前端送來的內容。"""

    def test_updates_sort_index_in_given_order_and_ignores_unrelated_usernames(self):
        fake_accounts = [
            {"username": "u1", "department": "桃園所"},
            {"username": "u2", "department": "桃園所"},
            {"username": "u3", "department": "台中所"},
        ]
        fake_batch = mock.Mock()
        fake_db = mock.Mock()
        fake_db.batch.return_value = fake_batch
        doc_refs = {"u1": mock.Mock(name="doc_u1"), "u2": mock.Mock(name="doc_u2")}
        fake_collection = mock.Mock()
        fake_collection.document.side_effect = lambda u: doc_refs[u]

        with mock.patch.object(platform_accounts, "list_accounts", return_value=fake_accounts):
            with mock.patch.object(platform_accounts, "get_db", return_value=fake_db):
                with mock.patch.object(platform_accounts, "users_ref", return_value=fake_collection):
                    platform_accounts.reorder_department("桃園所", ["u2", "u1", "u3", "u_unknown"])

        fake_batch.update.assert_any_call(doc_refs["u2"], {"sort_index": 0})
        fake_batch.update.assert_any_call(doc_refs["u1"], {"sort_index": 1})
        self.assertEqual(fake_batch.update.call_count, 2)
        fake_batch.commit.assert_called_once()


class ValidateAccountDeletionTests(unittest.TestCase):
    def test_cannot_delete_self(self):
        error = validate_account_deletion("alice", "alice", target_is_platform_admin=False)
        self.assertEqual(error, "self")

    def test_cannot_delete_platform_admin_account(self):
        error = validate_account_deletion("alice", "bob", target_is_platform_admin=True)
        self.assertEqual(error, "platform_admin")

    def test_can_delete_ordinary_account(self):
        error = validate_account_deletion("alice", "bob", target_is_platform_admin=False)
        self.assertEqual(error, "")

    def test_self_check_takes_priority_over_platform_admin_check(self):
        error = validate_account_deletion("alice", "alice", target_is_platform_admin=True)
        self.assertEqual(error, "self")


if __name__ == "__main__":
    unittest.main()
