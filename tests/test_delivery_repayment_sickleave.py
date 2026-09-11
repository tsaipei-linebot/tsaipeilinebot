import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from datetime import date

from delivery.excel_export import build_repayment_workbook, build_sick_leave_workbook
from delivery.repository import (
    compute_annual_leave_days,
    leave_period_and_quota,
    leave_quota_summary_for_person,
    repayment_matches_filters,
    sick_leave_matches_filters,
    sick_leave_record_date,
    years_of_service_at,
)


class RepaymentMatchesFiltersTests(unittest.TestCase):
    def _record(self, **overrides):
        base = {"personnel_name": "王小明", "vendor": "shopee", "occurred_date": "2026-03-15"}
        base.update(overrides)
        return base

    def test_no_filters_matches(self):
        self.assertTrue(repayment_matches_filters(self._record()))

    def test_name_keyword_excludes_non_matching(self):
        self.assertFalse(repayment_matches_filters(self._record(), name_keyword="李小華"))

    def test_name_keyword_matches_substring(self):
        self.assertTrue(repayment_matches_filters(self._record(), name_keyword="小明"))

    def test_vendor_filter_excludes_non_matching(self):
        self.assertFalse(repayment_matches_filters(self._record(), vendor_filter="ud"))

    def test_vendor_filter_matches(self):
        self.assertTrue(repayment_matches_filters(self._record(), vendor_filter="shopee"))

    def test_month_filter_matches(self):
        self.assertTrue(repayment_matches_filters(self._record(), month_filter="2026-03"))

    def test_month_filter_excludes_non_matching_month(self):
        self.assertFalse(repayment_matches_filters(self._record(), month_filter="2026-04"))


class SickLeaveMatchesFiltersTests(unittest.TestCase):
    def _record(self, **overrides):
        base = {
            "personnel_name": "王小明",
            "vendor": "shopee",
            "leave_date": "2026-03-15",
            "hours": 8,
            "leave_type": "sick",
        }
        base.update(overrides)
        return base

    def test_no_filters_matches(self):
        self.assertTrue(sick_leave_matches_filters(self._record()))

    def test_name_keyword_excludes_non_matching(self):
        self.assertFalse(sick_leave_matches_filters(self._record(), name_keyword="李小華"))

    def test_vendor_filter_excludes_non_matching(self):
        self.assertFalse(sick_leave_matches_filters(self._record(), vendor_filter="ud"))

    def test_month_filter_matches_leave_date(self):
        self.assertTrue(sick_leave_matches_filters(self._record(), month_filter="2026-03"))

    def test_month_filter_excludes_non_matching_month(self):
        self.assertFalse(sick_leave_matches_filters(self._record(), month_filter="2026-04"))

    def test_leave_type_filter_matches(self):
        self.assertTrue(sick_leave_matches_filters(self._record(), leave_type_filter="sick"))

    def test_leave_type_filter_excludes_non_matching(self):
        self.assertFalse(sick_leave_matches_filters(self._record(), leave_type_filter="annual"))

    def test_old_schema_record_still_matches_by_start_date(self):
        old_record = {
            "personnel_name": "李小華",
            "vendor": "ud",
            "start_date": "2026-02-01",
            "end_date": "2026-02-02",
            "leave_type": "sick",
        }
        self.assertTrue(sick_leave_matches_filters(old_record, month_filter="2026-02"))


class SickLeaveRecordDateTests(unittest.TestCase):
    def test_uses_leave_date_when_present(self):
        self.assertEqual(sick_leave_record_date({"leave_date": "2026-03-15", "start_date": "2026-01-01"}), "2026-03-15")

    def test_falls_back_to_start_date_for_old_records(self):
        self.assertEqual(sick_leave_record_date({"start_date": "2026-01-01", "end_date": "2026-01-02"}), "2026-01-01")

    def test_empty_when_neither_present(self):
        self.assertEqual(sick_leave_record_date({}), "")


class AnnualLeaveQuotaTests(unittest.TestCase):
    def test_under_half_year_is_zero(self):
        self.assertEqual(compute_annual_leave_days(0.0), 0)

    def test_half_year_to_one_year_is_three(self):
        self.assertEqual(compute_annual_leave_days(0.5), 3)

    def test_one_to_two_years_is_seven(self):
        self.assertEqual(compute_annual_leave_days(1.0), 7)

    def test_two_to_three_years_is_ten(self):
        self.assertEqual(compute_annual_leave_days(2.0), 10)

    def test_three_to_five_years_is_fourteen(self):
        self.assertEqual(compute_annual_leave_days(3.0), 14)

    def test_five_to_ten_years_is_fifteen(self):
        self.assertEqual(compute_annual_leave_days(9.0), 15)

    def test_ten_years_is_sixteen(self):
        self.assertEqual(compute_annual_leave_days(10.0), 16)

    def test_eleven_years_is_seventeen(self):
        self.assertEqual(compute_annual_leave_days(11.0), 17)

    def test_caps_at_thirty(self):
        self.assertEqual(compute_annual_leave_days(40.0), 30)


class YearsOfServiceAtTests(unittest.TestCase):
    def test_before_hire_is_zero(self):
        self.assertEqual(years_of_service_at(date(2026, 1, 1), date(2025, 1, 1)), 0.0)

    def test_under_half_year(self):
        self.assertEqual(years_of_service_at(date(2026, 1, 1), date(2026, 3, 1)), 0.0)

    def test_over_half_year_under_one_year(self):
        self.assertEqual(years_of_service_at(date(2026, 1, 1), date(2026, 8, 1)), 0.5)

    def test_exactly_one_year(self):
        self.assertEqual(years_of_service_at(date(2025, 1, 1), date(2026, 1, 1)), 1.0)

    def test_ten_years(self):
        self.assertEqual(years_of_service_at(date(2016, 1, 1), date(2026, 1, 1)), 10.0)


class LeavePeriodAndQuotaTests(unittest.TestCase):
    def test_anniversary_without_hire_date_returns_none(self):
        start, end, quota = leave_period_and_quota("annual", None, date(2026, 6, 1))
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertIsNone(quota)

    def test_anniversary_period_and_quota(self):
        start, end, quota = leave_period_and_quota("annual", date(2020, 5, 1), date(2026, 6, 1))
        self.assertEqual(start, date(2026, 5, 1))
        self.assertEqual(end, date(2027, 4, 30))
        self.assertEqual(quota, 15)

    def test_calendar_period_and_fixed_quota(self):
        start, end, quota = leave_period_and_quota("personal", None, date(2026, 6, 1))
        self.assertEqual(start, date(2026, 1, 1))
        self.assertEqual(end, date(2026, 12, 31))
        self.assertEqual(quota, 14)

    def test_no_cap_leave_type_has_none_quota(self):
        _, _, quota = leave_period_and_quota("official", None, date(2026, 6, 1))
        self.assertIsNone(quota)


class LeaveQuotaSummaryForPersonTests(unittest.TestCase):
    def _record(self, leave_type, leave_date, hours):
        return {"leave_type": leave_type, "leave_date": leave_date, "hours": hours}

    def test_counts_hours_within_period_for_matching_type(self):
        records = [self._record("sick", "2026-03-01", 8), self._record("sick", "2026-01-01", 16)]
        summary = leave_quota_summary_for_person(records, date(2020, 1, 1), date(2026, 6, 1))
        sick_row = next(r for r in summary if r["leave_type"] == "sick")
        self.assertEqual(sick_row["days_used"], 3.0)
        self.assertEqual(sick_row["quota_days"], 30)

    def test_family_care_pools_into_personal_quota(self):
        records = [self._record("family_care", "2026-02-01", 40), self._record("personal", "2026-03-01", 8)]
        summary = leave_quota_summary_for_person(records, date(2020, 1, 1), date(2026, 6, 1))
        personal_row = next(r for r in summary if r["leave_type"] == "personal")
        family_row = next(r for r in summary if r["leave_type"] == "family_care")
        self.assertEqual(personal_row["days_used"], 6.0)  # 40h+8h = 48h = 6 天，全部算進事假額度
        self.assertEqual(family_row["days_used"], 5.0)  # 家庭照顧假本身只算自己的 40h = 5 天

    def test_excludes_old_schema_records_without_hours(self):
        records = [{"leave_type": "sick", "start_date": "2026-03-01", "end_date": "2026-03-02"}]
        summary = leave_quota_summary_for_person(records, date(2020, 1, 1), date(2026, 6, 1))
        sick_row = next(r for r in summary if r["leave_type"] == "sick")
        self.assertEqual(sick_row["days_used"], 0)

    def test_unlimited_leave_types_excluded_from_summary(self):
        summary = leave_quota_summary_for_person([], date(2020, 1, 1), date(2026, 6, 1))
        codes = {row["leave_type"] for row in summary}
        self.assertNotIn("official", codes)
        self.assertNotIn("other", codes)
        self.assertNotIn("parental_unpaid", codes)

    def test_returns_empty_when_hire_date_missing(self):
        records = [self._record("sick", "2026-03-01", 8)]
        summary = leave_quota_summary_for_person(records, None, date(2026, 6, 1))
        codes = {row["leave_type"] for row in summary}
        self.assertNotIn("annual", codes)
        self.assertIn("sick", codes)


class ExcelExportTests(unittest.TestCase):
    def test_repayment_workbook_contains_expected_rows(self):
        records = [
            {
                "occurred_date": "2026-03-15",
                "vendor": "shopee",
                "personnel_name": "王小明",
                "amount": 500,
                "reason": "遺失商品",
                "approved": True,
            }
        ]
        content = build_repayment_workbook(records)
        self.assertTrue(content.startswith(b"PK"))  # .xlsx 是 zip 格式，開頭一定是 PK

        import io
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        row = [cell.value for cell in ws[2]]
        self.assertEqual(header, ["日期", "廠商", "人員", "金額", "原因", "核准狀態"])
        self.assertEqual(row, ["2026-03-15", "蝦皮", "王小明", 500, "遺失商品", "已核准"])

    def test_sick_leave_workbook_contains_expected_rows(self):
        records = [
            {
                "leave_date": "2026-03-15",
                "hours": 8,
                "leave_type": "sick",
                "vendor": "ud",
                "personnel_name": "李小華",
                "reason": "感冒",
                "approved": False,
            }
        ]
        content = build_sick_leave_workbook(records)

        import io
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        row = [cell.value for cell in ws[2]]
        self.assertEqual(header, ["申請日期", "時數", "假別", "廠商", "人員", "原因", "核准狀態"])
        self.assertEqual(row, ["2026-03-15", 8, "病假", "UD", "李小華", "感冒", "未核准"])

    def test_sick_leave_workbook_falls_back_to_old_schema(self):
        records = [
            {
                "start_date": "2026-02-01",
                "end_date": "2026-02-02",
                "leave_type": "sick",
                "vendor": "ud",
                "personnel_name": "李小華",
                "reason": "感冒",
                "approved": False,
            }
        ]
        content = build_sick_leave_workbook(records)

        import io
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        row = [cell.value for cell in ws[2]]
        self.assertEqual(row[0], "2026-02-01 ~ 2026-02-02")


if __name__ == "__main__":
    unittest.main()
