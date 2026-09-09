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
            earnings={"work_hours": 500.0},
            deductions={"labor_ins": 100.0},
        )
        self.assertEqual(payload["type"], "SUBMIT_SALARY")
        self.assertEqual(payload["applicant"], {"displayName": "王小明"})
        self.assertEqual(payload["info"]["applicant_name"], "王小明")
        self.assertEqual(payload["info"]["name"], "李小華")
        self.assertEqual(payload["info"]["notes"], "測試備註")
        self.assertEqual(payload["earnings"], {"work_hours": 500.0})
        self.assertEqual(payload["deductions"], {"labor_ins": 100.0})
        self.assertEqual(
            payload["summary"],
            {"total_earnings": 500.0, "total_deductions": 100.0, "net_total": 400.0},
        )
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
        self.assertEqual(payload["summary"], {"total_earnings": 0, "total_deductions": 0, "net_total": 0})


class ValidateTaiwanIdTests(unittest.TestCase):
    """validate_taiwan_id() 照抄現有 Netlify 表單 index_6.html 的
    validateTaiwanId()（格式 + 檢查碼演算法），"A123456789" 是那份原始碼
    表單欄位的 placeholder 文字，剛好也是一組檢查碼合法的範例值。"""

    def test_known_valid_id_passes(self):
        self.assertTrue(submit_service.validate_taiwan_id("A123456789"))

    def test_lowercase_input_is_normalized(self):
        self.assertTrue(submit_service.validate_taiwan_id("a123456789"))

    def test_wrong_checksum_digit_fails(self):
        self.assertFalse(submit_service.validate_taiwan_id("A123456780"))

    def test_wrong_format_fails(self):
        self.assertFalse(submit_service.validate_taiwan_id("1234567890"))

    def test_too_short_fails(self):
        self.assertFalse(submit_service.validate_taiwan_id("A12345678"))

    def test_empty_fails(self):
        self.assertFalse(submit_service.validate_taiwan_id(""))
        self.assertFalse(submit_service.validate_taiwan_id(None))


class SubmitSalaryRepaymentTests(unittest.TestCase):
    """submit_salary_repayment() 呼叫 GAS 那支 Web App，這裡涵蓋：沒設定網址、
    連線失敗、逾時、回應不是合法 JSON、回應是 JSON 但格式不對、正常成功／
    GAS 自己擋下來（例如尚未完成 LINE 綁定）這幾種情況——每一種都要回傳
    結構一致的 dict，呼叫端（me_routes.py）不用另外接例外。

    2026-09-09 使用者實測回報：GAS 那支網頁應用程式偶爾會出現「其實已經
    處理完這筆申請，但回傳執行結果給呼叫端這一步卡住」的情況（HTTP 404、
    回應不是合法 JSON），這種「不確定到底有沒有處理完」的狀況要回傳
    status="unknown"，不能當成 status="error" 處理——呼叫端看到 "error"
    才可以放心讓同仁重新送出，"unknown" 時絕對不能，否則會有重複申請
    的風險（見 me_routes.py 的說明）。只有「送出請求前就確定失敗」（例如
    網址沒設定、DNS 解析失敗、連線被拒絕）這種 GAS 一定沒收到請求的情況
    才回傳 "error"。"""

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

    def test_timeout_returns_unknown_not_error(self):
        import requests as requests_module

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(
                submit_service.requests, "post", side_effect=requests_module.Timeout("boom")
            ):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "unknown")
        self.assertIn("我的專區", result["message"])

    def test_non_json_response_returns_unknown_not_error(self):
        fake_response = mock.Mock(status_code=404)
        fake_response.json.side_effect = ValueError("not json")

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "unknown")
        self.assertIn("我的專區", result["message"])

    def test_json_response_missing_status_returns_unknown_not_error(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"unexpected": "shape"}

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_salary_repayment({"type": "SUBMIT_SALARY"})
        self.assertEqual(result["status"], "unknown")

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
