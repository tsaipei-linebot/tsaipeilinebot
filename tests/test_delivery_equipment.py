import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.repository import equipment_transaction_error


_PERSONNEL = {"id": "p1", "name": "小明", "vendor": "ud"}


def _no_missing_documents():
    # equipment_transaction_error() 在檢查庫存/尚欠之前會先呼叫
    # missing_documents()——這裡直接把它 patch 成「沒有缺件」，讓測試專注在
    # equipment_transaction_error 自己的數量/庫存/尚欠邏輯，不用另外在測試裡
    # 組出一份真的滿足 config.DOC_TYPES 全部應備項目的人員資料（那是
    # missing_documents 自己單元測試該覆蓋的範圍，不是這裡的重點）。
    return mock.patch.object(repository, "missing_documents", return_value=[])


class EquipmentTransactionErrorTests(unittest.TestCase):
    """equipment_transaction_error() 是純函式（Firestore 讀取跟驗證邏輯分開），
    覆蓋 repository.py 說明裡列出的每一種擋下情境。"""

    def test_quantity_must_be_a_positive_integer(self):
        for bad in (0, -1, 1.5, "3", None):
            self.assertEqual(
                equipment_transaction_error("purchase", None, {}, bad),
                "invalid_quantity",
                f"quantity={bad!r}",
            )

    def test_borrow_requires_personnel(self):
        self.assertEqual(
            equipment_transaction_error("borrow", None, {"quantity": 10}, 1),
            "personnel_not_found",
        )

    def test_transfer_and_purchase_do_not_require_personnel(self):
        with _no_missing_documents():
            self.assertEqual(equipment_transaction_error("transfer", None, {"quantity": 10}, 1), "")
            self.assertEqual(equipment_transaction_error("purchase", None, {"quantity": 10}, 1), "")

    def test_borrow_blocked_when_personnel_has_missing_documents(self):
        with mock.patch.object(repository, "missing_documents", return_value=[{"code": "id_card"}]):
            self.assertEqual(
                equipment_transaction_error("borrow", _PERSONNEL, {"quantity": 10}, 1),
                "personnel_missing_documents",
            )

    def test_borrow_blocked_when_stock_insufficient(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("borrow", _PERSONNEL, {"quantity": 2}, 5),
                "insufficient_stock",
            )

    def test_borrow_allowed_when_stock_sufficient(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("borrow", _PERSONNEL, {"quantity": 5}, 5),
                "",
            )

    def test_borrow_override_stock_check_still_checks_missing_documents(self):
        # 主管特批（override_stock_check）只略過庫存檢查，缺件仍然要擋下——
        # 不能靠特批繞過人員資格本身。
        with mock.patch.object(repository, "missing_documents", return_value=[{"code": "id_card"}]):
            self.assertEqual(
                equipment_transaction_error(
                    "borrow", _PERSONNEL, {"quantity": 2}, 5, override_stock_check=True
                ),
                "personnel_missing_documents",
            )

    def test_transfer_blocked_when_stock_insufficient(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("transfer", None, {"quantity": 2}, 5),
                "insufficient_stock",
            )

    def test_transfer_override_bypasses_stock_check(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("transfer", None, {"quantity": 2}, 5, override_stock_check=True),
                "",
            )

    def test_purchase_never_checks_stock(self):
        with _no_missing_documents():
            self.assertEqual(equipment_transaction_error("purchase", None, {"quantity": 0}, 999), "")

    def test_return_blocked_when_quantity_exceeds_debt(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("return", _PERSONNEL, None, 5, debt={"quantity_owed": 2}),
                "insufficient_debt",
            )

    def test_return_allowed_when_quantity_within_debt(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("return", _PERSONNEL, None, 2, debt={"quantity_owed": 5}),
                "",
            )

    def test_buyout_blocked_when_quantity_exceeds_debt(self):
        with _no_missing_documents():
            self.assertEqual(
                equipment_transaction_error("buyout", _PERSONNEL, None, 3, debt={"quantity_owed": 1}),
                "insufficient_debt",
            )

    def test_buyout_requires_personnel_like_borrow(self):
        self.assertEqual(
            equipment_transaction_error("buyout", None, None, 1, debt={"quantity_owed": 5}),
            "personnel_not_found",
        )


def _fake_doc_snapshot(exists: bool, data: dict = None):
    snapshot = mock.Mock(exists=exists)
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _fake_transaction_collection(snapshot):
    fake_doc_ref = mock.Mock()
    fake_doc_ref.get.return_value = snapshot
    fake_collection = mock.Mock()
    fake_collection.document.return_value = fake_doc_ref
    return fake_collection, fake_doc_ref


class ApplyEquipmentTransactionEffectTests(unittest.TestCase):
    """_apply_equipment_transaction_effect() 是 record/update/delete 共用的
    庫存/尚欠異動邏輯，sign=-1 要是 sign=1 的精確鏡像（新增-刪除同一筆
    紀錄，庫存/尚欠要完全回到刪除前的樣子）。"""

    def _adjust_calls(self, transaction_type, sign, **kwargs):
        with mock.patch.object(repository, "_adjust_equipment_stock") as mock_stock:
            with mock.patch.object(repository, "_adjust_equipment_debt") as mock_debt:
                repository._apply_equipment_transaction_effect(
                    transaction_type,
                    kwargs.get("item_id", "item1"),
                    kwargs.get("quantity", 3),
                    kwargs.get("from_location_id", "loc-a"),
                    kwargs.get("to_location_id", "loc-b"),
                    kwargs.get("personnel_id", "p1"),
                    sign=sign,
                )
        return mock_stock.call_args_list, mock_debt.call_args_list

    def test_borrow_forward_and_reverse_are_mirror_images(self):
        stock_fwd, debt_fwd = self._adjust_calls("borrow", 1)
        stock_rev, debt_rev = self._adjust_calls("borrow", -1)
        self.assertEqual(stock_fwd, [mock.call("loc-a", "item1", -3)])
        self.assertEqual(debt_fwd, [mock.call("p1", "item1", 3)])
        self.assertEqual(stock_rev, [mock.call("loc-a", "item1", 3)])
        self.assertEqual(debt_rev, [mock.call("p1", "item1", -3)])

    def test_return_forward_and_reverse_are_mirror_images(self):
        stock_fwd, debt_fwd = self._adjust_calls("return", 1)
        stock_rev, debt_rev = self._adjust_calls("return", -1)
        self.assertEqual(stock_fwd, [mock.call("loc-a", "item1", 3)])
        self.assertEqual(debt_fwd, [mock.call("p1", "item1", -3)])
        self.assertEqual(stock_rev, [mock.call("loc-a", "item1", -3)])
        self.assertEqual(debt_rev, [mock.call("p1", "item1", 3)])

    def test_transfer_forward_and_reverse_are_mirror_images(self):
        stock_fwd, debt_fwd = self._adjust_calls("transfer", 1)
        stock_rev, _ = self._adjust_calls("transfer", -1)
        self.assertEqual(stock_fwd, [mock.call("loc-a", "item1", -3), mock.call("loc-b", "item1", 3)])
        self.assertEqual(stock_rev, [mock.call("loc-a", "item1", 3), mock.call("loc-b", "item1", -3)])
        self.assertEqual(debt_fwd, [])

    def test_purchase_only_touches_destination_stock(self):
        stock_fwd, debt_fwd = self._adjust_calls("purchase", 1)
        self.assertEqual(stock_fwd, [mock.call("loc-b", "item1", 3)])
        self.assertEqual(debt_fwd, [])

    def test_buyout_only_touches_debt(self):
        stock_fwd, debt_fwd = self._adjust_calls("buyout", 1)
        self.assertEqual(stock_fwd, [])
        self.assertEqual(debt_fwd, [mock.call("p1", "item1", -3)])


class DeleteEquipmentTransactionTests(unittest.TestCase):
    def test_missing_transaction_returns_false(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            self.assertFalse(repository.delete_equipment_transaction("missing"))
        fake_doc_ref.delete.assert_not_called()

    def test_borrow_reverses_effect_then_deletes(self):
        data = {
            "type": "borrow", "item_id": "item1", "quantity": 4,
            "from_location_id": "loc-a", "to_location_id": "", "personnel_id": "p1",
        }
        snapshot = _fake_doc_snapshot(True, data)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            with mock.patch.object(repository, "_adjust_equipment_stock") as mock_stock:
                with mock.patch.object(repository, "_adjust_equipment_debt") as mock_debt:
                    result = repository.delete_equipment_transaction("t1")
        self.assertTrue(result)
        mock_stock.assert_called_once_with("loc-a", "item1", 4)
        mock_debt.assert_called_once_with("p1", "item1", -4)
        fake_doc_ref.delete.assert_called_once()

    def test_writeoff_adds_debt_back_then_deletes(self):
        data = {"type": "writeoff", "item_id": "item1", "quantity": 7, "personnel_id": "p1"}
        snapshot = _fake_doc_snapshot(True, data)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            with mock.patch.object(repository, "_adjust_equipment_debt") as mock_debt:
                result = repository.delete_equipment_transaction("t1")
        self.assertTrue(result)
        mock_debt.assert_called_once_with("p1", "item1", 7)
        fake_doc_ref.delete.assert_called_once()


class UpdateEquipmentTransactionTests(unittest.TestCase):
    def test_missing_transaction_returns_not_found(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, _ = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            ok, error = repository.update_equipment_transaction("missing", quantity=1)
        self.assertFalse(ok)
        self.assertEqual(error, "not_found")

    def test_writeoff_only_updates_reason_and_skips_validation(self):
        data = {"type": "writeoff", "item_id": "item1", "quantity": 5, "personnel_id": "p1"}
        snapshot = _fake_doc_snapshot(True, data)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            ok, error = repository.update_equipment_transaction("t1", quantity=0, reason="盤點對不起來")
        self.assertTrue(ok)
        self.assertEqual(error, "")
        fake_doc_ref.update.assert_called_once_with({"reason": "盤點對不起來"})

    def test_invalid_new_quantity_rolls_back_reversed_effect(self):
        data = {
            "type": "purchase", "item_id": "item1", "quantity": 5,
            "from_location_id": "", "to_location_id": "loc-b", "personnel_id": "",
        }
        snapshot = _fake_doc_snapshot(True, data)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            with mock.patch.object(repository, "_adjust_equipment_stock") as mock_stock:
                ok, error = repository.update_equipment_transaction("t1", quantity=-1, to_location_id="loc-b")
        self.assertFalse(ok)
        self.assertEqual(error, "invalid_quantity")
        # 復原舊效果（+5）失敗後要加回去（-5 的反向，也就是再 -5），
        # 兩次呼叫合計等於完全沒發生過任何淨異動。
        self.assertEqual(
            mock_stock.call_args_list,
            [mock.call("loc-b", "item1", -5), mock.call("loc-b", "item1", 5)],
        )
        fake_doc_ref.update.assert_not_called()

    def test_valid_update_reapplies_new_effect_and_writes_fields(self):
        data = {
            "type": "purchase", "item_id": "item1", "quantity": 5,
            "from_location_id": "", "to_location_id": "loc-b", "personnel_id": "",
        }
        snapshot = _fake_doc_snapshot(True, data)
        fake_collection, fake_doc_ref = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            with mock.patch.object(repository, "_adjust_equipment_stock") as mock_stock:
                ok, error = repository.update_equipment_transaction("t1", quantity=8, to_location_id="loc-b")
        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(
            mock_stock.call_args_list,
            [mock.call("loc-b", "item1", -5), mock.call("loc-b", "item1", 8)],
        )
        payload = fake_doc_ref.update.call_args.args[0]
        self.assertEqual(payload["quantity"], 8)
        self.assertEqual(payload["to_location_id"], "loc-b")


class GetEquipmentTransactionTests(unittest.TestCase):
    def test_returns_none_when_missing(self):
        snapshot = _fake_doc_snapshot(False)
        fake_collection, _ = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            self.assertIsNone(repository.get_equipment_transaction("missing"))

    def test_returns_data_with_id(self):
        snapshot = _fake_doc_snapshot(True, {"type": "purchase"})
        snapshot.id = "t1"
        fake_collection, _ = _fake_transaction_collection(snapshot)
        with mock.patch.object(repository, "equipment_transactions_ref", return_value=fake_collection):
            data = repository.get_equipment_transaction("t1")
        self.assertEqual(data["id"], "t1")
        self.assertEqual(data["type"], "purchase")


if __name__ == "__main__":
    unittest.main()
