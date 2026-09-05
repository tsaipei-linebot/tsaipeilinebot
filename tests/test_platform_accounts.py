import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

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
