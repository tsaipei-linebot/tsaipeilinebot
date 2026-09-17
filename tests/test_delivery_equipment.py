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


if __name__ == "__main__":
    unittest.main()
