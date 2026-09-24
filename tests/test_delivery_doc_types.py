"""配送部人員「到期狀況」（2026-09-24 改版）：只追蹤 4 種證明、只看廠商、
只填日期。規則見 delivery/config.py 的 DOC_TYPES。"""
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
    personnel_employment_status,
    personnel_matches_search,
)


def _names(vendor, cooperation_type=""):
    return sorted((d["name"], d.get("required", True)) for d in applicable_doc_types(vendor, cooperation_type))


class ApplicableDocTypesTests(unittest.TestCase):
    """使用者 2026-09-24 逐項確認的對照表。"""

    def test_shopee_contract(self):
        self.assertEqual(_names("shopee_contract"), [("公會加保證明", False), ("強制險", True)])

    def test_sf_has_no_police_clearance(self):
        self.assertEqual(_names("sf"), [("公會加保證明", False), ("強制險", True)])

    def test_ud_and_uc_only_police_clearance(self):
        self.assertEqual(_names("ud"), [("良民證", True)])
        self.assertEqual(_names("uc"), [("良民證", True)])

    def test_shopee_employed_own_car(self):
        self.assertEqual(_names("shopee_employed_own_car"), [("強制險", True), ("營業用第三責任險", True)])

    def test_other_shopee_vendors_track_nothing(self):
        for vendor in ("shopee", "shopee_company_car", "shopee_speed_warehouse"):
            self.assertEqual(applicable_doc_types(vendor), [], vendor)

    def test_cooperation_type_no_longer_adds_items(self):
        """原本合作方式選二輪承攬/二輪雇傭會另外加強制險等項目，使用者確認拿掉。"""
        self.assertEqual(_names("ud", "two_wheel_employed"), [("良民證", True)])
        self.assertEqual(_names("shopee", "two_wheel_contract"), [])

    def test_old_document_codes_are_kept_so_existing_dates_carry_over(self):
        codes = {d["code"] for d in applicable_doc_types("sf")} | {d["code"] for d in applicable_doc_types("ud")}
        self.assertEqual(codes, {"sf_insurance", "sf_guild_insurance", "police_clearance"})


def _doc(required=True):
    return {"code": "sf_insurance", "name": "強制險", "required": required}


def _person(expiry):
    return {"documents": {"sf_insurance": {"expiry_date": expiry}} if expiry is not None else {}}


class DocStatusTests(unittest.TestCase):
    today = date(2026, 9, 24)

    def _status(self, expiry, required=True):
        return doc_status(_doc(required), _person(expiry), today=self.today)

    def test_unfilled_required_is_a_problem(self):
        status = self._status(None)
        self.assertEqual(status["state"], "unfilled")
        self.assertTrue(status["missing"])

    def test_unfilled_optional_is_not_a_problem(self):
        status = self._status(None, required=False)
        self.assertEqual(status["state"], "unfilled")
        self.assertFalse(status["missing"])

    def test_ok_when_more_than_30_days_left(self):
        status = self._status((self.today + timedelta(days=31)).isoformat())
        self.assertEqual(status["state"], "ok")
        self.assertFalse(status["missing"])

    def test_expiring_within_30_days_is_not_yet_a_problem(self):
        status = self._status((self.today + timedelta(days=30)).isoformat())
        self.assertEqual(status["state"], "expiring")
        self.assertFalse(status["missing"])

    def test_expired_is_a_problem_even_when_optional(self):
        status = self._status((self.today - timedelta(days=1)).isoformat(), required=False)
        self.assertEqual(status["state"], "expired")
        self.assertTrue(status["missing"])

    def test_expires_today_is_still_valid(self):
        self.assertEqual(self._status(self.today.isoformat())["state"], "expiring")

    def test_garbage_date_counts_as_unfilled(self):
        status = self._status("not-a-date")
        self.assertEqual(status["state"], "unfilled")
        self.assertEqual(status["expiry_date"], "")

    def test_old_uploaded_file_alone_no_longer_counts(self):
        """以前只上傳照片、沒填日期的，改版後算「未填」（不再上傳照片）。"""
        person = {"documents": {"sf_insurance": {"file_path": "personnel-docs/x/a.jpg"}}}
        self.assertEqual(doc_status(_doc(), person, today=self.today)["state"], "unfilled")


class MissingDocumentsTests(unittest.TestCase):
    def test_sf_with_insurance_filled_has_no_problem_even_without_optional_guild(self):
        person = {"vendor": "sf", "documents": {"sf_insurance": {"expiry_date": "2099-01-01"}}}
        self.assertEqual(missing_documents(person), [])
        self.assertEqual(len(all_document_statuses(person)), 2)

    def test_ud_without_police_clearance_is_missing(self):
        self.assertEqual([m["code"] for m in missing_documents({"vendor": "ud"})], ["police_clearance"])

    def test_vendor_without_tracked_items_never_missing(self):
        self.assertEqual(missing_documents({"vendor": "shopee"}), [])


class PersonnelMatchesSearchTests(unittest.TestCase):
    def _p(self, status, name="王小明", phone="0912345678", vendor="ud"):
        return {"name": name, "phone": phone, "vendor": vendor, "employment_status": status}

    def test_pending_and_employed_shown_by_default(self):
        self.assertTrue(personnel_matches_search(self._p("pending_onboard")))
        self.assertTrue(personnel_matches_search(self._p("employed")))

    def test_withdrawn_and_resigned_hidden_by_default(self):
        self.assertFalse(personnel_matches_search(self._p("onboard_withdrawn")))
        self.assertFalse(personnel_matches_search(self._p("resigned")))

    def test_hidden_statuses_shown_when_searching_name_or_phone(self):
        self.assertTrue(personnel_matches_search(self._p("resigned"), name="小明"))
        self.assertTrue(personnel_matches_search(self._p("onboard_withdrawn"), phone="0912"))

    def test_status_filter_is_exact(self):
        self.assertTrue(personnel_matches_search(self._p("resigned"), employment_status="resigned"))
        self.assertFalse(personnel_matches_search(self._p("employed"), employment_status="resigned"))

    def test_name_phone_vendor_filters(self):
        person = self._p("employed")
        self.assertFalse(personnel_matches_search(person, name="陳"))
        self.assertFalse(personnel_matches_search(person, phone="0988"))
        self.assertFalse(personnel_matches_search(person, vendor="sf"))
        self.assertTrue(personnel_matches_search(person, name="王", phone="0912", vendor="ud"))

    def test_legacy_personnel_without_status_is_employed(self):
        person = {"name": "老員工", "vendor": "ud"}
        self.assertEqual(personnel_employment_status(person), "employed")
        self.assertTrue(personnel_matches_search(person))


if __name__ == "__main__":
    unittest.main()
