import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.auth import current_user


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user


class CurrentUserBackwardCompatRoleTests(unittest.TestCase):
    """delivery/auth.py 的 current_user() 是薄薄一層包在 platform_accounts
    共用邏輯外面：既有樣板（base.html、incident_detail.html……）都寫
    `user.role == "admin"`，這裡確保那個計算出來的 role 欄位，反映的是
    「配送部」這個模組的角色，不是別的模組。"""

    def test_no_session_returns_none(self):
        self.assertIsNone(current_user(_FakeRequest()))

    def test_delivery_admin_gets_role_admin(self):
        account = {"username": "alice", "name": "Alice", "modules": {"delivery": "admin"}, "is_platform_admin": False}
        user = current_user(_FakeRequest(account))
        self.assertEqual(user["role"], "admin")

    def test_delivery_staff_gets_role_staff(self):
        account = {"username": "bob", "name": "Bob", "modules": {"delivery": "staff"}, "is_platform_admin": False}
        user = current_user(_FakeRequest(account))
        self.assertEqual(user["role"], "staff")

    def test_admin_of_another_module_only_is_still_staff_here(self):
        account = {"username": "carol", "name": "Carol", "modules": {"management": "admin"}, "is_platform_admin": False}
        user = current_user(_FakeRequest(account))
        self.assertEqual(user["role"], "staff")

    def test_platform_admin_gets_role_admin_even_without_explicit_delivery_entry(self):
        account = {"username": "boss", "name": "老闆", "modules": {}, "is_platform_admin": True}
        user = current_user(_FakeRequest(account))
        self.assertEqual(user["role"], "admin")

    def test_original_account_fields_pass_through(self):
        account = {"username": "alice", "name": "Alice", "modules": {"delivery": "admin"}, "is_platform_admin": False}
        user = current_user(_FakeRequest(account))
        self.assertEqual(user["username"], "alice")
        self.assertEqual(user["is_platform_admin"], False)


if __name__ == "__main__":
    unittest.main()
