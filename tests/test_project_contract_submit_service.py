import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import services.project_contract_submit_service as submit_service


class BuildSubmitPayloadTests(unittest.TestCase):
    """build_submit_payload() 是純函式，組出來的內容要照 GAS 那支
    ProjectWorkflowService.processProjectSubmission() 看得懂的格式。"""

    def test_builds_expected_shape(self):
        payload = submit_service.build_submit_payload(
            applicant_name="胡少凱",
            vendor="日月光半導體股份有限公司",
            coop_category="派遣",
            contract_mode="實支實付",
            interview_specialist="王大明",
            visit_supervisor="李協理",
            file_base64="ZmFrZS1kYXRh",
            file_filename="合約.pdf",
            file_mime_type="application/pdf",
        )
        self.assertEqual(payload["type"], "SUBMIT_PROJECT")
        self.assertEqual(payload["applicant"], {"displayName": "胡少凱"})
        self.assertEqual(payload["fields"]["applicant_name"], "胡少凱")
        self.assertEqual(payload["fields"]["vendor"], "日月光半導體股份有限公司")
        self.assertEqual(payload["fields"]["coop_category"], "派遣")
        self.assertEqual(payload["fields"]["contract_mode"], "實支實付")
        self.assertEqual(payload["fields"]["interview_specialist"], "王大明")
        self.assertEqual(payload["fields"]["visit_supervisor"], "李協理")
        self.assertEqual(payload["file"], {
            "base64": "ZmFrZS1kYXRh",
            "filename": "合約.pdf",
            "mimeType": "application/pdf",
        })


class SubmitProjectContractTests(unittest.TestCase):
    """submit_project_contract() 呼叫 GAS 那支 Web App，涵蓋成功／GAS
    自己擋下來（例如尚未完成 LINE 綁定）／連線層級問題（error vs unknown）
    這幾種情況——跟 job_listing_submit_service.submit_job() 是同一套
    「確定沒送到」跟「不確定有沒有處理完」分開處理的原則。"""

    def test_not_configured_returns_error_without_network_call(self):
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", ""):
            with mock.patch.object(submit_service.requests, "post") as mock_post:
                result = submit_service.submit_project_contract({"type": "SUBMIT_PROJECT"})
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "error")

    def test_timeout_returns_unknown(self):
        import requests as requests_module

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(
                submit_service.requests, "post", side_effect=requests_module.Timeout("boom")
            ):
                result = submit_service.submit_project_contract({"type": "SUBMIT_PROJECT"})
        self.assertEqual(result["status"], "unknown")

    def test_success_response_passed_through(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"status": "success", "message": "專案資料已成功送出"}

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_project_contract({"type": "SUBMIT_PROJECT"})
        self.assertEqual(result["status"], "success")

    def test_unauthorized_rejection_passed_through_unchanged(self):
        """GAS 自己判斷「申請同仁尚未完成 LINE 綁定」會回傳 status
        不是 success，這邊要原封不動轉發。"""
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {
            "status": "unauthorized",
            "message": "申請同仁【胡少凱】尚未完成 LINE 身分綁定！",
        }
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_project_contract({"type": "SUBMIT_PROJECT"})
        self.assertEqual(result["status"], "unauthorized")


class StaticOptionListTests(unittest.TestCase):
    """這兩個下拉選單選項是照抄現有 Netlify 表單原始碼的固定清單。"""

    def test_coop_category_options(self):
        self.assertEqual(submit_service.COOP_CATEGORY_OPTIONS, ["派遣", "代招", "國際學生"])

    def test_contract_mode_options(self):
        self.assertEqual(submit_service.CONTRACT_MODE_OPTIONS, ["實支實付", "一口價"])


if __name__ == "__main__":
    unittest.main()
