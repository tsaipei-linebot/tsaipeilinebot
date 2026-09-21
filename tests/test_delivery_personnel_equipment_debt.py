import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import vendor_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _staff_account():
    return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "specialist"}


class PersonnelDetailEquipmentDebtTests(unittest.TestCase):
    """退保連動提醒（2026-09-17 新增）：人員詳細頁需要知道這個人名下還有
    沒有裝備借還管理的未歸還裝備，供樣板顯示警告／JS 在改選「離職」時
    跳出提醒視窗用。「退保」這個系統目前還沒有對應功能，暫時掛在人員
    狀態改成「離職」這個既有動作上（見 vendor_routes.personnel_detail()
    的說明）。"""

    PERSON = {"id": "p1", "vendor": "ud", "name": "小明"}

    def test_resolves_item_names_for_outstanding_debt(self):
        debt = [{"personnel_id": "p1", "item_id": "item1", "quantity_owed": 2}]
        items = [{"id": "item1", "name": "橘衣"}]
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "list_equipment_debt", return_value=debt) as mock_debt:
                with mock.patch.object(vendor_routes.repository, "list_equipment_items", return_value=items):
                    with mock.patch.object(vendor_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vendor_routes.repository, "all_document_statuses", return_value=[]):
                            with mock.patch.object(vendor_routes, "templates") as mock_templates:
                                vendor_routes.personnel_detail("p1", _FakeRequest(_staff_account()), redirect=None)
        mock_debt.assert_called_once_with(personnel_id="p1")
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["equipment_debt"][0]["item_name"], "橘衣")
        self.assertEqual(context["equipment_debt"][0]["quantity_owed"], 2)

    def test_no_outstanding_debt_is_empty_list(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "list_equipment_debt", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "list_cooperation_types", return_value=[]):
                    with mock.patch.object(vendor_routes.repository, "all_document_statuses", return_value=[]):
                        with mock.patch.object(vendor_routes, "templates") as mock_templates:
                            vendor_routes.personnel_detail("p1", _FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["equipment_debt"], [])

    def test_deleted_item_shows_placeholder_name(self):
        debt = [{"personnel_id": "p1", "item_id": "gone", "quantity_owed": 1}]
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "list_equipment_debt", return_value=debt):
                with mock.patch.object(vendor_routes.repository, "list_equipment_items", return_value=[]):
                    with mock.patch.object(vendor_routes.repository, "list_cooperation_types", return_value=[]):
                        with mock.patch.object(vendor_routes.repository, "all_document_statuses", return_value=[]):
                            with mock.patch.object(vendor_routes, "templates") as mock_templates:
                                vendor_routes.personnel_detail("p1", _FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["equipment_debt"][0]["item_name"], "（已刪除品項）")

    def test_passes_cooperation_types_by_vendor_for_live_filtering(self):
        # 2026-09-21 新增：人員詳細頁的「所屬廠商」選單改廠商時，瀏覽器端
        # JS 要靠這份全部廠商分組好的資料即時篩選/帶入合作方式，不用整頁
        # 重新整理去問伺服器。
        fake_by_vendor = {"shopee": [{"id": "two_wheel_contract", "name": "二輪承攬"}]}
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "list_equipment_debt", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "list_cooperation_types", return_value=[]):
                    with mock.patch.object(
                        vendor_routes.repository, "cooperation_types_by_vendor", return_value=fake_by_vendor
                    ):
                        with mock.patch.object(vendor_routes.repository, "all_document_statuses", return_value=[]):
                            with mock.patch.object(vendor_routes, "templates") as mock_templates:
                                vendor_routes.personnel_detail("p1", _FakeRequest(_staff_account()), redirect=None)
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["cooperation_types_by_vendor"], fake_by_vendor)


if __name__ == "__main__":
    unittest.main()
