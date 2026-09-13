import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import platform_vendors


class ToVendorTests(unittest.TestCase):
    def test_defaults_missing_fields_to_empty_string(self):
        vendor = platform_vendors._to_vendor("shopee", {"name": "蝦皮"})
        self.assertEqual(vendor["id"], "shopee")
        self.assertEqual(vendor["name"], "蝦皮")
        self.assertEqual(vendor["company_id"], "")
        self.assertEqual(vendor["note"], "")

    def test_preserves_all_fields_when_present(self):
        data = {field: f"v-{field}" for field in platform_vendors.FIELDS}
        vendor = platform_vendors._to_vendor("abc", data)
        for field in platform_vendors.FIELDS:
            self.assertEqual(vendor[field], f"v-{field}")


class ValidateVendorFieldsTests(unittest.TestCase):
    def test_valid_fields_pass(self):
        self.assertEqual(platform_vendors.validate_vendor_fields({"code": "shopee", "name": "蝦皮"}), "")

    def test_missing_code_fails(self):
        self.assertIn("代號", platform_vendors.validate_vendor_fields({"code": "", "name": "蝦皮"}))

    def test_missing_name_fails(self):
        self.assertIn("廠商名稱", platform_vendors.validate_vendor_fields({"code": "shopee", "name": " "}))

    def test_company_id_can_be_blank(self):
        fields = {"code": "shopee", "name": "蝦皮", "company_id": ""}
        self.assertEqual(platform_vendors.validate_vendor_fields(fields), "")


class CreateVendorAutoTests(unittest.TestCase):
    """合約產生器／派遣契約產生器同步資料用的自動建立函式（2026-09-13
    新增）：文件 ID 交給 Firestore 自動配發，不像 create_vendor() 那樣
    用代號當文件 ID。"""

    def _fake_collection(self):
        fake_ref = mock.Mock()
        fake_ref.id = "auto-id-123"
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_ref
        return fake_collection, fake_ref

    def test_code_defaults_to_tax_id_when_not_given(self):
        fake_collection, fake_ref = self._fake_collection()
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            result_id = platform_vendors.create_vendor_auto(
                {"name": "測試客戶", "tax_id": "12345678", "contract_year": "2027", "company_id": "weizheng"}
            )
        fake_ref.set.assert_called_once_with(
            {"code": "12345678", "name": "測試客戶", "company_id": "weizheng", "note": "",
             "tax_id": "12345678", "contract_year": "2027"}
        )
        self.assertEqual(result_id, "auto-id-123")

    def test_code_falls_back_to_name_when_no_tax_id(self):
        fake_collection, fake_ref = self._fake_collection()
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            platform_vendors.create_vendor_auto({"name": "派遣契約自動建立的客戶"})
        payload = fake_ref.set.call_args[0][0]
        self.assertEqual(payload["code"], "派遣契約自動建立的客戶")
        self.assertEqual(payload["tax_id"], "")
        self.assertEqual(payload["contract_year"], "")

    def test_explicit_code_is_not_overridden(self):
        fake_collection, fake_ref = self._fake_collection()
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            platform_vendors.create_vendor_auto({"code": "custom-code", "name": "測試客戶", "tax_id": "12345678"})
        payload = fake_ref.set.call_args[0][0]
        self.assertEqual(payload["code"], "custom-code")


class VendorNameExistsTests(unittest.TestCase):
    def test_blank_name_returns_false_without_querying(self):
        fake_collection = mock.Mock()
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            self.assertFalse(platform_vendors.vendor_name_exists("  "))
        fake_collection.where.assert_not_called()

    def test_returns_true_when_a_matching_document_exists(self):
        fake_query = mock.Mock()
        fake_query.stream.return_value = iter([mock.Mock()])
        fake_collection = mock.Mock()
        fake_collection.where.return_value.limit.return_value = fake_query
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            self.assertTrue(platform_vendors.vendor_name_exists("蝦皮三輪"))
        fake_collection.where.assert_called_once_with("name", "==", "蝦皮三輪")

    def test_returns_false_when_no_matching_document(self):
        fake_query = mock.Mock()
        fake_query.stream.return_value = iter([])
        fake_collection = mock.Mock()
        fake_collection.where.return_value.limit.return_value = fake_query
        with mock.patch.object(platform_vendors, "vendors_ref", return_value=fake_collection):
            self.assertFalse(platform_vendors.vendor_name_exists("查無此廠商"))


if __name__ == "__main__":
    unittest.main()
