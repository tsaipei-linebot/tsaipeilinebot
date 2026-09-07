import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from scripts.migrate_users_to_modules import _plan_migration


class _FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def stream(self):
        return list(self._docs)


class PlanMigrationTests(unittest.TestCase):
    """一次性遷移腳本的規劃邏輯（不碰 Firestore 寫入，純粹算出「打算做什麼
    變更」），確保：舊版 role 欄位正確轉成 modules，只有指定的帳號被標記
    is_platform_admin，已經是新格式的帳號會被跳過（避免腳本重複執行時
    把已經手動調整過的權限蓋掉）。"""

    def _patch_users_ref(self, docs):
        return patch(
            "scripts.migrate_users_to_modules.users_ref",
            return_value=_FakeCollection([_FakeSnapshot(doc_id, data) for doc_id, data in docs]),
        )

    def test_converts_role_to_modules(self):
        with self._patch_users_ref([("alice", {"role": "admin"}), ("bob", {"role": "staff"})]):
            plan = _plan_migration("alice")
        by_username = {item["username"]: item for item in plan}
        self.assertEqual(by_username["alice"]["modules"], {"delivery": "admin"})
        self.assertEqual(by_username["bob"]["modules"], {"delivery": "staff"})

    def test_only_named_account_becomes_platform_admin(self):
        with self._patch_users_ref([("alice", {"role": "admin"}), ("bob", {"role": "admin"})]):
            plan = _plan_migration("alice")
        by_username = {item["username"]: item for item in plan}
        self.assertTrue(by_username["alice"]["is_platform_admin"])
        self.assertFalse(by_username["bob"]["is_platform_admin"])

    def test_missing_role_defaults_to_staff(self):
        with self._patch_users_ref([("carol", {})]):
            plan = _plan_migration("alice")
        self.assertEqual(plan[0]["modules"], {"delivery": "staff"})

    def test_already_migrated_accounts_are_skipped(self):
        with self._patch_users_ref([("alice", {"modules": {"delivery": "admin"}, "is_platform_admin": True})]):
            plan = _plan_migration("alice")
        self.assertEqual(plan, [])


if __name__ == "__main__":
    unittest.main()
