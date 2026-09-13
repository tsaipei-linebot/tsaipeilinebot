import os
import sys
from unittest import mock
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from services import vendor_sync


class SyncVendorFromClientContractTests(unittest.TestCase):
    """合約產生器送出成功後的同步邏輯：每次都新增一筆，不檢查廠商管理裡
    是不是已經有同名/同統編紀錄——同一家客戶簽了好幾年、甚至同一年簽了
    好幾份，使用者要的就是每份合約都留下自己的紀錄。"""

    def test_always_creates_a_new_vendor_without_checking_duplicates(self):
        with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
            with mock.patch.object(vendor_sync.platform_vendors, "vendor_name_exists") as mock_exists:
                vendor_sync.sync_vendor_from_client_contract(
                    name="測試客戶股份有限公司", tax_id="12345678", contract_year=2027, company_id="weizheng",
                )
        mock_exists.assert_not_called()
        mock_create.assert_called_once_with(
            {"name": "測試客戶股份有限公司", "tax_id": "12345678", "contract_year": "2027", "company_id": "weizheng"}
        )

    def test_contract_year_is_stringified(self):
        with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
            vendor_sync.sync_vendor_from_client_contract(
                name="測試客戶", tax_id="", contract_year=2026, company_id="",
            )
        self.assertEqual(mock_create.call_args[0][0]["contract_year"], "2026")

    def test_missing_contract_year_becomes_empty_string(self):
        with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
            vendor_sync.sync_vendor_from_client_contract(
                name="測試客戶", tax_id="", contract_year=None, company_id="",
            )
        self.assertEqual(mock_create.call_args[0][0]["contract_year"], "")


class SyncVendorFromDispatchContractTests(unittest.TestCase):
    """派遣契約產生器送出成功後的同步邏輯：只有廠商管理裡還沒有同名紀錄
    時才新增一筆，避免同一個客戶重複產生很多份派遣契約時灌爆重複紀錄。"""

    def test_creates_vendor_when_name_not_already_present(self):
        with mock.patch.object(vendor_sync.platform_vendors, "vendor_name_exists", return_value=False):
            with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
                vendor_sync.sync_vendor_from_dispatch_contract("新客戶")
        mock_create.assert_called_once_with({"name": "新客戶"})

    def test_skips_when_name_already_present(self):
        with mock.patch.object(vendor_sync.platform_vendors, "vendor_name_exists", return_value=True):
            with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
                vendor_sync.sync_vendor_from_dispatch_contract("既有客戶")
        mock_create.assert_not_called()

    def test_blank_name_is_ignored(self):
        with mock.patch.object(vendor_sync.platform_vendors, "vendor_name_exists") as mock_exists:
            with mock.patch.object(vendor_sync.platform_vendors, "create_vendor_auto") as mock_create:
                vendor_sync.sync_vendor_from_dispatch_contract("   ")
        mock_exists.assert_not_called()
        mock_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
