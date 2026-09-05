import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from management.repository import can_view_client_visit, group_staff_by_department, record_asset_event


class CanViewClientVisitTests(unittest.TestCase):
    """客戶拜訪紀錄刻意只有記錄本人跟管理部主管（is_management_admin，
    呼叫端會用 user["role"] == "admin" 算出來，全平台管理員也會落在這裡，
    因為 platform_accounts.module_role() 對任何模組都回傳 admin）看得到，
    這是這次管理部功能裡唯一一個「不是全部門共享」的可見範圍規則，值得
    直接測。"""

    def test_creator_can_view_own_visit(self):
        visit = {"created_by": "alice"}
        self.assertTrue(can_view_client_visit(visit, "alice", is_management_admin=False))

    def test_other_staff_cannot_view_someone_elses_visit(self):
        visit = {"created_by": "alice"}
        self.assertFalse(can_view_client_visit(visit, "bob", is_management_admin=False))

    def test_management_admin_can_view_any_visit(self):
        visit = {"created_by": "alice"}
        self.assertTrue(can_view_client_visit(visit, "manager", is_management_admin=True))

    def test_creator_who_is_also_management_admin_can_still_view_own(self):
        visit = {"created_by": "alice"}
        self.assertTrue(can_view_client_visit(visit, "alice", is_management_admin=True))


class GroupStaffByDepartmentTests(unittest.TestCase):
    """組織圖是員工名冊依部門分組後的畫面呈現，這裡測分組邏輯本身。"""

    def test_groups_by_department_preserving_first_seen_order(self):
        staff = [
            {"department": "業務部", "name": "小明"},
            {"department": "管理部", "name": "小華"},
            {"department": "業務部", "name": "小美"},
        ]
        groups = group_staff_by_department(staff)
        self.assertEqual([g["department"] for g in groups], ["業務部", "管理部"])
        self.assertEqual([m["name"] for m in groups[0]["members"]], ["小明", "小美"])
        self.assertEqual([m["name"] for m in groups[1]["members"]], ["小華"])

    def test_empty_list_returns_empty_groups(self):
        self.assertEqual(group_staff_by_department([]), [])


class RecordAssetEventValidationTests(unittest.TestCase):
    """record_asset_event() 在真的去查/寫 Firestore 之前，會先擋掉不合法的
    狀態代碼——這一段不用碰資料庫就能測到。"""

    def test_invalid_status_rejected_before_touching_firestore(self):
        self.assertFalse(
            record_asset_event("asset1", "not-a-real-status", "alice", "2026-01-01", "", "bob", "Bob")
        )


if __name__ == "__main__":
    unittest.main()
