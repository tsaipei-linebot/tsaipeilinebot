import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import dispatch_contract_routes
import main
from fastapi.testclient import TestClient


class ClientContractOptionsTests(unittest.TestCase):
    """「選擇對應的合約」下拉選單的選項組法：只列出這個帳號的部門有被勾在
    連到的廠商服務部門裡的合約（不是送出人鏈），依客戶名稱＋合約起始
    日期年份組出畫面文字。"""

    def test_appends_year_when_parsable(self):
        with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={"v1": {}}):
            with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts",
                                    return_value=[{"id": "c1", "vendor_id": "v1", "party_a_name": "測試客戶", "contract_start_date": "2027-01-01"}]):
                with mock.patch.object(dispatch_contract_routes, "viewer_can_link_contract_vendor", return_value=True):
                    options = dispatch_contract_routes._client_contract_options({"username": "bob"})
        self.assertEqual(options, [{"id": "c1", "label": "測試客戶（2027年）"}])

    def test_missing_year_shows_name_only(self):
        with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={"v1": {}}):
            with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts",
                                    return_value=[{"id": "c1", "vendor_id": "v1", "party_a_name": "測試客戶", "contract_start_date": ""}]):
                with mock.patch.object(dispatch_contract_routes, "viewer_can_link_contract_vendor", return_value=True):
                    options = dispatch_contract_routes._client_contract_options({"username": "bob"})
        self.assertEqual(options, [{"id": "c1", "label": "測試客戶"}])

    def test_excludes_contracts_the_account_cannot_link(self):
        with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={"v1": {}}):
            with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts",
                                    return_value=[{"id": "c1", "vendor_id": "v1", "party_a_name": "測試客戶", "contract_start_date": "2026-01-01"}]):
                with mock.patch.object(dispatch_contract_routes, "viewer_can_link_contract_vendor", return_value=False):
                    options = dispatch_contract_routes._client_contract_options({"username": "bob"})
        self.assertEqual(options, [])


class DispatchContractRoutingSmokeTests(unittest.TestCase):
    """/dispatch-contracts 是派遣契約產生器的獨立模組，跟
    test_project_contract_routes.py 的既有分工一致，只涵蓋不需要真的打
    Firestore／GCS 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_new_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/dispatch-contracts/new", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_download_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts/abc123/download", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")

    def test_preview_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/dispatch-contracts/abc123/preview", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/dispatch-contracts")


class RequireAccessDependencyTests(unittest.TestCase):
    """dispatch_contract_routes._require_access() 是 /dispatch-contracts 的
    權限檢查，直接單元測試回傳值（跟 test_project_contract_routes.py 同一種
    寫法）——任何有 dispatch_contracts 模組權限的帳號都能進來，不分
    「專員」/「主管」角色。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = dispatch_contract_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/dispatch-contracts")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "modules": {}, "is_platform_admin": False}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_module_access_returns_none(self):
        account = {"username": "bob", "modules": {"dispatch_contracts": "staff"}, "is_platform_admin": False}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "modules": {}, "is_platform_admin": True}
        result = dispatch_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class SubmitValidationTests(unittest.TestCase):
    """POST /dispatch-contracts/new 的表單驗證跟成功流程——mock 掉
    render_contract_docx／save_submission／dispatch_contract_storage，
    不真的套版、不真的打 Firestore/GCS，只驗證路由本身的邏輯（缺欄位擋下、
    成功時呼叫順序跟回傳的下載檔頭）。"""

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
        return {"username": "bob", "modules": {"dispatch_contracts": "staff"}, "is_platform_admin": False}

    def _multidict(self, pairs):
        # Starlette 的 FormData 支援重複 key（getlist），這裡用最簡單的
        # 假物件模擬同樣的介面，不用真的拉 starlette FormData 進來測。
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

    def test_missing_client_name_blocks_submit(self):
        form = self._multidict([
            ("work_address", "台北市"), ("work_content", "內容"),
            ("enabled_columns", "title"),
            ("shift_title", "日班"),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                self._FakeRequest(self._account(), form), redirect=None,
                            ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("客戶名稱", context["error"])

    def test_no_enabled_columns_blocks_submit(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市"), ("work_content", "內容"),
            ("shift_title", "日班"),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                self._FakeRequest(self._account(), form), redirect=None,
                            ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("欄位", context["error"])

    def test_all_rows_blank_blocks_submit(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市"), ("work_content", "內容"),
            ("enabled_columns", "title"),
            ("shift_title", ""),
        ])
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                self._FakeRequest(self._account(), form), redirect=None,
                            ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("班別", context["error"])

    def test_successful_submit_renders_saves_and_returns_docx(self):
        form = self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市內湖區"), ("work_content", "收銀"),
            ("pay_cycle", "每月1號"),
            ("enabled_columns", "title"), ("enabled_columns", "wage"),
            ("shift_title", "日班"), ("shift_wage", "196/hr"),
            ("clause_leave_policy", ""), ("clause_overtime_allowance_note", ""),
            ("clause_dress_deposit_note", ""), ("clause_benefits_note", ""), ("clause_onboarding_note", ""),
        ])
        fake_bytes = b"FAKE-DOCX-BYTES"
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=fake_bytes) as mock_render:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=False):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            with mock.patch.object(dispatch_contract_routes, "sync_vendor_from_dispatch_contract"):
                                result = asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                    self._FakeRequest(self._account(), form), redirect=None,
                                ))
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args.kwargs
        self.assertEqual(render_kwargs["shifts"], [{"title": "日班", "hours": "－", "wage": "196/hr", "bonus": "－", "overtime": "－"}])
        mock_save.assert_called_once()
        self.assertEqual(result.body, fake_bytes)
        self.assertIn("attachment", result.headers["content-disposition"])
        self.assertIn("pchome", result.headers["content-disposition"])

    def _minimal_form(self):
        return self._multidict([
            ("client_name", "pchome"), ("work_address", "台北市"), ("work_content", "收銀"),
            ("enabled_columns", "title"), ("shift_title", "日班"),
            ("clause_leave_policy", ""), ("clause_overtime_allowance_note", ""),
            ("clause_dress_deposit_note", ""), ("clause_benefits_note", ""), ("clause_onboarding_note", ""),
        ])

    def test_pdf_conversion_success_is_uploaded_and_saved(self):
        """儲存空間有設定、PDF 轉檔成功時，pdf_blob_path 要真的傳給
        save_submission——列表頁靠這個欄位決定要不要顯示「預覽」連結。"""
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=b"DOCX"):
            with mock.patch.object(dispatch_contract_routes, "convert_docx_to_pdf", return_value=b"PDF") as mock_convert:
                with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=True):
                        with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "upload_contract_docx", return_value="dispatch_contracts/x/a.docx"):
                            with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "upload_contract_pdf", return_value="dispatch_contracts/x/a.pdf") as mock_upload_pdf:
                                with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                                    with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                                        with mock.patch.object(dispatch_contract_routes, "sync_vendor_from_dispatch_contract"):
                                            asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                                self._FakeRequest(self._account(), self._minimal_form()), redirect=None,
                                            ))
        mock_convert.assert_called_once_with(b"DOCX")
        mock_upload_pdf.assert_called_once()
        self.assertEqual(mock_save.call_args.kwargs["pdf_blob_path"], "dispatch_contracts/x/a.pdf")

    def test_pdf_conversion_failure_saves_without_pdf(self):
        """PDF 轉檔失敗（convert_docx_to_pdf 回傳 None）時，不上傳、不當機，
        pdf_blob_path 存空字串——Word 檔案跟紀錄本身完全不受影響。"""
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=b"DOCX"):
            with mock.patch.object(dispatch_contract_routes, "convert_docx_to_pdf", return_value=None):
                with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=True):
                        with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "upload_contract_docx", return_value="dispatch_contracts/x/a.docx"):
                            with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "upload_contract_pdf") as mock_upload_pdf:
                                with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                                    with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                                        with mock.patch.object(dispatch_contract_routes, "sync_vendor_from_dispatch_contract"):
                                            result = asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                                self._FakeRequest(self._account(), self._minimal_form()), redirect=None,
                                            ))
        mock_upload_pdf.assert_not_called()
        self.assertEqual(mock_save.call_args.kwargs["pdf_blob_path"], "")
        self.assertEqual(result.body, b"DOCX")


class LinkedClientContractTests(unittest.TestCase):
    """派遣契約表單「選擇對應的合約」（2026-09-14 新增）：選了合約的話，
    客戶名稱／vendor_id 都直接沿用那份合約的，不會再呼叫
    sync_vendor_from_dispatch_contract() 去靠名稱猜；選的合約不存在或
    這個帳號看不到的話要擋下來，不能送出。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user, form_data):
            self.session = LinkedClientContractTests._FakeSession({"user": user})
            self._form_data = form_data

        async def form(self):
            return self._form_data

    def _account(self):
        return {"username": "bob", "modules": {"dispatch_contracts": "staff"}, "is_platform_admin": False}

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

    def _minimal_form(self, linked_id=""):
        pairs = [
            ("client_name", "手動打的名稱"), ("work_address", "台北市"), ("work_content", "收銀"),
            ("enabled_columns", "title"), ("shift_title", "日班"),
            ("clause_leave_policy", ""), ("clause_overtime_allowance_note", ""),
            ("clause_dress_deposit_note", ""), ("clause_benefits_note", ""), ("clause_onboarding_note", ""),
        ]
        if linked_id:
            pairs.append(("linked_client_contract_id", linked_id))
        return self._multidict(pairs)

    def test_linked_contract_overrides_client_name_and_vendor_id(self):
        contract = {"id": "c1", "party_a_name": "測試客戶股份有限公司", "vendor_id": "vendor-abc"}
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=b"DOCX"):
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=False):
                    with mock.patch.object(dispatch_contract_routes, "get_client_contract_submission", return_value=contract):
                        with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={"vendor-abc": {}}):
                            with mock.patch.object(dispatch_contract_routes, "viewer_can_link_contract_vendor", return_value=True):
                                with mock.patch.object(dispatch_contract_routes, "sync_vendor_from_dispatch_contract") as mock_sync:
                                    result = asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                        self._FakeRequest(self._account(), self._minimal_form(linked_id="c1")), redirect=None,
                                    ))
        mock_sync.assert_not_called()
        self.assertEqual(mock_save.call_args.kwargs["client_name"], "測試客戶股份有限公司")
        self.assertEqual(mock_save.call_args.kwargs["vendor_id"], "vendor-abc")
        self.assertEqual(mock_save.call_args.kwargs["linked_client_contract_id"], "c1")
        self.assertEqual(result.body, b"DOCX")

    def test_missing_linked_contract_blocks_submit(self):
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            with mock.patch.object(dispatch_contract_routes, "get_client_contract_submission", return_value=None):
                                asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                    self._FakeRequest(self._account(), self._minimal_form(linked_id="does-not-exist")), redirect=None,
                                ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("合約", context["error"])

    def test_invisible_linked_contract_blocks_submit(self):
        contract = {"id": "c1", "party_a_name": "別人的客戶", "vendor_id": "vendor-xyz"}
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes, "list_recent_client_names", return_value=[]):
                    with mock.patch.object(dispatch_contract_routes.platform_vendors, "list_vendors", return_value=[]):
                        with mock.patch.object(dispatch_contract_routes, "list_all_client_contracts", return_value=[]):
                            with mock.patch.object(dispatch_contract_routes, "get_client_contract_submission", return_value=contract):
                                with mock.patch.object(dispatch_contract_routes, "viewer_can_link_contract_vendor", return_value=False):
                                    asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                        self._FakeRequest(self._account(), self._minimal_form(linked_id="c1")), redirect=None,
                                    ))
        mock_save.assert_not_called()
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertIn("合約", context["error"])

    def test_no_linked_contract_falls_back_to_name_sync(self):
        with mock.patch.object(dispatch_contract_routes, "render_contract_docx", return_value=b"DOCX"):
            with mock.patch.object(dispatch_contract_routes, "save_submission") as mock_save:
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "is_configured", return_value=False):
                    with mock.patch.object(dispatch_contract_routes, "get_client_contract_submission") as mock_get:
                        with mock.patch.object(dispatch_contract_routes, "sync_vendor_from_dispatch_contract", return_value="fallback-id") as mock_sync:
                            asyncio.run(dispatch_contract_routes.dispatch_contract_submit(
                                self._FakeRequest(self._account(), self._minimal_form()), redirect=None,
                            ))
        mock_get.assert_not_called()
        mock_sync.assert_called_once_with("手動打的名稱")
        self.assertEqual(mock_save.call_args.kwargs["client_name"], "手動打的名稱")
        self.assertEqual(mock_save.call_args.kwargs["vendor_id"], "fallback-id")
        self.assertEqual(mock_save.call_args.kwargs["linked_client_contract_id"], "")


class HomeRouteTests(unittest.TestCase):
    """dispatch_contract_home() 要把目前登入的帳號傳給
    list_visible_submissions()，而不是直接呼叫 list_submissions()——這是
    可見範圍收斂（送出者/主管/平台管理員）真正生效的地方。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = HomeRouteTests._FakeSession({"user": user})

    def test_home_passes_account_to_visible_submissions(self):
        account = {"username": "bob", "is_platform_admin": False}
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "list_visible_submissions", return_value=[]) as mock_list:
                with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={}):
                    dispatch_contract_routes.dispatch_contract_home(
                        self._FakeRequest(account), generated="", redirect=None,
                    )
        mock_list.assert_called_once_with(account)

    def test_show_summary_link_reflects_department_access_check(self):
        account = {"username": "carol", "is_platform_admin": False}
        with mock.patch.object(dispatch_contract_routes, "templates") as mock_templates:
            with mock.patch.object(dispatch_contract_routes, "list_visible_submissions", return_value=[]):
                with mock.patch.object(dispatch_contract_routes, "build_vendor_lookup", return_value={"v1": {}}):
                    with mock.patch.object(dispatch_contract_routes, "viewer_has_any_department_access", return_value=True) as mock_check:
                        dispatch_contract_routes.dispatch_contract_home(
                            self._FakeRequest(account), generated="", redirect=None,
                        )
        mock_check.assert_called_once_with(account, {"v1": {}})
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertTrue(context["show_summary_link"])
        self.assertEqual(mock_templates.TemplateResponse.call_args[0][2]["records"], [])


class DownloadRouteVisibilityTests(unittest.TestCase):
    """dispatch_contract_download()：跟 preview 一樣，不是送出者/主管/平台
    管理員的話，即使知道網址也不能下載別人的紀錄。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DownloadRouteVisibilityTests._FakeSession({"user": user})

    def test_other_user_record_returns_404(self):
        record = {"id": "x", "client_name": "pchome", "blob_path": "dispatch_contracts/x/a.docx", "submitted_by": "alice"}
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                result = dispatch_contract_routes.dispatch_contract_download(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.status_code, 404)

    def test_owner_can_download(self):
        record = {"id": "x", "client_name": "pchome", "blob_path": "dispatch_contracts/x/a.docx", "submitted_by": "bob"}
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                    return_value=(b"DOCX-DATA", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
                result = dispatch_contract_routes.dispatch_contract_download(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.body, b"DOCX-DATA")

    def test_service_department_manager_can_download_even_if_not_submitter_chain(self):
        record = {"id": "x", "client_name": "pchome", "blob_path": "dispatch_contracts/x/a.docx",
                  "submitted_by": "alice", "vendor_id": "v1"}
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                with mock.patch.object(dispatch_contract_routes, "can_view_via_vendor_department_single", return_value=True):
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                            return_value=(b"DOCX-DATA", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
                        result = dispatch_contract_routes.dispatch_contract_download(
                            "x", self._FakeRequest({"username": "carol", "is_platform_admin": False}), redirect=None,
                        )
        self.assertEqual(result.body, b"DOCX-DATA")


class PreviewRouteTests(unittest.TestCase):
    """dispatch_contract_preview()：沒有 pdf_blob_path 的紀錄回 404，有的話
    用 inline Content-Disposition 回傳 PDF 內容，讓瀏覽器直接顯示。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = PreviewRouteTests._FakeSession({"user": user})

    def test_no_pdf_blob_path_returns_404(self):
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value={"id": "x", "pdf_blob_path": ""}):
            result = dispatch_contract_routes.dispatch_contract_preview(
                "x", self._FakeRequest({"username": "bob"}), redirect=None,
            )
        self.assertEqual(result.status_code, 404)

    def test_missing_record_returns_404(self):
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=None):
            result = dispatch_contract_routes.dispatch_contract_preview(
                "x", self._FakeRequest({"username": "bob"}), redirect=None,
            )
        self.assertEqual(result.status_code, 404)

    def test_existing_pdf_returns_inline_content(self):
        record = {
            "id": "x", "client_name": "pchome", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
            "submitted_by": "bob",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                    return_value=(b"%PDF-DATA", "application/pdf")):
                result = dispatch_contract_routes.dispatch_contract_preview(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.body, b"%PDF-DATA")
        self.assertIn("inline", result.headers["content-disposition"])
        self.assertIn("pchome", result.headers["content-disposition"])

    def test_service_department_manager_can_preview_even_if_not_submitter_chain(self):
        record = {
            "id": "x", "client_name": "pchome", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
            "submitted_by": "alice", "vendor_id": "v1",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                with mock.patch.object(dispatch_contract_routes, "can_view_via_vendor_department_single", return_value=True):
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                            return_value=(b"%PDF-DATA", "application/pdf")):
                        result = dispatch_contract_routes.dispatch_contract_preview(
                            "x", self._FakeRequest({"username": "carol", "is_platform_admin": False}), redirect=None,
                        )
        self.assertEqual(result.body, b"%PDF-DATA")

    def test_other_user_record_returns_404(self):
        """不是送出者、不是送出者的主管、也不是平台管理員的話，即使
        pdf_blob_path 存在，也不能用網址直接看到別人的預覽。"""
        record = {
            "id": "x", "client_name": "pchome", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
            "submitted_by": "alice",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                result = dispatch_contract_routes.dispatch_contract_preview(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        self.assertEqual(result.status_code, 404)

    def test_manager_of_submitter_can_preview(self):
        record = {
            "id": "x", "client_name": "pchome", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
            "submitted_by": "alice",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": ["carol"]}):
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                        return_value=(b"%PDF-DATA", "application/pdf")):
                    result = dispatch_contract_routes.dispatch_contract_preview(
                        "x", self._FakeRequest({"username": "carol", "is_platform_admin": False}), redirect=None,
                    )
        self.assertEqual(result.body, b"%PDF-DATA")

    def test_platform_admin_can_preview_anyone(self):
        record = {
            "id": "x", "client_name": "pchome", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
            "submitted_by": "alice",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "download_file",
                                    return_value=(b"%PDF-DATA", "application/pdf")):
                result = dispatch_contract_routes.dispatch_contract_preview(
                    "x", self._FakeRequest({"username": "boss", "is_platform_admin": True}), redirect=None,
                )
        self.assertEqual(result.body, b"%PDF-DATA")


class DeleteRouteTests(unittest.TestCase):
    """POST /dispatch-contracts/{id}/delete（2026-09-12 新增）：契約作廢用，
    能不能刪一樣走 can_view_submission() 的可見範圍判斷，刪除時要把 GCS
    上的 Word/PDF 檔案也一起清掉——跟合約產生器的刪除功能同一套做法。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = DeleteRouteTests._FakeSession({"user": user})

    def test_owner_can_delete_record_and_its_files(self):
        record = {
            "id": "x", "submitted_by": "bob",
            "blob_path": "dispatch_contracts/x/a.docx", "pdf_blob_path": "dispatch_contracts/x/a.pdf",
        }
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes, "delete_submission") as mock_delete:
                with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "delete_file") as mock_delete_file:
                    result = dispatch_contract_routes.dispatch_contract_delete(
                        "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                    )
        mock_delete.assert_called_once_with("x")
        mock_delete_file.assert_any_call("dispatch_contracts/x/a.docx")
        mock_delete_file.assert_any_call("dispatch_contracts/x/a.pdf")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/dispatch-contracts")

    def test_other_user_cannot_delete(self):
        record = {"id": "x", "submitted_by": "alice", "blob_path": "dispatch_contracts/x/a.docx"}
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": []}):
                with mock.patch.object(dispatch_contract_routes, "delete_submission") as mock_delete:
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "delete_file") as mock_delete_file:
                        result = dispatch_contract_routes.dispatch_contract_delete(
                            "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                        )
        mock_delete.assert_not_called()
        mock_delete_file.assert_not_called()
        self.assertEqual(result.status_code, 303)

    def test_missing_record_is_noop(self):
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=None):
            with mock.patch.object(dispatch_contract_routes, "delete_submission") as mock_delete:
                result = dispatch_contract_routes.dispatch_contract_delete(
                    "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                )
        mock_delete.assert_not_called()
        self.assertEqual(result.status_code, 303)

    def test_manager_of_submitter_can_delete(self):
        record = {"id": "x", "submitted_by": "alice", "blob_path": "dispatch_contracts/x/a.docx"}
        with mock.patch.object(dispatch_contract_routes, "get_submission", return_value=record):
            with mock.patch.object(dispatch_contract_routes.platform_accounts, "get_account",
                                    return_value={"username": "alice", "manager_usernames": ["bob"]}):
                with mock.patch.object(dispatch_contract_routes, "delete_submission") as mock_delete:
                    with mock.patch.object(dispatch_contract_routes.dispatch_contract_storage, "delete_file"):
                        dispatch_contract_routes.dispatch_contract_delete(
                            "x", self._FakeRequest({"username": "bob", "is_platform_admin": False}), redirect=None,
                        )
        mock_delete.assert_called_once_with("x")


if __name__ == "__main__":
    unittest.main()
