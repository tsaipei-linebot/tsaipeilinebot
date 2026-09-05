import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from management.repository import can_view_client_visit, group_staff_by_department, record_asset_event


class CanViewClientVisitTests(unittest.TestCase):
    """客戶拜訪紀錄刻意只有記錄本人跟全平台管理員看得到，這是這次管理部
    功能裡唯一一個「不是全部門共享」的可見範圍規則，值得直接測。"""

    def test_creator_can_view_own_visit(self):
        visit = {"created_by": "alice"}
        self.assertTrue(can_view_client_visit(visit, "alice", is_platform_admin=False))

    def test_other_staff_cannot_view_someone_elses_visit(self):
        visit = {"created_by": "alice"}
        self.assertFalse(can_view_client_visit(visit, "bob", is_platform_admin=False))

    def test_platform_admin_can_view_any_visit(self):
        visit = {"created_by": "alice"}
        self.assertTrue(can_view_client_visit(visit, "boss", is_platform_admin=True))

    def test_module_admin_who_is_not_platform_admin_cannot_view_others(self):
        # 這裡刻意強調：management 模組的「主管」角色跟 is_platform_admin
        # 是兩回事，只有後者能看到別人的拜訪紀錄。
        visit = {"created_by": "alice"}
        self.assertFalse(can_view_client_visit(visit, "management_admin", is_platform_admin=False))


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
