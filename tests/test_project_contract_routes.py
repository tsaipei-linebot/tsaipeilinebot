import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import project_contract_routes
import main
from fastapi.testclient import TestClient


class ProjectContractRoutingSmokeTests(unittest.TestCase):
    """/project-contracts 是專案合約維護的獨立模組，跟 test_job_listing_routes.py
    的既有分工一致，只涵蓋不需要真的打 Firestore／GAS 的部分：未登入時的導向。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_form_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/project-contracts", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/project-contracts")

    def test_submit_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/project-contracts", data={}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/project-contracts")


class RequireAccessDependencyTests(unittest.TestCase):
    """project_contract_routes._require_access() 是 /project-contracts 的
    權限檢查，直接單元測試回傳值（跟 test_job_listing_routes.py 同一種
    寫法）——任何有 project_contracts 模組權限的帳號都能進來，不分
    「專員」/「主管」角色，兩者體驗完全一樣。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user=None):
            self.session = RequireAccessDependencyTests._FakeSession()
            if user is not None:
                self.session["user"] = user

    def test_no_session_redirects_to_login(self):
        result = project_contract_routes._require_access(self._FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/login?next=/project-contracts")

    def test_logged_in_without_module_access_redirects_to_portal(self):
        account = {"username": "alice", "name": "Alice", "modules": {}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/portal")

    def test_logged_in_with_staff_module_access_returns_none(self):
        account = {"username": "bob", "name": "Bob", "modules": {"project_contracts": "staff"}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_logged_in_with_admin_module_access_returns_none(self):
        account = {"username": "carol", "name": "Carol", "modules": {"project_contracts": "admin"}, "is_platform_admin": False}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)

    def test_platform_admin_always_has_access(self):
        account = {"username": "boss", "name": "Boss", "modules": {}, "is_platform_admin": True}
        result = project_contract_routes._require_access(self._FakeRequest(account))
        self.assertIsNone(result)


class ClientContractOptionsTests(unittest.TestCase):
    """_client_contract_options()：2026-09-12 新增「從合約產生器帶入」選單，
    只列出這個帳號看得到、而且真的有存到 Word 檔的紀錄，並且依
    contract_version 對應出這裡的「簽約模式」給前端 JS 自動帶入用。"""

    def test_filters_out_records_without_blob_path(self):
        account = {"username": "bob", "is_platform_admin": False}
        records = [
            {"id": "1", "party_a_name": "A公司", "blob_path": "client_contracts/1/a.docx",
             "contract_version": "hourly_flat_rate", "created_at": "2026-01-01"},
            {"id": "2", "party_a_name": "B公司", "blob_path": "", "contract_version": "hourly_flat_rate"},
        ]
        with mock.patch.object(project_contract_routes, "list_visible_client_contracts", return_value=records):
            options = project_contract_routes._client_contract_options(account)
        self.assertEqual([o["id"] for o in options], ["1"])

    def test_maps_contract_version_to_project_contract_mode(self):
        account = {"username": "bob", "is_platform_admin": False}
        records = [{"id": "1", "party_a_name": "A公司", "blob_path": "client_contracts/1/a.docx",
                    "contract_version": "hourly_flat_rate", "created_at": "2026-01-01"}]
        with mock.patch.object(project_contract_routes, "list_visible_client_contracts", return_value=records):
            options = project_contract_routes._client_contract_options(account)
        self.assertEqual(options[0]["project_contract_mode"], "一口價")

    def test_white_collar_referral_maps_to_referral_coop_category(self):
        # 2026-09-12 新增「白領代招」版本：合作類別要對應「代招」，不是
        # 前兩版共用的「派遣」，不然帶入專案合約維護表單時會選錯選項。
        account = {"username": "bob", "is_platform_admin": False}
        records = [{"id": "1", "party_a_name": "A公司", "blob_path": "client_contracts/1/a.docx",
                    "contract_version": "white_collar_referral", "created_at": "2026-01-01"}]
        with mock.patch.object(project_contract_routes, "list_visible_client_contracts", return_value=records):
            options = project_contract_routes._client_contract_options(account)
        self.assertEqual(options[0]["project_contract_coop_category"], "代招")

    def test_hourly_flat_rate_maps_to_dispatch_coop_category(self):
        account = {"username": "bob", "is_platform_admin": False}
        records = [{"id": "1", "party_a_name": "A公司", "blob_path": "client_contracts/1/a.docx",
                    "contract_version": "hourly_flat_rate", "created_at": "2026-01-01"}]
        with mock.patch.object(project_contract_routes, "list_visible_client_contracts", return_value=records):
            options = project_contract_routes._client_contract_options(account)
        self.assertEqual(options[0]["project_contract_coop_category"], "派遣")

    def test_taiwanese_referral_maps_to_referral_coop_category(self):
        # 2026-09-12 新增「台籍代招」版本：跟白領代招一樣對應「代招」。
        account = {"username": "bob", "is_platform_admin": False}
        records = [{"id": "1", "party_a_name": "A公司", "blob_path": "client_contracts/1/a.docx",
                    "contract_version": "taiwanese_referral", "created_at": "2026-01-01"}]
        with mock.patch.object(project_contract_routes, "list_visible_client_contracts", return_value=records):
            options = project_contract_routes._client_contract_options(account)
        self.assertEqual(options[0]["project_contract_coop_category"], "代招")


class MarkClientContractSentTests(unittest.TestCase):
    """_mark_client_contract_sent_if_applicable()：送出成功後如果有帶
    from_client_contract_id 才標記，而且要先確認這個帳號真的看得到那筆
    合約產生器紀錄，避免竄改表單欄位去標記別人的紀錄。"""

    def test_blank_id_is_noop(self):
        with mock.patch.object(project_contract_routes, "mark_sent_to_project_contracts") as mock_mark:
            project_contract_routes._mark_client_contract_sent_if_applicable({"username": "bob"}, "")
        mock_mark.assert_not_called()

    def test_record_not_found_is_noop(self):
        with mock.patch.object(project_contract_routes, "get_client_contract", return_value=None):
            with mock.patch.object(project_contract_routes, "mark_sent_to_project_contracts") as mock_mark:
                project_contract_routes._mark_client_contract_sent_if_applicable({"username": "bob"}, "x")
        mock_mark.assert_not_called()

    def test_no_view_permission_is_noop(self):
        record = {"id": "x", "submitted_by": "alice"}
        with mock.patch.object(project_contract_routes, "get_client_contract", return_value=record):
            with mock.patch.object(project_contract_routes, "can_view_client_contract", return_value=False):
                with mock.patch.object(project_contract_routes, "mark_sent_to_project_contracts") as mock_mark:
                    project_contract_routes._mark_client_contract_sent_if_applicable({"username": "bob"}, "x")
        mock_mark.assert_not_called()

    def test_visible_record_gets_marked(self):
        record = {"id": "x", "submitted_by": "bob"}
        with mock.patch.object(project_contract_routes, "get_client_contract", return_value=record):
            with mock.patch.object(project_contract_routes, "can_view_client_contract", return_value=True):
                with mock.patch.object(project_contract_routes, "mark_sent_to_project_contracts") as mock_mark:
                    project_contract_routes._mark_client_contract_sent_if_applicable({"username": "bob"}, "x")
        mock_mark.assert_called_once_with("x")


class SubmitMarksClientContractOnSuccessTests(unittest.TestCase):
    """POST /project-contracts 送出成功、且表單帶了 from_client_contract_id
    時，要呼叫 _mark_client_contract_sent_if_applicable()。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user, form_data):
            self.session = SubmitMarksClientContractOnSuccessTests._FakeSession({"user": user})
            self._form_data = form_data

        async def form(self):
            return self._form_data

    class _FakeUploadFile:
        def __init__(self, filename="合約.docx", content=b"fake docx", content_type="application/msword"):
            self.filename = filename
            self.content_type = content_type
            self._content = content

        async def read(self):
            return self._content

    def _multidict(self, pairs):
        class _Fake:
            def __init__(self, pairs):
                self._pairs = pairs

            def get(self, key, default=None):
                for k, v in self._pairs:
                    if k == key:
                        return v
                return default

        return _Fake(pairs)

    def test_from_client_contract_id_triggers_mark(self):
        account = {"username": "bob", "name": "Bob", "modules": {"project_contracts": "staff"}, "is_platform_admin": False}
        form = self._multidict([
            ("vendor", "測試客戶"), ("coop_category", "派遣"), ("contract_mode", "一口價"),
            ("interview_specialist", "王大明"), ("visit_supervisor", "李協理"),
            ("from_client_contract_id", "cc-123"),
        ])
        with mock.patch.object(project_contract_routes, "submit_project_contract",
                                return_value={"status": "success"}):
            with mock.patch.object(project_contract_routes, "_mark_client_contract_sent_if_applicable") as mock_mark:
                asyncio.run(project_contract_routes.project_contract_submit(
                    self._FakeRequest(account, form), self._FakeUploadFile(), redirect=None,
                ))
        mock_mark.assert_called_once_with(account, "cc-123")


if __name__ == "__main__":
    unittest.main()
