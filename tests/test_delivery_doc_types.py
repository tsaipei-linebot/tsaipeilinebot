import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.repository import (
    all_document_statuses,
    applicable_doc_types,
    doc_status,
    missing_documents,
    personnel_matches_filters,
)


class ApplicableDocTypesTests(unittest.TestCase):
    def test_shopee_excludes_police_clearance(self):
        codes = {d["code"] for d in applicable_doc_types("shopee", "two_wheel_contract")}
        self.assertNotIn("police_clearance", codes)

    def test_other_vendor_includes_police_clearance(self):
        codes = {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract")}
        self.assertIn("police_clearance", codes)

    def test_two_wheel_contract_requires_insurance_and_guild_not_liability(self):
        codes = {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract")}
        self.assertIn("insurance", codes)
        self.assertIn("guild_insurance", codes)
        self.assertNotIn("liability_insurance", codes)

    def test_two_wheel_employed_requires_insurance_and_liability_not_guild(self):
        codes = {d["code"] for d in applicable_doc_types("ud", "two_wheel_employed")}
        self.assertIn("insurance", codes)
        self.assertIn("liability_insurance", codes)
        self.assertNotIn("guild_insurance", codes)

    def test_three_wheel_employed_requires_none_of_the_three_insurances(self):
        codes = {d["code"] for d in applicable_doc_types("ud", "three_wheel_employed")}
        self.assertNotIn("insurance", codes)
        self.assertNotIn("guild_insurance", codes)
        self.assertNotIn("liability_insurance", codes)

    def test_unset_cooperation_type_excludes_all_conditional_insurances(self):
        codes = {d["code"] for d in applicable_doc_types("ud", "")}
        self.assertNotIn("insurance", codes)
        self.assertNotIn("guild_insurance", codes)
        self.assertNotIn("liability_insurance", codes)
        # 身分證/駕照/合約簽定/良民證這些不看合作方式，一律都在
        self.assertIn("id_card", codes)
        self.assertIn("driver_license", codes)
        self.assertIn("contract", codes)
        self.assertIn("police_clearance", codes)

    def test_include_vendors_whitelist_only_applies_to_ud(self):
        ud_codes = {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract")}
        shopee_codes = {d["code"] for d in applicable_doc_types("shopee", "two_wheel_contract")}
        uc_codes = {d["code"] for d in applicable_doc_types("uc", "two_wheel_contract")}
        for code in ("uber_system", "selfie_photo"):
            self.assertIn(code, ud_codes)
            self.assertNotIn(code, shopee_codes)
            self.assertNotIn(code, uc_codes)

    def test_momo_test_requires_ud_and_momo_client(self):
        self.assertIn("momo_test", {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract", "momo")})
        self.assertNotIn("momo_test", {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract", "pchome")})
        self.assertNotIn("momo_test", {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract", "")})
        # UD 以外的廠商就算 client=momo 也不會出現（clients 限定要先過 include_vendors 那關）
        self.assertNotIn("momo_test", {d["code"] for d in applicable_doc_types("shopee", "two_wheel_contract", "momo")})

    def test_uber_system_not_gated_by_client(self):
        self.assertIn("uber_system", {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract", "pchome")})
        self.assertIn("uber_system", {d["code"] for d in applicable_doc_types("ud", "two_wheel_contract", "")})


class DocStatusTests(unittest.TestCase):
    def test_id_number_kind_missing_when_blank(self):
        status = doc_status({"code": "id_card", "name": "身分證", "kind": "id_number"}, {"id_number": ""})
        self.assertTrue(status["missing"])

    def test_id_number_kind_missing_when_invalid_checksum(self):
        status = doc_status({"code": "id_card", "name": "身分證", "kind": "id_number"}, {"id_number": "A123456780"})
        self.assertTrue(status["missing"])

    def test_id_number_kind_not_missing_when_valid(self):
        status = doc_status({"code": "id_card", "name": "身分證", "kind": "id_number"}, {"id_number": "A123456789"})
        self.assertFalse(status["missing"])

    def test_checkbox_kind_missing_when_unchecked(self):
        status = doc_status({"code": "driver_license", "name": "駕照", "kind": "checkbox"}, {"documents": {}})
        self.assertTrue(status["missing"])

    def test_checkbox_kind_not_missing_when_checked(self):
        personnel = {"documents": {"driver_license": {"checked": True}}}
        status = doc_status({"code": "driver_license", "name": "駕照", "kind": "checkbox"}, personnel)
        self.assertFalse(status["missing"])

    def test_file_expiry_kind_missing_when_no_file(self):
        status = doc_status({"code": "insurance", "name": "強制險", "kind": "file_expiry"}, {"documents": {}})
        self.assertTrue(status["missing"])

    def test_file_expiry_kind_missing_when_expired(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        personnel = {"documents": {"insurance": {"file_path": "x.jpg", "expiry_date": past}}}
        status = doc_status({"code": "insurance", "name": "強制險", "kind": "file_expiry"}, personnel)
        self.assertTrue(status["missing"])
        self.assertTrue(status["expired"])

    def test_file_expiry_kind_not_missing_when_valid(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        personnel = {"documents": {"insurance": {"file_path": "x.jpg", "expiry_date": future}}}
        status = doc_status({"code": "insurance", "name": "強制險", "kind": "file_expiry"}, personnel)
        self.assertFalse(status["missing"])

    def test_file_kind_missing_when_no_file(self):
        status = doc_status({"code": "selfie_photo", "name": "自拍照", "kind": "file"}, {"documents": {}})
        self.assertTrue(status["missing"])

    def test_file_kind_not_missing_when_uploaded(self):
        personnel = {"documents": {"selfie_photo": {"file_path": "x.jpg"}}}
        status = doc_status({"code": "selfie_photo", "name": "自拍照", "kind": "file"}, personnel)
        self.assertFalse(status["missing"])
        # kind="file" 沒有到期日這個概念，不應該出現在回傳結果裡
        self.assertNotIn("expiry_date", status)
        self.assertNotIn("expired", status)


class MissingDocumentsIntegrationTests(unittest.TestCase):
    def test_shopee_two_wheel_contract_missing_list_excludes_police_clearance_and_liability(self):
        personnel = {
            "vendor": "shopee",
            "cooperation_type": "two_wheel_contract",
            "id_number": "",
            "documents": {},
        }
        missing_codes = {m["code"] for m in missing_documents(personnel)}
        self.assertNotIn("police_clearance", missing_codes)
        self.assertNotIn("liability_insurance", missing_codes)
        self.assertIn("insurance", missing_codes)
        self.assertIn("guild_insurance", missing_codes)

    def test_fully_complete_two_wheel_contract_at_shopee_has_no_missing(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        personnel = {
            "vendor": "shopee",
            "cooperation_type": "two_wheel_contract",
            "id_number": "A123456789",
            "documents": {
                "driver_license": {"checked": True},
                "contract": {"checked": True},
                "insurance": {"file_path": "a.jpg", "expiry_date": future},
                "guild_insurance": {"file_path": "b.jpg", "expiry_date": future},
            },
        }
        self.assertEqual(missing_documents(personnel), [])

    def test_all_document_statuses_length_matches_applicable_doc_types(self):
        personnel = {"vendor": "ud", "cooperation_type": "two_wheel_employed", "id_number": "", "documents": {}}
        statuses = all_document_statuses(personnel)
        expected = applicable_doc_types("ud", "two_wheel_employed")
        self.assertEqual(len(statuses), len(expected))


class PersonnelMatchesFiltersTests(unittest.TestCase):
    def test_complete_personnel_hidden_by_default(self):
        self.assertFalse(personnel_matches_filters({"name": "王小明", "phone": "0912345678"}, missing=[]))

    def test_complete_personnel_shown_when_name_searched(self):
        self.assertTrue(
            personnel_matches_filters({"name": "王小明", "phone": "0912345678"}, missing=[], name_keyword="王小明")
        )

    def test_incomplete_personnel_shown_by_default(self):
        self.assertTrue(personnel_matches_filters({"name": "王小明", "phone": "0912345678"}, missing=[{"code": "x"}]))

    def test_name_keyword_excludes_non_matching(self):
        self.assertFalse(
            personnel_matches_filters({"name": "王小明", "phone": "0912345678"}, missing=[{"code": "x"}], name_keyword="李小華")
        )

    def test_phone_keyword_excludes_non_matching(self):
        self.assertFalse(
            personnel_matches_filters({"name": "王小明", "phone": "0912345678"}, missing=[{"code": "x"}], phone_keyword="0900000000")
        )


if __name__ == "__main__":
    unittest.main()
