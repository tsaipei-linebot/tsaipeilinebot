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


def _manager_account(username, modules):
    return {"username": username, "modules": modules, "rank": "manager", "is_platform_admin": False}


def _staff_account(username, modules):
    return {"username": username, "modules": modules, "rank": "specialist", "is_platform_admin": False}


def _no_access_account(username):
    return {"username": username, "modules": [], "rank": "specialist", "is_platform_admin": False}


def _platform_admin_account(username):
    return {"username": username, "modules": [], "rank": "", "is_platform_admin": True}


class ContractSummaryRoutingSmokeTests(unittest.TestCase):
    """跟其他模組（client_contract_routes.py／dispatch_contract_routes.py）
    的既有分工一致，只涵蓋不需要真的打 Firestore 的部分：未登入時的導向。"""

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


class RequireAccessDependencyTests(unittest.TestCase):
    """_require_access()：專員（不管開放哪個模組）跟完全沒開放這兩個模組
    的帳號都導去 /portal，只有主管角色（其中一個模組是主管即可）或全平台
    管理員才放行——這是使用者明確要求的規則。"""

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

    def test_no_access_to_either_module_redirects_to_portal(self):
        result = contract_summary_routes._require_access(self._FakeRequest(_no_access_account("dave")))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_staff_role_in_both_modules_redirects_to_portal(self):
        account = _staff_account("bob", ["client_contracts", "dispatch_contracts"])
        result = contract_summary_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_manager_of_client_contracts_only_has_access(self):
        account = _manager_account("carol", ["client_contracts"])
        self.assertIsNone(contract_summary_routes._require_access(self._FakeRequest(account)))

    def test_manager_of_dispatch_contracts_only_has_access(self):
        account = _manager_account("carol", ["dispatch_contracts"])
        self.assertIsNone(contract_summary_routes._require_access(self._FakeRequest(account)))

    def test_platform_admin_always_has_access(self):
        self.assertIsNone(contract_summary_routes._require_access(self._FakeRequest(_platform_admin_account("boss"))))


class ContractSummaryHomeTests(unittest.TestCase):
    """只有帳號有存取權的那一半（合約產生器總表／派遣契約總表）才會去查
    對應產生器的紀錄——沒權限的那一半完全不用打 Firestore。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ContractSummaryHomeTests._FakeSession({"user": user})

    def test_only_queries_client_contracts_when_only_client_access(self):
        account = _manager_account("carol", ["client_contracts"])
        with mock.patch.object(contract_summary_routes, "templates") as mock_templates:
            with mock.patch.object(contract_summary_routes, "list_visible_client_contracts", return_value=["r1"]) as mock_client_list:
                with mock.patch.object(contract_summary_routes, "list_visible_dispatch_contracts") as mock_dispatch_list:
                    with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026, 2027]) as mock_years:
                        with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]) as mock_parse:
                            with mock.patch.object(contract_summary_routes, "build_client_contract_summary_rows", return_value=["row"]) as mock_build_client:
                                with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows") as mock_build_dispatch:
                                    contract_summary_routes.contract_summary_home(
                                        self._FakeRequest(account), years=["2026"], redirect=None,
                                    )
        mock_client_list.assert_called_once_with(account, limit=contract_summary_routes._RECORDS_LIMIT)
        mock_dispatch_list.assert_not_called()
        mock_years.assert_called_once_with(["r1"])
        mock_parse.assert_called_once_with(["2026"], [2026, 2027])
        mock_build_client.assert_called_once_with(["r1"], [2026])
        mock_build_dispatch.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["has_client_access"])
        self.assertFalse(context["has_dispatch_access"])
        self.assertEqual(context["client_rows"], ["row"])
        self.assertEqual(context["dispatch_rows"], [])

    def test_only_queries_dispatch_contracts_when_only_dispatch_access(self):
        account = _manager_account("carol", ["dispatch_contracts"])
        with mock.patch.object(contract_summary_routes, "templates"):
            with mock.patch.object(contract_summary_routes, "list_visible_client_contracts") as mock_client_list:
                with mock.patch.object(contract_summary_routes, "list_visible_dispatch_contracts", return_value=["d1"]) as mock_dispatch_list:
                    with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]) as mock_build_dispatch:
                        result_context = contract_summary_routes.contract_summary_home(
                            self._FakeRequest(account), years=[], redirect=None,
                        )
        mock_client_list.assert_not_called()
        mock_dispatch_list.assert_called_once_with(account, limit=contract_summary_routes._RECORDS_LIMIT)
        mock_build_dispatch.assert_called_once_with(["d1"])

    def test_both_access_queries_both(self):
        account = _platform_admin_account("boss")
        with mock.patch.object(contract_summary_routes, "templates") as mock_templates:
            with mock.patch.object(contract_summary_routes, "list_visible_client_contracts", return_value=["r1"]):
                with mock.patch.object(contract_summary_routes, "list_visible_dispatch_contracts", return_value=["d1"]):
                    with mock.patch.object(contract_summary_routes, "available_client_contract_years", return_value=[2026]):
                        with mock.patch.object(contract_summary_routes, "parse_selected_years", return_value=[2026]):
                            with mock.patch.object(contract_summary_routes, "build_client_contract_summary_rows", return_value=["row"]):
                                with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]):
                                    contract_summary_routes.contract_summary_home(
                                        self._FakeRequest(account), years=[], redirect=None,
                                    )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["has_client_access"])
        self.assertTrue(context["has_dispatch_access"])
        self.assertEqual(context["client_rows"], ["row"])
        self.assertEqual(context["dispatch_rows"], ["drow"])


class ExportClientContractsTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ExportClientContractsTests._FakeSession({"user": user})

    def test_returns_404_when_not_manager_of_client_contracts(self):
        account = _manager_account("carol", ["dispatch_contracts"])
        result = contract_summary_routes.contract_summary_export_client_contracts(
            self._FakeRequest(account), years=[], redirect=None,
        )
        self.assertEqual(result.status_code, 404)

    def test_returns_xlsx_content_when_manager_of_client_contracts(self):
        account = _manager_account("carol", ["client_contracts"])
        with mock.patch.object(contract_summary_routes, "list_visible_client_contracts", return_value=["r1"]):
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

    def test_returns_404_when_not_manager_of_dispatch_contracts(self):
        account = _manager_account("carol", ["client_contracts"])
        result = contract_summary_routes.contract_summary_export_dispatch_contracts(
            self._FakeRequest(account), redirect=None,
        )
        self.assertEqual(result.status_code, 404)

    def test_returns_xlsx_content_when_manager_of_dispatch_contracts(self):
        account = _manager_account("carol", ["dispatch_contracts"])
        with mock.patch.object(contract_summary_routes, "list_visible_dispatch_contracts", return_value=["d1"]):
            with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_rows", return_value=["drow"]):
                with mock.patch.object(contract_summary_routes, "build_dispatch_contract_summary_workbook", return_value=b"XLSX2") as mock_build:
                    result = contract_summary_routes.contract_summary_export_dispatch_contracts(
                        self._FakeRequest(account), redirect=None,
                    )
        mock_build.assert_called_once_with(["drow"])
        self.assertEqual(result.body, b"XLSX2")
        self.assertIn("attachment", result.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
