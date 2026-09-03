import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.auth import hash_password, verify_password


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


if __name__ == "__main__":
    unittest.main()
