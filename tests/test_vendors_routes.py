import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import main
import vendors_routes
from fastapi.testclient import TestClient


class VendorRoutingSmokeTests(unittest.TestCase):
    """/vendors 是全平台管理員專用的廠商主檔管理頁面，跟
    test_company_routes.py 的既有分工一致，只涵蓋不需要真的打 Firestore
    的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_vendors_list_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/vendors/", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))

    def test_new_vendor_form_redirects_to_portal_when_not_authenticated(self):
        resp = self.client.get("/vendors/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].endswith("/portal"))


class FieldsFromFormTests(unittest.TestCase):
    """_fields_from_form()：service_departments 是多選（清單），不在
    platform_vendors.FIELDS 那組簡單字串欄位裡，要另外用 getlist() 收。"""

    class _FakeForm(dict):
        def getlist(self, key):
            value = self.get(key)
            return list(value) if isinstance(value, list) else []

    def test_collects_service_departments_via_getlist(self):
        form = self._FakeForm({"code": "shopee", "name": "蝦皮", "service_departments": ["業務一部", "業務二部"]})
        fields = vendors_routes._fields_from_form(form)
        self.assertEqual(fields["service_departments"], ["業務一部", "業務二部"])
        self.assertEqual(fields["code"], "shopee")

    def test_missing_service_departments_becomes_empty_list(self):
        form = self._FakeForm({"code": "shopee", "name": "蝦皮"})
        fields = vendors_routes._fields_from_form(form)
        self.assertEqual(fields["service_departments"], [])


class ClientContractByVendorIdTests(unittest.TestCase):
    def test_keys_by_vendor_id_first_match_wins(self):
        records = [
            {"id": "c1", "vendor_id": "v1"},
            {"id": "c2", "vendor_id": "v2"},
            {"id": "c3", "vendor_id": "v1"},  # 理論上不會發生（合約一對一新建廠商），保險起見測第一筆優先
        ]
        with mock.patch.object(vendors_routes, "list_client_contract_submissions", return_value=records):
            result = vendors_routes._client_contract_by_vendor_id()
        self.assertEqual(result["v1"]["id"], "c1")
        self.assertEqual(result["v2"]["id"], "c2")

    def test_ignores_records_without_vendor_id(self):
        with mock.patch.object(vendors_routes, "list_client_contract_submissions", return_value=[{"id": "c1", "vendor_id": ""}]):
            result = vendors_routes._client_contract_by_vendor_id()
        self.assertEqual(result, {})


class DispatchContractsByVendorIdTests(unittest.TestCase):
    def test_groups_by_vendor_and_keeps_first_per_submitter(self):
        records = [
            {"id": "d1", "vendor_id": "v1", "submitted_by": "alice"},
            {"id": "d2", "vendor_id": "v1", "submitted_by": "alice"},  # 同一人較舊的一筆要被跳過（假設清單已新到舊排序）
            {"id": "d3", "vendor_id": "v1", "submitted_by": "carol"},
            {"id": "d4", "vendor_id": "v2", "submitted_by": "bob"},
        ]
        with mock.patch.object(vendors_routes, "list_dispatch_contract_submissions", return_value=records):
            result = vendors_routes._dispatch_contracts_by_vendor_id()
        self.assertEqual([r["id"] for r in result["v1"]], ["d1", "d3"])
        self.assertEqual([r["id"] for r in result["v2"]], ["d4"])

    def test_ignores_records_without_vendor_id(self):
        with mock.patch.object(vendors_routes, "list_dispatch_contract_submissions", return_value=[{"id": "d1", "vendor_id": "", "submitted_by": "bob"}]):
            result = vendors_routes._dispatch_contracts_by_vendor_id()
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
