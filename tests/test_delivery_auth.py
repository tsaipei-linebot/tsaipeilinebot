import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.auth import hash_password, validate_user_deletion, verify_password


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


class ValidateUserDeletionTests(unittest.TestCase):
    def test_cannot_delete_self(self):
        error = validate_user_deletion("alice", "alice", "staff", admin_count=2)
        self.assertEqual(error, "self")

    def test_cannot_delete_last_admin(self):
        error = validate_user_deletion("alice", "bob", "admin", admin_count=1)
        self.assertEqual(error, "last_admin")

    def test_can_delete_admin_when_others_remain(self):
        error = validate_user_deletion("alice", "bob", "admin", admin_count=2)
        self.assertEqual(error, "")

    def test_can_delete_staff_regardless_of_admin_count(self):
        error = validate_user_deletion("alice", "bob", "staff", admin_count=1)
        self.assertEqual(error, "")

    def test_self_check_takes_priority_over_last_admin_check(self):
        # 自己就是最後一位管理員時，還是回傳 "self"（不能刪自己這個規則優先），
        # 而不是被 last_admin 規則蓋過去，訊息才會準確對應到真正的原因。
        error = validate_user_deletion("alice", "alice", "admin", admin_count=1)
        self.assertEqual(error, "self")


if __name__ == "__main__":
    unittest.main()
