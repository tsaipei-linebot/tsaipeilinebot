import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import contract_summary_routes
import main
from fastapi.testclient import TestClient


def _manager(department="業務一部", is_platform_admin=False):
    return {"username": "carol", "rank": "manager", "department": department, "is_platform_admin": is_platform_admin}


def _staff(department="業務一部"):
    return {"username": "bob", "rank": "specialist", "department": department, "is_platform_admin": False}


def _admin():
    return {"username": "boss", "rank": "", "department": "", "is_platform_admin": True}


class ContractSummaryRoutingSmokeTests(unittest.TestCase):
    """跟其他模組的既有分工一致，只涵蓋不需要真的打 Firestore 的部分：
    未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/contract-summary", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/contract-summary")

    def test_export_client_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/contract-summary/export/client-contracts.xlsx", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/contract-summary")

    def test_export_dispatch_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/contract-summary/export/dispatch-contracts.xlsx", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/contract-summary")

    def test_export_merged_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/contract-summary/export/merged.xlsx", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/contract-summary")


class RequireAccessDependencyTests(unittest.TestCase):
    """_require_access()：2026-09-14 改版，完全看「服務部門」規則——
    專員、或部門沒有服務任何廠商的帳號一律導去 /portal，不再看有沒有
    開通合約產生器／派遣契約產生器模組。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = contract_summary_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/contract-summary")

    def test_no_department_access_redirects_to_portal(self):
        with mock.patch.object(contract_summary_routes, "build_vendor_lookup", return_value={}):
            with mock.patch.object(contract_summary_routes, "viewer_has_any_department_access", return_value=False):
                result = contract_summary_routes._require_access(self._FakeRequest(_staff()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_department_access_allows_through(self):
        with mock.patch.object(contract_summary_routes, "build_vendor_lookup", return_value={"v1": {}}):
            with mock.patch.object(contract_summary_routes, "viewer_has_any_department_access", return_value=True) as mock_check:
                result = contract_summary_routes._require_access(self._FakeRequest(_manager()))
        self.assertIsNone(result)
        mock_check.assert_called_once_with(_manager(), {"v1": {}})

    def test_platform_admin_always_has_access(self):
        with mock.patch.object(contract_summary_routes, "build_vendor_lookup", return_value={}):
            result = contract_summary_routes._require_access(self._FakeRequest(_admin()))
        self.assertIsNone(result)


class VisibleRecordsHelperTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = VisibleRecordsHelperTests._FakeSession({"user": user})

    def test_prepares_vendor_lookup_and_filters_both_record_types(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "build_vendor_lookup", return_value={"v1": {}}) as mock_lookup:
            with mock.patch.object(contract_summary_routes, "list_all_client_contracts", return_value=["c1"]) as mock_client:
                with mock.patch.object(contract_summary_routes, "list_all_dispatch_contracts", return_value=["d1"]) as mock_dispatch:
                    with mock.patch.object(contract_summary_routes, "visible_client_contract_records", return_value=["c1-visible"]) as mock_visible_client:
                        with mock.patch.object(contract_summary_routes, "visible_dispatch_contract_records", return_value=["d1-visible"]) as mock_visible_dispatch:
                            client_records, dispatch_records, near_limit_warning = contract_summary_routes._visible_records(account)
        mock_client.assert_called_once_with(limit=contract_summary_routes._RECORDS_LIMIT)
        mock_dispatch.assert_called_once_with(limit=contract_summary_routes._RECORDS_LIMIT)
        mock_visible_client.assert_called_once_with(["c1"], account, {"v1": {}})
        mock_visible_dispatch.assert_called_once_with(["d1"], account, {"v1": {}})
        self.assertEqual(client_records, ["c1-visible"])
        self.assertEqual(dispatch_records, ["d1-visible"])
        self.assertEqual(near_limit_warning, "")

    def test_near_limit_warning_uses_raw_counts_before_permission_filtering(self):
        """near_limit_warning 要看「權限過濾前」的原始筆數（系統整體資料量），
        不是這個帳號實際看得到的筆數，不然主管只看得到自己部門的一小部分，
        永遠不會觸發警示。"""
        account = _manager()
        raw_client = ["c"] * contract_summary_routes._NEAR_LIMIT_WARNING_THRESHOLD
        with mock.patch.object(contract_summary_routes, "build_vendor_lookup", return_value={}):
            with mock.patch.object(contract_summary_routes, "list_all_client_contracts", return_value=raw_client):
                with mock.patch.object(contract_summary_routes, "list_all_dispatch_contracts", return_value=[]):
                    with mock.patch.object(contract_summary_routes, "visible_client_contract_records", return_value=["only-one-visible"]):
                        with mock.patch.object(contract_summary_routes, "visible_dispatch_contract_records", return_value=[]):
                            _, _, near_limit_warning = contract_summary_routes._visible_records(account)
        self.assertNotEqual(near_limit_warning, "")


class NearLimitWarningTests(unittest.TestCase):
    def test_below_threshold_returns_empty_string(self):
        self.assertEqual(contract_summary_routes._near_limit_warning(0, 0), "")
        self.assertEqual(
            contract_summary_routes._near_limit_warning(
                contract_summary_routes._NEAR_LIMIT_WARNING_THRESHOLD - 1, 0
            ),
            "",
        )

    def test_client_total_at_threshold_triggers_warning(self):
        warning = contract_summary_routes._near_limit_warning(
            contract_summary_routes._NEAR_LIMIT_WARNING_THRESHOLD, 0
        )
        self.assertNotEqual(warning, "")

    def test_dispatch_total_at_threshold_triggers_warning(self):
        warning = contract_summary_routes._near_limit_warning(
            0, contract_summary_routes._NEAR_LIMIT_WARNING_THRESHOLD
        )
        self.assertNotEqual(warning, "")


class ContractSummaryHomeTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ContractSummaryHomeTests._FakeSession({"user": user})

    def test_builds_all_three_sections_from_visible_records(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "templates") as mock_templates:
            with mock.patch.object(contract_summary_routes, "_visible_records", return_value=(["c1"], ["d1"], "")):
                with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026, 2027]) as mock_years:
                    with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]) as mock_parse:
                        with mock.patch.object(contract_summary_routes, "build_client_contract_summary_rows", return_value=["crow"]) as mock_build_client:
                            with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]) as mock_build_dispatch:
                                with mock.patch.object(contract_summary_routes, "build_merged_summary_rows", return_value=(["mrow"], 2)) as mock_build_merged:
                                    contract_summary_routes.contract_summary_home(
                                        self._FakeRequest(account), years=["2026"], redirect=None,
                                    )
        mock_years.assert_called_once_with(["c1"])
        mock_parse.assert_called_once_with(["2026"], [2026, 2027])
        mock_build_client.assert_called_once_with(["c1"], [2026])
        mock_build_dispatch.assert_called_once_with(["d1"])
        mock_build_merged.assert_called_once_with(["c1"], ["d1"], [2026])
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["client_rows"], ["crow"])
        self.assertEqual(context["dispatch_rows"], ["drow"])
        self.assertEqual(context["merged_rows"], ["mrow"])
        self.assertEqual(list(context["shift_range"]), [0, 1])
        self.assertEqual(context["near_limit_warning"], "")

    def test_near_limit_warning_passed_through_to_template_context(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "templates") as mock_templates:
            with mock.patch.object(
                contract_summary_routes, "_visible_records", return_value=(["c1"], ["d1"], "接近上限警示文字")
            ):
                with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026]):
                    with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]):
                        with mock.patch.object(contract_summary_routes, "build_client_contract_summary_rows", return_value=["crow"]):
                            with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]):
                                with mock.patch.object(contract_summary_routes, "build_merged_summary_rows", return_value=(["mrow"], 2)):
                                    contract_summary_routes.contract_summary_home(
                                        self._FakeRequest(account), years=["2026"], redirect=None,
                                    )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["near_limit_warning"], "接近上限警示文字")


class ExportClientContractsTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ExportClientContractsTests._FakeSession({"user": user})

    def test_returns_xlsx_content(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "_visible_records", return_value=(["c1"], ["d1"], "")):
            with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026]):
                with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]):
                    with mock.patch.object(contract_summary_routes, "build_client_contract_summary_rows", return_value=["row"]):
                        with mock.patch.object(contract_summary_routes, "build_client_contract_summary_workbook", return_value=b"XLSX") as mock_build:
                            result = contract_summary_routes.contract_summary_export_client_contracts(
                                self._FakeRequest(account), years=["2026"], redirect=None,
                            )
        mock_build.assert_called_once_with(["row"], contract_summary_routes.CONTRACT_VERSIONS)
        self.assertEqual(result.body, b"XLSX")
        self.assertIn("attachment", result.headers["content-disposition"])


class ExportDispatchContractsTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ExportDispatchContractsTests._FakeSession({"user": user})

    def test_returns_xlsx_content(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "_visible_records", return_value=(["c1"], ["d1"], "")):
            with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]):
                with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_workbook", return_value=b"XLSX2") as mock_build:
                    result = contract_summary_routes.contract_summary_export_dispatch_contracts(
                        self._FakeRequest(account), redirect=None,
                    )
        mock_build.assert_called_once_with(["drow"])
        self.assertEqual(result.body, b"XLSX2")
        self.assertIn("attachment", result.headers["content-disposition"])


class ExportMergedTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ExportMergedTests._FakeSession({"user": user})

    def test_returns_xlsx_content(self):
        account = _manager()
        with mock.patch.object(contract_summary_routes, "_visible_records", return_value=(["c1"], ["d1"], "")):
            with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026]):
                with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]):
                    with mock.patch.object(contract_summary_routes, "build_merged_summary_rows", return_value=(["mrow"], 3)) as mock_build_rows:
                        with mock.patch.object(contract_summary_routes, "build_merged_summary_workbook", return_value=b"XLSX3") as mock_build:
                            result = contract_summary_routes.contract_summary_export_merged(
                                self._FakeRequest(account), years=["2026"], redirect=None,
                            )
        mock_build_rows.assert_called_once_with(["c1"], ["d1"], [2026])
        mock_build.assert_called_once_with(["mrow"], 3, contract_summary_routes.CONTRACT_VERSIONS)
        self.assertEqual(result.body, b"XLSX3")
        self.assertIn("attachment", result.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
