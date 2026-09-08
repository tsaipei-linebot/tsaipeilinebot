import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import platform_companies


class ToCompanyTests(unittest.TestCase):
    def test_defaults_missing_fields_to_empty_string(self):
        company = platform_companies._to_company("tsaipei", {"name": "材霈有限公司"})
        self.assertEqual(company["id"], "tsaipei")
        self.assertEqual(company["name"], "材霈有限公司")
        self.assertEqual(company["labor_insurance_no"], "")
        self.assertEqual(company["note"], "")

    def test_preserves_all_fields_when_present(self):
        data = {field: f"v-{field}" for field in platform_companies.FIELDS}
        company = platform_companies._to_company("abc", data)
        for field in platform_companies.FIELDS:
            self.assertEqual(company[field], f"v-{field}")


class ValidateCompanyFieldsTests(unittest.TestCase):
    def _valid_fields(self):
        return {"short_name": "材霈", "name": "材霈有限公司", "tax_id": "29168344"}

    def test_valid_fields_pass(self):
        self.assertEqual(platform_companies.validate_company_fields(self._valid_fields()), "")

    def test_missing_short_name_fails(self):
        fields = self._valid_fields()
        fields["short_name"] = ""
        self.assertIn("簡稱", platform_companies.validate_company_fields(fields))

    def test_missing_name_fails(self):
        fields = self._valid_fields()
        fields["name"] = "  "
        self.assertIn("公司名稱", platform_companies.validate_company_fields(fields))

    def test_missing_tax_id_fails(self):
        fields = self._valid_fields()
        fields["tax_id"] = ""
        self.assertIn("統一編號", platform_companies.validate_company_fields(fields))

    def test_optional_fields_can_be_blank(self):
        fields = self._valid_fields()
        fields["phone"] = ""
        fields["responsible_person"] = ""
        fields["labor_insurance_no"] = ""
        self.assertEqual(platform_companies.validate_company_fields(fields), "")


if __name__ == "__main__":
    unittest.main()
