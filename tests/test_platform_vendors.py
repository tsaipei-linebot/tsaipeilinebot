import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
