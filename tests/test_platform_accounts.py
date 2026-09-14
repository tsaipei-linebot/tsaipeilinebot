import os
import sys
import time
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
    is_manager_rank,
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


class IsManagerRankTests(unittest.TestCase):
    """2026-09-12 新增：職級副主任（含）以上算管理權限，不在清單裡的職級
    代碼（含空字串、還沒設定）一律當作沒有管理權限，不會誤放行。"""

    def test_deputy_supervisor_and_above_are_manager_rank(self):
        for rank in ("manager", "deputy_manager", "supervisor", "deputy_supervisor"):
            self.assertTrue(is_manager_rank(rank), rank)

    def test_specialist_is_not_manager_rank(self):
        self.assertFalse(is_manager_rank("specialist"))

    def test_unknown_or_missing_rank_is_not_manager_rank(self):
        self.assertFalse(is_manager_rank(""))
        self.assertFalse(is_manager_rank("not-a-real-rank"))
        self.assertFalse(is_manager_rank(None))


class ModuleRoleTests(unittest.TestCase):
    """一個帳號可能同時橫跨好幾個部門模組。2026-09-12 起，modules 只存
    「開放了哪些模組」的清單，模組內算不算管理權限（"admin"/"staff"）
    改由職級（rank）決定，不是新增/編輯帳號時針對每個模組各自指定；
    全平台管理員（is_platform_admin）視同任何模組的管理員。"""

    def test_no_account_has_no_access(self):
        self.assertIsNone(module_role(None, "delivery"))
        self.assertFalse(has_module_access(None, "delivery"))

    def test_account_without_module_has_no_access(self):
        account = {"modules": ["management"], "rank": "manager", "is_platform_admin": False}
        self.assertIsNone(module_role(account, "delivery"))
        self.assertFalse(has_module_access(account, "delivery"))

    def test_open_module_with_specialist_rank_is_staff(self):
        account = {"modules": ["delivery"], "rank": "specialist", "is_platform_admin": False}
        self.assertEqual(module_role(account, "delivery"), ROLE_STAFF)
        self.assertTrue(has_module_access(account, "delivery"))

    def test_open_module_without_rank_defaults_to_staff_not_admin(self):
        # 還沒補職級的帳號（例如舊資料剛遷移過來），一律當作一般權限，
        # 不會因為欄位空白就意外拿到管理權限。
        account = {"modules": ["delivery"], "rank": "", "is_platform_admin": False}
        self.assertEqual(module_role(account, "delivery"), ROLE_STAFF)

    def test_open_module_with_manager_rank_is_admin(self):
        account = {"modules": ["delivery", "management"], "rank": "deputy_supervisor", "is_platform_admin": False}
        self.assertEqual(module_role(account, "delivery"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "management"), ROLE_ADMIN)

    def test_manager_rank_alone_is_not_enough_without_module_open(self):
        # 職級再高，模組沒開放一樣看不到——「開放與否」跟「職級」是兩件
        # 各自獨立要滿足的條件。
        account = {"modules": [], "rank": "manager", "is_platform_admin": False}
        self.assertIsNone(module_role(account, "delivery"))

    def test_platform_admin_is_admin_of_every_module_even_without_explicit_entry(self):
        account = {"modules": [], "rank": "", "is_platform_admin": True}
        self.assertEqual(module_role(account, "delivery"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "management"), ROLE_ADMIN)
        self.assertEqual(module_role(account, "some_future_module"), ROLE_ADMIN)

    def test_legacy_dict_format_modules_still_grants_access_regardless_of_stored_role(self):
        # 2026-09-12 之前存的舊資料格式（模組→角色字典）：不管字典裡原本
        # 存的角色值是什麼，模組存在就算「有開放」，角色一律改看職級，不
        # 是讀字典裡的舊角色值。
        legacy_account = {"modules": {"delivery": "admin"}, "rank": "specialist", "is_platform_admin": False}
        self.assertTrue(has_module_access(legacy_account, "delivery"))
        self.assertEqual(module_role(legacy_account, "delivery"), ROLE_STAFF)


class ToAccountTests(unittest.TestCase):
    """帳號資料多了 manager_usernames／department／rank 幾個欄位（在
    /accounts 網頁上設定），確認舊資料（沒有這些欄位）讀出來會是安全的
    預設值，不會噴 KeyError。"""

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

    def test_defaults_rank_to_empty_string_when_missing(self):
        account = platform_accounts._to_account("alice", {"name": "Alice"})
        self.assertEqual(account["rank"], "")

    def test_preserves_rank_when_present(self):
        account = platform_accounts._to_account("alice", {"name": "Alice", "rank": "supervisor"})
        self.assertEqual(account["rank"], "supervisor")

    def test_normalizes_legacy_dict_modules_to_list_of_codes(self):
        account = platform_accounts._to_account("alice", {"name": "Alice", "modules": {"delivery": "admin", "hr": "staff"}})
        self.assertEqual(set(account["modules"]), {"delivery", "hr"})

    def test_preserves_new_list_modules_format(self):
        account = platform_accounts._to_account("alice", {"name": "Alice", "modules": ["delivery", "hr"]})
        self.assertEqual(set(account["modules"]), {"delivery", "hr"})

    def test_defaults_modules_to_empty_list_when_missing(self):
        account = platform_accounts._to_account("alice", {"name": "Alice"})
        self.assertEqual(account["modules"], [])


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


class _FakeSnapshot:
    def __init__(self, exists, data=None):
        self.exists = exists
        self._data = data or {}

    def to_dict(self):
        return self._data


class IsLockedOutTests(unittest.TestCase):
    """`is_locked_out()` 給登入頁顯示提示訊息，也是 `authenticate()`
    內部用來擋密碼比對的判斷——2026-09-14 新增，防止有人寫程式對著
    已知帳號一直亂猜密碼。"""

    def test_blank_username_is_false(self):
        self.assertFalse(platform_accounts.is_locked_out(""))

    def test_no_lockout_doc_is_false(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=False)
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            self.assertFalse(platform_accounts.is_locked_out("bob"))

    def test_locked_until_in_future_is_true(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=True, data={"locked_until": time.time() + 600})
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            self.assertTrue(platform_accounts.is_locked_out("bob"))

    def test_locked_until_in_past_is_false(self):
        # 鎖定時間已經過了——不用另外跑清除的排程，讀取當下自然判斷成
        # 沒有鎖定，下次成功登入時 _clear_login_lockout() 才會真的清掉
        # 這筆過期的紀錄。
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=True, data={"locked_until": time.time() - 1})
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            self.assertFalse(platform_accounts.is_locked_out("bob"))

    def test_no_locked_until_field_is_false(self):
        # 有失敗紀錄文件，但還沒累積到門檻次數，不會有 locked_until 欄位。
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=True, data={"failed_count": 2})
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            self.assertFalse(platform_accounts.is_locked_out("bob"))


class RecordFailedLoginTests(unittest.TestCase):
    def test_increments_failed_count_from_existing_record(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=True, data={"failed_count": 2})
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            platform_accounts._record_failed_login("bob")
        payload = fake_ref.set.call_args.args[0]
        self.assertEqual(payload["failed_count"], 3)
        self.assertNotIn("locked_until", payload)

    def test_first_failure_starts_count_at_one(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=False)
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            platform_accounts._record_failed_login("bob")
        payload = fake_ref.set.call_args.args[0]
        self.assertEqual(payload["failed_count"], 1)

    def test_reaching_threshold_sets_locked_until_in_the_future(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(
            exists=True, data={"failed_count": platform_accounts.LOGIN_MAX_FAILED_ATTEMPTS - 1}
        )
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            before = time.time()
            platform_accounts._record_failed_login("bob")
        payload = fake_ref.set.call_args.args[0]
        self.assertEqual(payload["failed_count"], platform_accounts.LOGIN_MAX_FAILED_ATTEMPTS)
        self.assertGreater(payload["locked_until"], before)

    def test_set_called_with_merge_true(self):
        fake_ref = mock.Mock()
        fake_ref.get.return_value = _FakeSnapshot(exists=False)
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            platform_accounts._record_failed_login("bob")
        self.assertEqual(fake_ref.set.call_args.kwargs.get("merge"), True)


class ClearLoginLockoutTests(unittest.TestCase):
    def test_deletes_the_lockout_document(self):
        fake_ref = mock.Mock()
        with mock.patch.object(platform_accounts, "_login_lockout_ref", return_value=fake_ref):
            platform_accounts._clear_login_lockout("bob")
        fake_ref.delete.assert_called_once()


class AuthenticateLockoutIntegrationTests(unittest.TestCase):
    """authenticate() 串起帳密比對跟鎖定機制——鎖定期間直接擋掉，不會
    再花成本去跑 PBKDF2 密碼比對；密碼錯誤會記一次失敗；密碼正確會清掉
    失敗紀錄。"""

    def _fake_user_snapshot(self, password):
        return _FakeSnapshot(exists=True, data={"password_hash": platform_accounts.hash_password(password)})

    def test_locked_account_returns_none_even_with_correct_password(self):
        fake_users = mock.Mock()
        fake_users.document.return_value.get.return_value = self._fake_user_snapshot("correct-password")
        with mock.patch.object(platform_accounts, "users_ref", return_value=fake_users):
            with mock.patch.object(platform_accounts, "is_locked_out", return_value=True):
                with mock.patch.object(platform_accounts, "verify_password") as mock_verify:
                    result = platform_accounts.authenticate("bob", "correct-password")
        self.assertIsNone(result)
        mock_verify.assert_not_called()

    def test_wrong_password_records_failed_login(self):
        fake_users = mock.Mock()
        fake_users.document.return_value.get.return_value = self._fake_user_snapshot("correct-password")
        with mock.patch.object(platform_accounts, "users_ref", return_value=fake_users):
            with mock.patch.object(platform_accounts, "is_locked_out", return_value=False):
                with mock.patch.object(platform_accounts, "_record_failed_login") as mock_record:
                    with mock.patch.object(platform_accounts, "_clear_login_lockout") as mock_clear:
                        result = platform_accounts.authenticate("bob", "wrong-password")
        self.assertIsNone(result)
        mock_record.assert_called_once_with("bob")
        mock_clear.assert_not_called()

    def test_correct_password_clears_lockout(self):
        fake_users = mock.Mock()
        fake_users.document.return_value.get.return_value = self._fake_user_snapshot("correct-password")
        with mock.patch.object(platform_accounts, "users_ref", return_value=fake_users):
            with mock.patch.object(platform_accounts, "is_locked_out", return_value=False):
                with mock.patch.object(platform_accounts, "_record_failed_login") as mock_record:
                    with mock.patch.object(platform_accounts, "_clear_login_lockout") as mock_clear:
                        result = platform_accounts.authenticate("bob", "correct-password")
        self.assertIsNotNone(result)
        mock_clear.assert_called_once_with("bob")
        mock_record.assert_not_called()

    def test_nonexistent_user_does_not_check_lockout_or_record_failure(self):
        fake_users = mock.Mock()
        fake_users.document.return_value.get.return_value = _FakeSnapshot(exists=False)
        with mock.patch.object(platform_accounts, "users_ref", return_value=fake_users):
            with mock.patch.object(platform_accounts, "is_locked_out") as mock_locked:
                with mock.patch.object(platform_accounts, "_record_failed_login") as mock_record:
                    result = platform_accounts.authenticate("nobody", "whatever")
        self.assertIsNone(result)
        mock_locked.assert_not_called()
        mock_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
