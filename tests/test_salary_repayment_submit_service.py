import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import services.salary_repayment_submit_service as submit_service


class BuildPayloadTests(unittest.TestCase):
    """build_payload() 是純函式，組出來的內容要照 GAS 那支
    SalaryWorkflowService.processSalarySubmission() 看得懂的格式（type 欄位、
    applicant.displayName、info 裡面的欄位名稱都要跟對方一致，這邊隨便改名
    對方會收到 undefined）。"""

    def test_builds_expected_shape(self):
        payload = submit_service.build_payload(
            applicant_name="王小明",
            employee_name="李小華",
            id_card="A123456789",
            vendor="蝦皮",
            apply_date="2026-09-09",
            pay_date="",
            deduct_month="",
            compensate_month="2026-09",
            is_claimable="是",
            pay_type="轉帳",
            notes="測試備註",
            earnings={"加班費": 500.0},
            deductions={"誤餐費": 100.0},
        )
        self.assertEqual(payload["type"], "SUBMIT_SALARY")
        self.assertEqual(payload["applicant"], {"displayName": "王小明"})
        self.assertEqual(payload["info"]["applicant_name"], "王小明")
        self.assertEqual(payload["info"]["name"], "李小華")
        self.assertEqual(payload["info"]["notes"], "測試備註")
        self.assertEqual(payload["earnings"], {"加班費": 500.0})
        self.assertEqual(payload["deductions"], {"誤餐費": 100.0})
        self.assertEqual(payload["summary"], {"total_earnings": 500.0, "total_deductions": 100.0})
        self.assertNotIn("image", payload)

    def test_includes_image_only_when_base64_present(self):
        payload = submit_service.build_payload(
            applicant_name="王小明",
            employee_name="李小華",
            id_card="",
            vendor="",
            apply_date="2026-09-09",
            pay_date="",
            deduct_month="",
            compensate_month="",
            is_claimable="",
            pay_type="",
            notes="備註",
            earnings={},
            deductions={},
            image_base64="ZmFrZQ==",
            image_filename="proof.jpg",
        )
        self.assertEqual(payload["image"], {"base64": "ZmFrZQ==", "filename": "proof.jpg"})

    def test_empty_earnings_and_deductions_sum_to_zero(self):
        payload = submit_service.build_payload(
            applicant_name="王小明", employee_name="李小華", id_card="", vendor="",
            apply_date="2026-09-09", pay_date="", deduct_month="", compensate_month="",
            is_claimable="", pay_type="", notes="備註", earnings={}, deductions={},
        )
        self.assertEqual(payload["summary"], {"total_earnings": 0, "total_deductions": 0})


class SubmitSalaryRepaymentTests(unittest.TestCase):
    """submit_salary_repayment() 呼叫 GAS 那支 Web App，這裡涵蓋：沒設定網址、
    連線失敗、回應不是合法 JSON、回應是 JSON 但格式不對、正常成功／GAS 自己
    擋下來（例如尚未完成 LINE 綁定）這幾種情況——每一種都要回傳結構一致的
    dict，呼叫端（me_routes.py）不用另外接例外。"""

    def test_not_configured_returns_error_without_network_call(self):
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", ""):
            with mock.patch.object(submit_service.requests, "post") as mock_post:
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertIn("JOB_PORTAL_GAS_WEBAPP_URL", result["message"])

    def test_network_error_returns_error_dict(self):
        import requests as requests_module

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(
                submit_service.requests, "post", side_effect=requests_module.ConnectionError("boom")
            ):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "error")
        self.assertIn("連線", result["message"])

    def test_non_json_response_returns_error_dict(self):
        fake_response = mock.Mock(status_code=500)
        fake_response.json.side_effect = ValueError("not json")

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "error")
        self.assertIn("500", result["message"])

    def test_json_response_missing_status_returns_error_dict(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"unexpected": "shape"}

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "error")

    def test_success_response_passed_through(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {
            "status": "success",
            "message": "薪資補款單已成功建立並送出審核",
            "salaryId": "SAL-20260909120000",
        }

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response) as mock_post:
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        mock_post.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["salaryId"], "SAL-20260909120000")

    def test_gas_rejection_passed_through_unchanged(self):
        """GAS 自己判斷「尚未完成 LINE 綁定」這類情況會回傳 status 不是
        success，這邊要原封不動轉發，讓同仁看到 GAS 給的中文說明。"""
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {
            "status": "unauthorized",
            "message": "申請同仁【王小明】尚未完成 LINE 身分綁定！",
        }

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "unauthorized")
        self.assertIn("LINE", result["message"])


if __name__ == "__main__":
    unittest.main()
