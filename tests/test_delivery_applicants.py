import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.repository import applicant_matches_filters, applicant_needs_test_drive, normalize_applicant_status


class NormalizeApplicantStatusTests(unittest.TestCase):
    def test_new_style_status_field_wins(self):
        self.assertEqual(normalize_applicant_status({"status": "interviewed"}), "interviewed")

    def test_falls_back_to_legacy_hired_flag(self):
        self.assertEqual(normalize_applicant_status({"hired": True}), "hired")

    def test_falls_back_to_legacy_withdrawn_flag(self):
        self.assertEqual(normalize_applicant_status({"withdrawn": True}), "withdrawn")

    def test_falls_back_to_legacy_interviewed_flag(self):
        self.assertEqual(normalize_applicant_status({"interviewed": True}), "interviewed")

    def test_defaults_to_not_interviewed(self):
        self.assertEqual(normalize_applicant_status({}), "not_interviewed")

    def test_hired_takes_priority_over_other_legacy_flags(self):
        self.assertEqual(normalize_applicant_status({"hired": True, "interviewed": True}), "hired")


class ApplicantMatchesFiltersTests(unittest.TestCase):
    def _applicant(self, **overrides):
        base = {"name": "王小明", "phone": "0912345678", "status": "not_interviewed"}
        base.update(overrides)
        return base

    def test_not_interviewed_shows_by_default(self):
        self.assertTrue(applicant_matches_filters(self._applicant()))

    def test_withdrawn_hidden_by_default(self):
        applicant = self._applicant(status="withdrawn")
        self.assertFalse(applicant_matches_filters(applicant))

    def test_withdrawn_shown_when_searching_by_name(self):
        applicant = self._applicant(status="withdrawn")
        self.assertTrue(applicant_matches_filters(applicant, name_keyword="王小明"))

    def test_withdrawn_shown_when_explicitly_filtering_status(self):
        applicant = self._applicant(status="withdrawn")
        self.assertTrue(applicant_matches_filters(applicant, status_filter="withdrawn"))

    def test_name_keyword_excludes_non_matching(self):
        applicant = self._applicant()
        self.assertFalse(applicant_matches_filters(applicant, name_keyword="李小華"))

    def test_phone_keyword_excludes_non_matching(self):
        applicant = self._applicant()
        self.assertFalse(applicant_matches_filters(applicant, phone_keyword="0900000000"))

    def test_status_filter_excludes_non_matching_status(self):
        applicant = self._applicant(status="interviewed")
        self.assertFalse(applicant_matches_filters(applicant, status_filter="hired"))

    def test_status_filter_matching_status_passes(self):
        applicant = self._applicant(status="interviewed")
        self.assertTrue(applicant_matches_filters(applicant, status_filter="interviewed"))

    def test_unspecified_vendor_shown_by_default(self):
        # 廠商還沒判斷出來的人正常顯示，不特別隱藏。
        applicant = self._applicant(vendor="")
        self.assertTrue(applicant_matches_filters(applicant))

    def test_vendor_filter_excludes_non_matching(self):
        applicant = self._applicant(vendor="ud")
        self.assertFalse(applicant_matches_filters(applicant, vendor_filter="shopee"))

    def test_vendor_filter_matching_passes(self):
        applicant = self._applicant(vendor="ud")
        self.assertTrue(applicant_matches_filters(applicant, vendor_filter="ud"))


class ApplicantNeedsTestDriveTests(unittest.TestCase):
    def test_ud_always_needs_test_drive(self):
        self.assertTrue(applicant_needs_test_drive("ud", ""))
        self.assertTrue(applicant_needs_test_drive("ud", "two_wheel_contract"))

    def test_uc_always_needs_test_drive(self):
        self.assertTrue(applicant_needs_test_drive("uc", ""))

    def test_sf_never_needs_test_drive(self):
        self.assertFalse(applicant_needs_test_drive("sf", ""))
        self.assertFalse(applicant_needs_test_drive("sf", "three_wheel_employed"))

    def test_shopee_needs_test_drive_only_for_three_wheel_employed(self):
        self.assertTrue(applicant_needs_test_drive("shopee", "three_wheel_employed"))
        self.assertFalse(applicant_needs_test_drive("shopee", "two_wheel_contract"))
        self.assertFalse(applicant_needs_test_drive("shopee", "two_wheel_employed"))
        self.assertFalse(applicant_needs_test_drive("shopee", ""))

    def test_unspecified_vendor_does_not_need_test_drive(self):
        self.assertFalse(applicant_needs_test_drive("", ""))


if __name__ == "__main__":
    unittest.main()
