import os
import sys
import unittest
from datetime import date, datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from services import contract_summary_service


def _manager(department="業務一部", is_platform_admin=False):
    return {"username": "carol", "rank": "manager", "department": department, "is_platform_admin": is_platform_admin}


def _staff(department="業務一部"):
    return {"username": "bob", "rank": "specialist", "department": department, "is_platform_admin": False}


def _admin():
    return {"username": "boss", "rank": "", "department": "", "is_platform_admin": True}


class BuildVendorLookupTests(unittest.TestCase):
    def test_keys_by_vendor_id(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "list_vendors",
                                return_value=[{"id": "v1", "name": "A"}, {"id": "v2", "name": "B"}]):
            lookup = contract_summary_service.build_vendor_lookup()
        self.assertEqual(lookup, {"v1": {"id": "v1", "name": "A"}, "v2": {"id": "v2", "name": "B"}})


class CanViewViaVendorDepartmentTests(unittest.TestCase):
    def test_platform_admin_always_true(self):
        self.assertTrue(contract_summary_service.can_view_via_vendor_department(_admin(), "", {}))

    def test_no_vendor_id_is_false(self):
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_manager(), "", {"v1": {}}))

    def test_staff_rank_is_false_even_if_department_matches(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_staff(), "v1", lookup))

    def test_manager_with_matching_department_is_true(self):
        lookup = {"v1": {"service_departments": ["業務一部", "業務二部"]}}
        self.assertTrue(contract_summary_service.can_view_via_vendor_department(_manager("業務一部"), "v1", lookup))

    def test_manager_with_non_matching_department_is_false(self):
        lookup = {"v1": {"service_departments": ["業務二部"]}}
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_manager("業務一部"), "v1", lookup))

    def test_manager_without_department_is_false(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_manager(""), "v1", lookup))

    def test_unknown_vendor_id_is_false(self):
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_manager(), "missing", {}))

    def test_blank_service_departments_is_false(self):
        lookup = {"v1": {"service_departments": []}}
        self.assertFalse(contract_summary_service.can_view_via_vendor_department(_manager("業務一部"), "v1", lookup))


class CanViewViaVendorDepartmentSingleTests(unittest.TestCase):
    def test_platform_admin_always_true_without_querying(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor") as mock_get:
            self.assertTrue(contract_summary_service.can_view_via_vendor_department_single(_admin(), "v1"))
        mock_get.assert_not_called()

    def test_no_vendor_id_is_false_without_querying(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor") as mock_get:
            self.assertFalse(contract_summary_service.can_view_via_vendor_department_single(_manager(), ""))
        mock_get.assert_not_called()

    def test_manager_with_matching_department_is_true(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor",
                                return_value={"service_departments": ["業務一部"]}):
            self.assertTrue(contract_summary_service.can_view_via_vendor_department_single(_manager("業務一部"), "v1"))

    def test_manager_with_non_matching_department_is_false(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor",
                                return_value={"service_departments": ["業務二部"]}):
            self.assertFalse(contract_summary_service.can_view_via_vendor_department_single(_manager("業務一部"), "v1"))

    def test_staff_rank_is_false(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor",
                                return_value={"service_departments": ["業務一部"]}):
            self.assertFalse(contract_summary_service.can_view_via_vendor_department_single(_staff(), "v1"))

    def test_missing_vendor_is_false(self):
        with mock.patch.object(contract_summary_service.platform_vendors, "get_vendor", return_value=None):
            self.assertFalse(contract_summary_service.can_view_via_vendor_department_single(_manager(), "v1"))


class ViewerCanLinkContractVendorTests(unittest.TestCase):
    """派遣契約產生器「選擇對應的合約」用：不要求主管職級，一般同仁也能
    連結自己部門負責的合約——跟 can_view_via_vendor_department() 不同。"""

    def test_platform_admin_always_true(self):
        self.assertTrue(contract_summary_service.viewer_can_link_contract_vendor(_admin(), "v1", {}))

    def test_staff_with_matching_department_is_true(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertTrue(contract_summary_service.viewer_can_link_contract_vendor(_staff("業務一部"), "v1", lookup))

    def test_manager_with_matching_department_is_also_true(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertTrue(contract_summary_service.viewer_can_link_contract_vendor(_manager("業務一部"), "v1", lookup))

    def test_non_matching_department_is_false(self):
        lookup = {"v1": {"service_departments": ["業務二部"]}}
        self.assertFalse(contract_summary_service.viewer_can_link_contract_vendor(_staff("業務一部"), "v1", lookup))

    def test_no_vendor_id_is_false(self):
        self.assertFalse(contract_summary_service.viewer_can_link_contract_vendor(_staff(), "", {}))

    def test_unknown_vendor_id_is_false(self):
        self.assertFalse(contract_summary_service.viewer_can_link_contract_vendor(_staff(), "missing", {}))

    def test_blank_department_is_false(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertFalse(contract_summary_service.viewer_can_link_contract_vendor(_staff(""), "v1", lookup))

    def test_vendor_with_no_service_departments_is_false(self):
        lookup = {"v1": {"service_departments": []}}
        self.assertFalse(contract_summary_service.viewer_can_link_contract_vendor(_staff("業務一部"), "v1", lookup))


class ViewerHasAnyDepartmentAccessTests(unittest.TestCase):
    def test_platform_admin_always_true(self):
        self.assertTrue(contract_summary_service.viewer_has_any_department_access(_admin(), {}))

    def test_staff_is_false(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        self.assertFalse(contract_summary_service.viewer_has_any_department_access(_staff(), lookup))

    def test_manager_with_no_matching_vendor_is_false(self):
        lookup = {"v1": {"service_departments": ["業務二部"]}}
        self.assertFalse(contract_summary_service.viewer_has_any_department_access(_manager("業務一部"), lookup))

    def test_manager_with_a_matching_vendor_is_true(self):
        lookup = {"v1": {"service_departments": ["業務二部"]}, "v2": {"service_departments": ["業務一部"]}}
        self.assertTrue(contract_summary_service.viewer_has_any_department_access(_manager("業務一部"), lookup))


class VisibleRecordsFilterTests(unittest.TestCase):
    def test_client_contract_records_filtered_by_vendor_department(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        records = [
            {"id": "c1", "vendor_id": "v1"},
            {"id": "c2", "vendor_id": "v-not-in-lookup"},
            {"id": "c3", "vendor_id": ""},
        ]
        result = contract_summary_service.visible_client_contract_records(records, _manager("業務一部"), lookup)
        self.assertEqual([r["id"] for r in result], ["c1"])

    def test_dispatch_contract_records_filtered_by_vendor_department(self):
        lookup = {"v1": {"service_departments": ["業務一部"]}}
        records = [{"id": "d1", "vendor_id": "v1"}, {"id": "d2", "vendor_id": ""}]
        result = contract_summary_service.visible_dispatch_contract_records(records, _manager("業務一部"), lookup)
        self.assertEqual([r["id"] for r in result], ["d1"])


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

    def test_includes_id_and_vendor_id_for_joining(self):
        records = [{"id": "c1", "vendor_id": "v1", "party_a_name": "A", "contract_start_date": "2026-01-01", "contract_version": ""}]
        rows = contract_summary_service.build_client_contract_summary_rows(records, [2026])
        self.assertEqual(rows[0]["id"], "c1")
        self.assertEqual(rows[0]["vendor_id"], "v1")

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


class DispatchGroupKeyTests(unittest.TestCase):
    def test_uses_linked_contract_id_and_submitter_when_present(self):
        record = {"linked_client_contract_id": "c1", "submitted_by": "alice", "client_name": "X"}
        self.assertEqual(contract_summary_service._dispatch_group_key(record), ("contract", "c1", "alice"))

    def test_falls_back_to_client_name_and_submitter(self):
        record = {"linked_client_contract_id": "", "submitted_by": "alice", "client_name": " pchome "}
        self.assertEqual(contract_summary_service._dispatch_group_key(record), ("name", "pchome", "alice"))


class BuildDispatchContractSummaryRowsTests(unittest.TestCase):
    def test_different_submitters_for_same_client_both_kept(self):
        records = [
            {
                "client_name": "pchome", "submitted_by": "alice", "linked_client_contract_id": "",
                "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc), "pay_cycle": "",
                "shifts": [{"title": "內湖店"}],
            },
            {
                "client_name": "pchome", "submitted_by": "carol", "linked_client_contract_id": "",
                "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc), "pay_cycle": "",
                "shifts": [{"title": "南港店"}],
            },
        ]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual(len(rows), 2)
        titles = {r["submitted_by"]: r["title"] for r in rows}
        self.assertEqual(titles["alice"], "內湖店")
        self.assertEqual(titles["carol"], "南港店")

    def test_only_keeps_the_first_seen_record_per_group(self):
        records = [
            {
                "client_name": "pchome", "submitted_by": "bob", "linked_client_contract_id": "",
                "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "pay_cycle": "每月10號", "shifts": [{"title": "日班新", "hours": "", "wage": "", "bonus": "", "overtime": ""}],
            },
            {
                "client_name": "pchome", "submitted_by": "bob", "linked_client_contract_id": "",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
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
                "client_name": "pchome", "submitted_by": "bob", "linked_client_contract_id": "",
                "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "pay_cycle": "每月10號",
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
        records = [{"client_name": "  ", "submitted_by": "bob", "linked_client_contract_id": "", "created_at": None, "shifts": [{"title": "x"}]}]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual(rows, [])

    def test_missing_created_at_gives_none_updated_year(self):
        records = [{"client_name": "pchome", "submitted_by": "bob", "linked_client_contract_id": "", "created_at": None, "pay_cycle": "", "shifts": [{"title": "日班"}]}]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertIsNone(rows[0]["updated_year"])

    def test_sorted_by_client_name_then_submitter(self):
        records = [
            {"client_name": "甲客戶", "submitted_by": "z", "linked_client_contract_id": "", "created_at": None, "pay_cycle": "", "shifts": [{"title": "y"}]},
            {"client_name": "乙客戶", "submitted_by": "a", "linked_client_contract_id": "", "created_at": None, "pay_cycle": "", "shifts": [{"title": "x"}]},
        ]
        rows = contract_summary_service.build_dispatch_contract_summary_rows(records)
        self.assertEqual([r["client_name"] for r in rows], ["乙客戶", "甲客戶"])


class BuildMergedSummaryRowsTests(unittest.TestCase):
    def _client_record(self, cid="c1", name="A公司", year="2026"):
        return {
            "id": cid, "vendor_id": "v1", "party_a_name": name, "contract_start_date": f"{year}-01-01",
            "contract_version": "hourly_flat_rate", "hourly_wage": "196", "management_fee": "20",
        }

    def _dispatch_record(self, linked_id="c1", submitted_by="bob", shifts=None, created=None):
        return {
            "vendor_id": "v1", "client_name": "A公司", "submitted_by": submitted_by,
            "linked_client_contract_id": linked_id, "pay_cycle": "每月10號",
            "created_at": created or datetime(2026, 6, 1, tzinfo=timezone.utc),
            "shifts": shifts if shifts is not None else [{"title": "日班", "hours": "9-18", "wage": "196", "bonus": "－", "overtime": "－"}],
        }

    def test_contract_without_matching_dispatch_gets_blank_columns(self):
        rows, max_shifts = contract_summary_service.build_merged_summary_rows(
            [self._client_record()], [], [2026],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dispatch_pay_cycle"], "")
        self.assertEqual(max_shifts, 1)
        self.assertEqual(rows[0]["shift_columns"], [{"title": "", "hours": "", "wage": "", "bonus": "", "overtime": ""}])

    def test_dispatch_without_linked_contract_id_is_ignored(self):
        dispatch = self._dispatch_record(linked_id="")
        rows, _ = contract_summary_service.build_merged_summary_rows([self._client_record()], [dispatch], [2026])
        self.assertEqual(rows[0]["dispatch_pay_cycle"], "")

    def test_matching_dispatch_fills_shift_columns(self):
        dispatch = self._dispatch_record()
        rows, max_shifts = contract_summary_service.build_merged_summary_rows(
            [self._client_record()], [dispatch], [2026],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dispatch_pay_cycle"], "每月10號")
        self.assertEqual(rows[0]["shift_columns"][0]["title"], "日班")

    def test_multiple_submitters_under_same_contract_repeat_the_row(self):
        dispatch1 = self._dispatch_record(submitted_by="alice", shifts=[{"title": "內湖店", "hours": "", "wage": "", "bonus": "", "overtime": ""}])
        dispatch2 = self._dispatch_record(submitted_by="carol", shifts=[{"title": "南港店", "hours": "", "wage": "", "bonus": "", "overtime": ""}])
        rows, _ = contract_summary_service.build_merged_summary_rows(
            [self._client_record()], [dispatch1, dispatch2], [2026],
        )
        self.assertEqual(len(rows), 2)
        submitters = {r["dispatch_submitted_by"] for r in rows}
        self.assertEqual(submitters, {"alice", "carol"})

    def test_max_shift_column_count_matches_largest_matched_dispatch(self):
        dispatch = self._dispatch_record(shifts=[
            {"title": "日班", "hours": "", "wage": "", "bonus": "", "overtime": ""},
            {"title": "夜班", "hours": "", "wage": "", "bonus": "", "overtime": ""},
            {"title": "假日班", "hours": "", "wage": "", "bonus": "", "overtime": ""},
        ])
        rows, max_shifts = contract_summary_service.build_merged_summary_rows(
            [self._client_record()], [dispatch], [2026],
        )
        self.assertEqual(max_shifts, 3)
        self.assertEqual(len(rows[0]["shift_columns"]), 3)
        self.assertEqual(rows[0]["shift_columns"][2]["title"], "假日班")


if __name__ == "__main__":
    unittest.main()
