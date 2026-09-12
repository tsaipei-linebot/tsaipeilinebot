import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from services.company_registry_lookup import lookup_company


def _fake_response(json_data=None, raise_for_status_error=None):
    resp = mock.Mock()
    if raise_for_status_error:
        resp.raise_for_status.side_effect = raise_for_status_error
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


class LookupCompanyTests(unittest.TestCase):
    def test_blank_query_returns_none(self):
        self.assertIsNone(lookup_company(""))
        self.assertIsNone(lookup_company("   "))

    def test_tax_id_query_uses_gcis_first(self):
        gcis_record = [{
            "Company_Name": "測試股份有限公司",
            "Responsible_Name": "陳大明",
            "Company_Location": "台北市信義區忠孝東路一段1號",
            "Business_Accounting_NO": "12345678",
        }]
        with mock.patch("services.company_registry_lookup.requests.get",
                         return_value=_fake_response(gcis_record)) as mock_get:
            result = lookup_company("12345678")
        mock_get.assert_called_once()
        self.assertIn("gcis.nat.gov.tw", mock_get.call_args[0][0])
        self.assertEqual(result, {
            "name": "測試股份有限公司", "representative": "陳大明",
            "address": "台北市信義區忠孝東路一段1號", "tax_id": "12345678",
        })

    def test_tax_id_query_falls_back_to_g0v_when_gcis_empty(self):
        g0v_record = {"data": [{
            "Company_Name": "另一間公司",
            "Responsible_Name": "李小華",
            "Company_Location": "新北市板橋區文化路一段1號",
            "Business_Accounting_NO": "87654321",
        }]}

        def _side_effect(url, **kwargs):
            if "gcis" in url:
                return _fake_response([])
            return _fake_response(g0v_record)

        with mock.patch("services.company_registry_lookup.requests.get", side_effect=_side_effect) as mock_get:
            result = lookup_company("87654321")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(result["name"], "另一間公司")

    def test_name_query_skips_gcis_uses_g0v_directly(self):
        g0v_record = [{"Company_Name": "瑋政有限公司", "Responsible_Name": "蔡志祥",
                        "Company_Location": "新北市板橋區文化路二段90號五樓", "Business_Accounting_NO": "68138452"}]
        with mock.patch("services.company_registry_lookup.requests.get",
                         return_value=_fake_response(g0v_record)) as mock_get:
            result = lookup_company("瑋政有限公司")
        mock_get.assert_called_once()
        self.assertIn("ronny.tw", mock_get.call_args[0][0])
        self.assertEqual(result["name"], "瑋政有限公司")

    def test_both_sources_empty_returns_none(self):
        with mock.patch("services.company_registry_lookup.requests.get", return_value=_fake_response([])):
            result = lookup_company("查無此公司")
        self.assertIsNone(result)

    def test_network_error_is_swallowed_and_returns_none(self):
        with mock.patch("services.company_registry_lookup.requests.get",
                         side_effect=requests.ConnectionError("no route")):
            result = lookup_company("瑋政有限公司")
        self.assertIsNone(result)

    def test_http_error_is_swallowed_and_returns_none(self):
        with mock.patch("services.company_registry_lookup.requests.get",
                         return_value=_fake_response(raise_for_status_error=requests.HTTPError("500"))):
            result = lookup_company("瑋政有限公司")
        self.assertIsNone(result)

    def test_unparseable_json_is_swallowed_and_returns_none(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("not json")
        with mock.patch("services.company_registry_lookup.requests.get", return_value=resp):
            result = lookup_company("瑋政有限公司")
        self.assertIsNone(result)

    def test_unexpected_response_shape_returns_none(self):
        with mock.patch("services.company_registry_lookup.requests.get",
                         return_value=_fake_response("not a list or dict")):
            result = lookup_company("瑋政有限公司")
        self.assertIsNone(result)

    def test_record_missing_name_field_is_treated_as_not_found(self):
        with mock.patch("services.company_registry_lookup.requests.get",
                         return_value=_fake_response([{"Responsible_Name": "無名氏"}])):
            result = lookup_company("瑋政有限公司")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
