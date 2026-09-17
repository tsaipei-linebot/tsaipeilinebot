import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import equipment_routes


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user):
        self.session = _FakeSession({"user": user})


def _staff_account():
    return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "specialist"}


def _admin_account():
    return {"username": "alice", "modules": {"delivery": "staff"}, "is_platform_admin": False, "rank": "manager"}


def _mock_form_context_lookups():
    """送出失敗要重新渲染表單時，路由會重新查一次品項/放置點/在職人員清單
    給下拉選單用——這幾個查詢跟這裡要測的「欄位驗證擋不擋得住」無關，統一
    mock 掉避免打到真的 Firestore。"""
    return mock.patch.multiple(
        equipment_routes.repository,
        list_equipment_items=mock.DEFAULT,
        list_equipment_locations=mock.DEFAULT,
        search_personnel=mock.DEFAULT,
    )


class TransactionSubmitFieldValidationTests(unittest.TestCase):
    """路由層的基本欄位檢查（下拉選單有沒有選、轉倉來源/目的是否相同），
    在呼叫 repository 之前就先擋下，不讓空字串文件 ID 打進 Firestore 查詢。"""

    def test_missing_item_is_rejected_without_calling_repository(self):
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "record_equipment_transaction") as mock_record:
                with mock.patch.object(equipment_routes, "templates") as mock_templates:
                    equipment_routes.transaction_submit(
                        _FakeRequest(_staff_account()),
                        transaction_type="purchase",
                        item_id="",
                        quantity=5,
                        to_location_id="loc1",
                        redirect=None,
                    )
        mock_record.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_transfer_requires_two_different_locations(self):
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "record_equipment_transaction") as mock_record:
                with mock.patch.object(equipment_routes, "templates"):
                    equipment_routes.transaction_submit(
                        _FakeRequest(_staff_account()),
                        transaction_type="transfer",
                        item_id="item1",
                        quantity=1,
                        from_location_id="loc1",
                        to_location_id="loc1",
                        redirect=None,
                    )
        mock_record.assert_not_called()

    def test_purchase_requires_to_location(self):
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "record_equipment_transaction") as mock_record:
                with mock.patch.object(equipment_routes, "templates"):
                    equipment_routes.transaction_submit(
                        _FakeRequest(_staff_account()),
                        transaction_type="purchase",
                        item_id="item1",
                        quantity=1,
                        to_location_id="",
                        redirect=None,
                    )
        mock_record.assert_not_called()

    def test_borrow_requires_personnel(self):
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "record_equipment_transaction") as mock_record:
                with mock.patch.object(equipment_routes, "templates"):
                    equipment_routes.transaction_submit(
                        _FakeRequest(_staff_account()),
                        transaction_type="borrow",
                        item_id="item1",
                        quantity=1,
                        from_location_id="loc1",
                        personnel_id="",
                        redirect=None,
                    )
        mock_record.assert_not_called()

    def test_valid_purchase_calls_repository_and_redirects(self):
        with mock.patch.object(
            equipment_routes.repository, "record_equipment_transaction", return_value=(True, "")
        ) as mock_record:
            resp = equipment_routes.transaction_submit(
                _FakeRequest(_staff_account()),
                transaction_type="purchase",
                item_id="item1",
                quantity=10,
                from_location_id="",
                to_location_id="loc1",
                personnel_id="",
                redirect=None,
            )
        mock_record.assert_called_once_with(
            transaction_type="purchase",
            item_id="item1",
            quantity=10,
            from_location_id="",
            to_location_id="loc1",
            personnel_id="",
            unit_price=None,
            payment_received=False,
            reported_by="bob",
            override_stock_check=False,
        )
        self.assertEqual(resp.status_code, 303)


class TransactionSubmitAdminOnlyTests(unittest.TestCase):
    """核銷（writeoff）限管理員操作，一般同仁即使直接送表單也要被擋下。"""

    def test_non_admin_writeoff_is_rejected(self):
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "record_equipment_writeoff") as mock_writeoff:
                with mock.patch.object(equipment_routes, "templates") as mock_templates:
                    equipment_routes.transaction_submit(
                        _FakeRequest(_staff_account()),
                        transaction_type="writeoff",
                        item_id="item1",
                        personnel_id="p1",
                        reason="遺失",
                        redirect=None,
                    )
        mock_writeoff.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])

    def test_admin_writeoff_calls_repository(self):
        with mock.patch.object(
            equipment_routes.repository, "record_equipment_writeoff", return_value=(True, "")
        ) as mock_writeoff:
            resp = equipment_routes.transaction_submit(
                _FakeRequest(_admin_account()),
                transaction_type="writeoff",
                item_id="item1",
                personnel_id="p1",
                reason="遺失",
                redirect=None,
            )
        mock_writeoff.assert_called_once_with(personnel_id="p1", item_id="item1", reason="遺失", operated_by="alice")
        self.assertEqual(resp.status_code, 303)

    def test_non_admin_override_stock_check_is_ignored(self):
        # 就算一般同仁在表單裡夾帶 override_stock_check=1，後端也不能相信這個
        # 值——只有真的是管理員身分才會把 True 傳進 repository。
        with mock.patch.object(
            equipment_routes.repository, "record_equipment_transaction", return_value=(True, "")
        ) as mock_record:
            equipment_routes.transaction_submit(
                _FakeRequest(_staff_account()),
                transaction_type="borrow",
                item_id="item1",
                quantity=1,
                from_location_id="loc1",
                personnel_id="p1",
                override_stock_check="1",
                redirect=None,
            )
        self.assertFalse(mock_record.call_args.kwargs["override_stock_check"])


class TransactionSubmitBuyoutTests(unittest.TestCase):
    """買斷單價依品項設定的買斷單價自動計算，不信任前端送來的單價欄位。"""

    def test_buyout_uses_item_configured_unit_price(self):
        item = {"id": "item1", "name": "籃子", "buyout_unit_price": 500}
        with mock.patch.object(equipment_routes.repository, "get_equipment_item", return_value=item):
            with mock.patch.object(
                equipment_routes.repository, "record_equipment_transaction", return_value=(True, "")
            ) as mock_record:
                equipment_routes.transaction_submit(
                    _FakeRequest(_staff_account()),
                    transaction_type="buyout",
                    item_id="item1",
                    quantity=2,
                    personnel_id="p1",
                    payment_received="1",
                    redirect=None,
                )
        self.assertEqual(mock_record.call_args.kwargs["unit_price"], 500)
        self.assertTrue(mock_record.call_args.kwargs["payment_received"])

    def test_buyout_blocked_when_item_has_no_unit_price_set(self):
        item = {"id": "item1", "name": "籃子", "buyout_unit_price": None}
        with _mock_form_context_lookups():
            with mock.patch.object(equipment_routes.repository, "get_equipment_item", return_value=item):
                with mock.patch.object(equipment_routes.repository, "record_equipment_transaction") as mock_record:
                    with mock.patch.object(equipment_routes, "templates") as mock_templates:
                        equipment_routes.transaction_submit(
                            _FakeRequest(_staff_account()),
                            transaction_type="buyout",
                            item_id="item1",
                            quantity=2,
                            personnel_id="p1",
                            redirect=None,
                        )
        mock_record.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["error"])


class ItemsAdminRoutesTests(unittest.TestCase):
    def test_create_item_parses_price_and_calls_repository(self):
        with mock.patch.object(equipment_routes.repository, "create_equipment_item") as mock_create:
            resp = equipment_routes.create_item(
                _FakeRequest(_admin_account()), name="籃子", buyout_unit_price="500", redirect=None
            )
        mock_create.assert_called_once_with("籃子", buyout_unit_price=500, created_by="alice")
        self.assertEqual(resp.status_code, 303)

    def test_create_item_blank_price_is_none(self):
        with mock.patch.object(equipment_routes.repository, "create_equipment_item") as mock_create:
            equipment_routes.create_item(
                _FakeRequest(_admin_account()), name="橘衣", buyout_unit_price="", redirect=None
            )
        mock_create.assert_called_once_with("橘衣", buyout_unit_price=None, created_by="alice")

    def test_delete_item_calls_repository(self):
        with mock.patch.object(equipment_routes.repository, "delete_equipment_item", return_value=True) as mock_delete:
            resp = equipment_routes.delete_item("item1", _FakeRequest(_admin_account()), redirect=None)
        mock_delete.assert_called_once_with("item1")
        self.assertEqual(resp.status_code, 303)


class LocationsAdminRoutesTests(unittest.TestCase):
    def test_create_location_calls_repository(self):
        with mock.patch.object(equipment_routes.repository, "create_equipment_location") as mock_create:
            resp = equipment_routes.create_location(_FakeRequest(_admin_account()), name="新北所", redirect=None)
        mock_create.assert_called_once_with("新北所", created_by="alice")
        self.assertEqual(resp.status_code, 303)

    def test_toggle_location_active(self):
        with mock.patch.object(equipment_routes.repository, "set_equipment_location_active") as mock_set:
            resp = equipment_routes.toggle_location_active(
                "loc1", _FakeRequest(_admin_account()), active="0", redirect=None
            )
        mock_set.assert_called_once_with("loc1", False)
        self.assertEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
