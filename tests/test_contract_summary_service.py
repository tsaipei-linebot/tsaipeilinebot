import os
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from services import contract_summary_service


class AvailableClientContractYearsTests(unittest.TestCase):
    def test_always_includes_this_year_and_next_year(self):
        years = contract_summary_service.available_client_contract_years([], today=date(2026, 9, 13))
        self.assertEqual(years, [2027, 2026])

    def test_includes_years_found_in_records(self):
        records = [{"contract_start_date": "2024-03-01"}, {"contract_start_date": "2027-01-01"}]
        years = contract_summary_service.available_client_contract_years(records, today=date(2026, 9, 13))
        self.assertEqual(years, [2027, 2026, 2024])

    def test_ignores_records_without_a_parsable_year(self):
        records = [{"contract_start_date": ""}, {"contract_start_date": None}]
        years = contract_summary_service.available_client_contract_years(records, today=date(2026, 9, 13))
        self.assertEqual(years, [2027, 2026])


class DefaultSelectedYearsTests(unittest.TestCase):
    def test_returns_this_year_and_next_year(self):
        self.assertEqual(contract_summary_service.default_selected_years(date(2026, 9, 13)), [2026, 2027])


class ParseSelectedYearsTests(unittest.TestCase):
    def test_empty_raw_years_falls_back_to_default_within_available(self):
        result = contract_summary_service.parse_selected_years([], [2026, 2025])
        self.assertEqual(result, [2026])

    def test_ignores_years_not_in_available_years(self):
        result = contract_summary_service.parse_selected_years(["2026", "1999"], [2026, 2027])
        self.assertEqual(result, [2026])

    def test_ignores_non_numeric_values(self):
        result = contract_summary_service.parse_selected_years(["abc"], [2026, 2027])
        self.assertEqual(result, [2027, 2026])

    def test_deduplicates_and_sorts_descending(self):
        result = contract_summary_service.parse_selected_years(["2025", "2027", "2025"], [2025, 2026, 2027])
        self.assertEqual(result, [2027, 2025])


class BuildClientContractSummaryRowsTests(unittest.TestCase):
    def test_filters_by_selected_years_only(self):
        records = [
            {"party_a_name": "A公司", "contract_start_date": "2026-01-01", "contract_version": "hourly_flat_rate"},
            {"party_a_name": "B公司", "contract_start_date": "2027-01-01", "contract_version": "actual_paid"},
        ]
        rows = contract_summary_service.build_client_contract_summary_rows(records, [2026])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_name"], "A公司")

    def test_pricing_summary_by_version(self):
        records = [
            {
                "party_a_name": "A", "contract_start_date": "2026-06-01", "contract_version": "hourly_flat_rate",
                "hourly_wage": "196", "management_fee": "20",
            },
            {
                "party_a_name": "B", "contract_start_date": "2026-06-01", "contract_version": "actual_paid",
                "service_fee": "人員薪資的15%",
            },
            {
                "party_a_name": "C", "contract_start_date": "2026-06-01", "contract_version": "white_collar_referral",
                "fee_amount": "2500元", "service_months": "3",
            },
            {
                "party_a_name": "D", "contract_start_date": "2026-06-01", "contract_version": "taiwanese_referral",
                "referral_fee_percentage": "15%", "referral_service_months": "6",
            },
        ]
        rows = contract_summary_service.build_client_contract_summary_rows(records, [2026])
        summaries = {r["client_name"]: r["pricing_summary"] for r in rows}
        self.assertEqual(summaries["A"], "時薪 196／管理費 20")
        self.assertEqual(summaries["B"], "服務費：人員薪資的15%")
        self.assertEqual(summaries["C"], "服務費 2500元／收費上限 3 個月")
        self.assertEqual(summaries["D"], "服務費 15%／收費上限 6 個月")

    def test_sorted_by_year_descending_then_client_name(self):
        records = [
            {"party_a_name": "乙公司", "contract_start_date": "2026-01-01", "contract_version": ""},
            {"party_a_name": "甲公司", "contract_start_date": "2027-01-01", "contract_version": ""},
            {"party_a_name": "丙公司", "contract_start_date": "2026-01-01", "contract_version": ""},
        ]
        rows = contract_summary_service.build_client_contract_summary_rows(records, [2026, 2027])
        self.assertEqual([r["client_name"] for r in rows], ["甲公司", "丙公司", "乙公司"])

    def test_records_missing_a_parsable_year_are_excluded(self):
        records = [{"party_a_name": "A", "contract_start_date": "", "contract_version": ""}]
        rows = contract_summary_service.build_client_contract_summary_rows(records, [2026])
        self.assertEqual(rows, [])


class BuildDispatchContractSummaryRowsTests(unittest.TestCase):
    def test_only_keeps_the_first_seen_record_per_client(self):
        records = [
            {
                "client_name": "pchome", "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "pay_cycle": "每月10號", "shifts": [{"title": "日班新", "hours": "", "wage": "", "bonus": "", "overtime": ""}],
            },
            {
                "client_name": "pchome", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "pay_cycle": "每月5號", "shifts": [{"title": "日班舊", "hours": "", "wage": "", "bonus": "", "overtime": ""}],
            },
        ]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "日班新")
        self.assertEqual(rows[0]["updated_year"], 2026)

    def test_each_shift_becomes_its_own_row(self):
        records = [
            {
                "client_name": "pchome", "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "pay_cycle": "每月10號",
                "shifts": [
                    {"title": "日班", "hours": "9-18", "wage": "196", "bonus": "－", "overtime": "－"},
                    {"title": "夜班", "hours": "22-7", "wage": "210", "bonus": "－", "overtime": "－"},
                ],
            },
        ]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["title"] for r in rows], ["日班", "夜班"])

    def test_blank_client_name_is_skipped(self):
        records = [{"client_name": "  ", "created_at": None, "shifts": [{"title": "x"}]}]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual(rows, [])

    def test_missing_created_at_gives_none_updated_year(self):
        records = [{"client_name": "pchome", "created_at": None, "pay_cycle": "", "shifts": [{"title": "日班"}]}]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertIsNone(rows[0]["updated_year"])

    def test_sorted_by_client_name(self):
        # 甲 (U+7532) 排序在 乙 (U+4E59) 之後，所以刻意用「甲客戶」先出現的
        # 輸入順序，確認輸出真的有被排序過，不是單純維持輸入順序。
        records = [
            {"client_name": "甲客戶", "created_at": None, "pay_cycle": "", "shifts": [{"title": "y"}]},
            {"client_name": "乙客戶", "created_at": None, "pay_cycle": "", "shifts": [{"title": "x"}]},
        ]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual([r["client_name"] for r in rows], ["乙客戶", "甲客戶"])


if __name__ == "__main__":
    unittest.main()
