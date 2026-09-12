import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import client_contract_routes
import main
from fastapi.testclient import TestClient


class FilenameHelperTests(unittest.TestCase):
    """檔名（含存進 GCS 的名稱）要帶合約起始日期的年份，不是送出當下的
    年份——2026-09-12 使用者要求，方便年底先產生下一年度續約合約時，
    檔名本身就標示清楚是哪一年的合約。"""

    def test_build_filename_includes_contract_year(self):
        self.assertEqual(
            client_contract_routes._build_filename("測試客戶股份有限公司", 2027, "docx"),
            "合約_測試客戶股份有限公司_2027.docx",
        )

    def test_contract_year_extracts_from_iso_date_string(self):
        self.assertEqual(client_contract_routes._contract_year({"contract_start_date": "2027-03-15"}), "2027")

    def test_contract_year_returns_empty_string_when_missing(self):
        self.assertEqual(client_contract_routes._contract_year({}), "")
        self.assertEqual(client_contract_routes._contract_year({"contract_start_date": ""}), "")


class ClientContractRoutingSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/client-contracts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/client-contracts")

    def test_new_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/client-contracts/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/client-contracts")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/client-contracts/new", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/client-contracts")

    def test_download_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/client-contracts/abc123/download", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/client-contracts")

    def test_preview_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/client-contracts/abc123/preview", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/client-contracts")

    def test_company_lookup_returns_not_found_json_when_not_authenticated(self):
        resp = self.client.get("/client-contracts/company-lookup", params={"q": "瑋政"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"found": False})


class RequireAccessDependencyTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = client_contract_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/client-contracts")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "modules": {}, "is_platform_admin": False}
        result = client_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_module_access_returns_none(self):
        account = {"username": "bob", "modules": {"client_contracts": "staff"}, "is_platform_admin": False}
        result = client_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "modules": {}, "is_platform_admin": True}
        result = client_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class CompanyLookupRouteTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = CompanyLookupRouteTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_account_returns_not_found(self):
        result = client_contract_routes.client_contract_company_lookup("瑋政", self._FakeRequest())
        self.assertEqual(result, {"found": False})

    def test_no_module_access_returns_not_found(self):
        account = {"username": "alice", "modules": {}, "is_platform_admin": False}
        result = client_contract_routes.client_contract_company_lookup("瑋政", self._FakeRequest(account))
        self.assertEqual(result, {"found": False})

    def test_found_result_is_passed_through(self):
        account = {"username": "bob", "modules": {"client_contracts": "staff"}, "is_platform_admin": False}
        with mock.patch.object(client_contract_routes, "lookup_company",
                                return_value={"name": "瑋政有限公司", "representative": "蔡志祥",
                                              "address": "新北市板橋區", "tax_id": "68138452"}):
            result = client_contract_routes.client_contract_company_lookup("瑋政", self._FakeRequest(account))
        self.assertEqual(result["found"], True)
        self.assertEqual(result["name"], "瑋政有限公司")

    def test_not_found_result(self):
        account = {"username": "bob", "modules": {"client_contracts": "staff"}, "is_platform_admin": False}
        with mock.patch.object(client_contract_routes, "lookup_company", return_value=None):
            result = client_contract_routes.client_contract_company_lookup("查無此公司", self._FakeRequest(account))
        self.assertEqual(result, {"found": False})


class SubmitValidationTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user, form_data):
            self.session = SubmitValidationTests._FakeSession({"user": user})
            self._form_data = form_data

        async def form(self):
            return self._form_data

    def _account(self):
        return {"username": "bob", "name": "王小明", "modules": {"client_contracts": "staff"}, "is_platform_admin": False}

    def _multidict(self, pairs):
        class _Fake:
            def __init__(self, pairs):
                self._pairs = pairs

            def get(self, key, default=None):
                for k, v in self._pairs:
                    if k == key:
                        return v
                return default

            def getlist(self, key):
                return [v for k, v in self._pairs if k == key]

        return _Fake(pairs)

    def _full_valid_pairs(self, **overrides):
        pairs = {
            "party_a_name": "測試客戶股份有限公司",
            "party_a_representative": "陳大明",
            "party_a_address": "台北市信義區忠孝東路一段1號",
            "party_a_tax_id": "12345678",
            "party_a_phone": "02-1234-5678",
            "party_b_company_id": "weizheng",
            "sign_date": "2026-01-01",
            "contract_start_date": "2026-01-01",
            "contract_end_date": "2026-12-31",
            "replace_notice_days": "3",
            "severance_payer": "乙方",
            "remit_day": "10",
            "hourly_wage": "200",
            "management_fee": "65",
            "contract_version": "hourly_flat_rate",
        }
        pairs.update(overrides)
        return list(pairs.items())

    def _fake_company(self):
        return {
            "id": "weizheng", "short_name": "weizheng", "name": "瑋政有限公司",
            "responsible_person": "蔡志祥", "address": "新北市板橋區文化路二段90號五樓",
            "phone": "(02) 6637-3899", "tax_id": "68138452",
        }

    def test_missing_party_a_name_blocks_submit(self):
        form = self._multidict(self._full_valid_pairs(party_a_name=""))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("甲方", context["error"])

    def test_missing_party_b_company_blocks_submit(self):
        form = self._multidict(self._full_valid_pairs(party_b_company_id=""))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                    asyncio.run(client_contract_routes.client_contract_submit(
                        self._FakeRequest(self._account(), form), redirect=None,
                    ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("乙方", context["error"])

    def test_invalid_severance_payer_blocks_submit(self):
        form = self._multidict(self._full_valid_pairs(severance_payer="丙方"))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("資遣費用", context["error"])

    def test_missing_hourly_wage_blocks_submit_for_hourly_flat_rate(self):
        form = self._multidict(self._full_valid_pairs(hourly_wage=""))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("報價", context["error"])

    def test_missing_service_fee_blocks_submit_for_actual_paid(self):
        form = self._multidict(self._full_valid_pairs(
            contract_version="actual_paid", hourly_wage="", management_fee="", service_fee="",
        ))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("報價", context["error"])

    def test_actual_paid_with_service_fee_does_not_require_hourly_fields(self):
        form = self._multidict(self._full_valid_pairs(
            contract_version="actual_paid", hourly_wage="", management_fee="", service_fee="人員薪資的15%",
        ))
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(client_contract_routes, "render_contract_docx", return_value=fake_bytes):
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.client_contract_storage, "is_configured", return_value=False):
                        result = asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_called_once()
        self.assertEqual(result.body, fake_bytes)

    def test_successful_submit_renders_saves_and_returns_docx(self):
        form = self._multidict(self._full_valid_pairs())
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(client_contract_routes, "render_contract_docx", return_value=fake_bytes) as mock_render:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.client_contract_storage, "is_configured", return_value=False):
                        result = asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args.kwargs
        self.assertEqual(render_kwargs["party_a"]["name"], "測試客戶股份有限公司")
        self.assertEqual(render_kwargs["party_b"]["name"], "瑋政有限公司")
        mock_save.assert_called_once()
        self.assertEqual(result.body, fake_bytes)
        self.assertIn("attachment", result.headers["content-disposition"])
        # 2026-09-12 使用者要求檔名要帶「合約年」（合約起始日期的年份，
        # 不是送出當下的年份）。
        self.assertIn("2026", result.headers["content-disposition"])

    def test_white_collar_referral_does_not_require_sign_date_or_severance_fields(self):
        # 白領代招版本沒有簽約日期／撤換條款這幾個欄位，即使表單完全沒帶
        # 這些值，只要報價欄位（fee_amount/service_months）跟其他共用
        # 必填欄位都有填，就應該能送出成功。
        form = self._multidict(self._full_valid_pairs(
            contract_version="white_collar_referral",
            sign_date="", replace_notice_days="", severance_payer="",
            hourly_wage="", management_fee="",
            fee_amount="二千五百元整", service_months="12",
        ))
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(client_contract_routes, "render_contract_docx", return_value=fake_bytes) as mock_render:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.client_contract_storage, "is_configured", return_value=False):
                        result = asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args.kwargs
        self.assertIsNone(render_kwargs["sign_date"])
        self.assertEqual(render_kwargs["fee_amount"], "二千五百元整")
        self.assertEqual(render_kwargs["service_months"], "12")
        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs["sign_date"], "")
        self.assertEqual(result.body, fake_bytes)

    def test_missing_pricing_fields_blocks_submit_for_white_collar_referral(self):
        form = self._multidict(self._full_valid_pairs(
            contract_version="white_collar_referral",
            sign_date="", replace_notice_days="", severance_payer="",
            hourly_wage="", management_fee="",
            fee_amount="", service_months="",
        ))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("報價", context["error"])

    def test_taiwanese_referral_does_not_require_sign_date_or_severance_fields(self):
        form = self._multidict(self._full_valid_pairs(
            contract_version="taiwanese_referral",
            sign_date="", replace_notice_days="", severance_payer="",
            hourly_wage="", management_fee="",
            referral_fee_percentage="人員應領薪資的15%", referral_service_months="6",
        ))
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(client_contract_routes, "render_contract_docx", return_value=fake_bytes) as mock_render:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.client_contract_storage, "is_configured", return_value=False):
                        result = asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args.kwargs
        self.assertIsNone(render_kwargs["sign_date"])
        self.assertEqual(render_kwargs["referral_fee_percentage"], "人員應領薪資的15%")
        self.assertEqual(render_kwargs["referral_service_months"], "6")
        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        self.assertEqual(save_kwargs["sign_date"], "")
        self.assertEqual(result.body, fake_bytes)

    def test_missing_pricing_fields_blocks_submit_for_taiwanese_referral(self):
        form = self._multidict(self._full_valid_pairs(
            contract_version="taiwanese_referral",
            sign_date="", replace_notice_days="", severance_payer="",
            hourly_wage="", management_fee="",
            referral_fee_percentage="", referral_service_months="",
        ))
        with mock.patch.object(client_contract_routes, "templates") as mock_templates:
            with mock.patch.object(client_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(client_contract_routes.platform_companies, "get_company",
                                        return_value=self._fake_company()):
                    with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                        asyncio.run(client_contract_routes.client_contract_submit(
                            self._FakeRequest(self._account(), form), redirect=None,
                        ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("報價", context["error"])


class DuplicateFromTests(unittest.TestCase):
    """GET /client-contracts/new?duplicate_from=xxx：把既有紀錄的欄位帶入
    新增表單，給年底續下一年度合約用。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DuplicateFromTests._FakeSession({"user": user})

    def _account(self):
        return {"username": "bob", "modules": {"client_contracts": "staff"}, "is_platform_admin": False}

    def test_visible_record_prefills_form(self):
        record = {
            "id": "x", "submitted_by": "bob", "party_a_name": "測試客戶股份有限公司",
            "party_b_company_id": "weizheng", "contract_start_date": "2026-01-01",
            "hourly_wage": "200", "management_fee": "65", "contract_version": "hourly_flat_rate",
        }
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                with mock.patch.object(client_contract_routes, "templates") as mock_templates:
                    client_contract_routes.client_contract_new_form(
                        self._FakeRequest(self._account()), duplicate_from="x", redirect=None,
                    )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["form"]["party_a_name"], "測試客戶股份有限公司")
        self.assertEqual(context["form"]["hourly_wage"], "200")

    def test_invisible_record_is_ignored(self):
        record = {"id": "x", "submitted_by": "alice", "party_a_name": "別人的客戶"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
                    with mock.patch.object(client_contract_routes, "templates") as mock_templates:
                        client_contract_routes.client_contract_new_form(
                            self._FakeRequest(self._account()), duplicate_from="x", redirect=None,
                        )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["form"], {})

    def test_no_duplicate_from_leaves_form_empty(self):
        with mock.patch.object(client_contract_routes.platform_companies, "list_companies", return_value=[]):
            with mock.patch.object(client_contract_routes, "templates") as mock_templates:
                client_contract_routes.client_contract_new_form(
                    self._FakeRequest(self._account()), duplicate_from="", redirect=None,
                )
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(context["form"], {})


class DeleteRouteTests(unittest.TestCase):
    """POST /client-contracts/{id}/delete：合約作廢用，能不能刪一樣走
    can_view_submission() 的可見範圍判斷，刪除時要把 GCS 上的 Word/PDF
    檔案也一起清掉。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DeleteRouteTests._FakeSession({"user": user})

    def test_owner_can_delete_record_and_its_files(self):
        record = {
            "id": "x", "submitted_by": "bob",
            "blob_path": "client_contracts/x/a.docx", "pdf_blob_path": "client_contracts/x/a.pdf",
        }
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes, "delete_submission") as mock_delete:
                with mock.patch.object(client_contract_routes.client_contract_storage, "delete_file") as mock_delete_file:
                    result = client_contract_routes.client_contract_delete(
                        "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                    )
        mock_delete.assert_called_once_with("x")
        mock_delete_file.assert_any_call("client_contracts/x/a.docx")
        mock_delete_file.assert_any_call("client_contracts/x/a.pdf")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/client-contracts")

    def test_other_user_cannot_delete(self):
        record = {"id": "x", "submitted_by": "alice", "blob_path": "client_contracts/x/a.docx"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                with mock.patch.object(client_contract_routes, "delete_submission") as mock_delete:
                    with mock.patch.object(client_contract_routes.client_contract_storage, "delete_file") as mock_delete_file:
                        result = client_contract_routes.client_contract_delete(
                            "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                        )
        mock_delete.assert_not_called()
        mock_delete_file.assert_not_called()
        self.assertEqual(result.status_code, 303)

    def test_missing_record_is_noop(self):
        with mock.patch.object(client_contract_routes, "get_submission", return_value=None):
            with mock.patch.object(client_contract_routes, "delete_submission") as mock_delete:
                result = client_contract_routes.client_contract_delete(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        mock_delete.assert_not_called()
        self.assertEqual(result.status_code, 303)


class DownloadRouteVisibilityTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DownloadRouteVisibilityTests._FakeSession({"user": user})

    def test_other_user_record_returns_404(self):
        record = {"id": "x", "party_a_name": "測試客戶", "blob_path": "client_contracts/x/a.docx", "submitted_by": "alice"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                result = client_contract_routes.client_contract_download(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.status_code, 404)

    def test_owner_can_download(self):
        record = {"id": "x", "party_a_name": "測試客戶", "blob_path": "client_contracts/x/a.docx",
                  "submitted_by": "bob", "contract_start_date": "2026-01-01"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.client_contract_storage, "download_file",
                                    return_value=(b"DOCX-DATA", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
                result = client_contract_routes.client_contract_download(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.body, b"DOCX-DATA")
        self.assertIn("2026", result.headers["content-disposition"])


class PreviewRouteTests(unittest.TestCase):
    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = PreviewRouteTests._FakeSession({"user": user})

    def test_no_pdf_blob_path_returns_404(self):
        with mock.patch.object(client_contract_routes, "get_submission", return_value={"id": "x", "pdf_blob_path": ""}):
            result = client_contract_routes.client_contract_preview(
                "x", self._FakeRequest({"username": "bob"}), redirect=None,
            )
        self.assertEqual(result.status_code, 404)

    def test_other_user_record_returns_404(self):
        record = {"id": "x", "party_a_name": "測試客戶", "pdf_blob_path": "client_contracts/x/a.pdf", "submitted_by": "alice"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                result = client_contract_routes.client_contract_preview(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.status_code, 404)

    def test_owner_can_preview(self):
        record = {"id": "x", "party_a_name": "測試客戶", "pdf_blob_path": "client_contracts/x/a.pdf",
                  "submitted_by": "bob", "contract_start_date": "2026-01-01"}
        with mock.patch.object(client_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(client_contract_routes.client_contract_storage, "download_file",
                                    return_value=(b"%PDF-DATA", "application/pdf")):
                result = client_contract_routes.client_contract_preview(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.body, b"%PDF-DATA")
        self.assertIn("inline", result.headers["content-disposition"])
        self.assertIn("2026", result.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
