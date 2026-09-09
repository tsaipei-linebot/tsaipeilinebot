import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

import services.job_listing_submit_service as submit_service


class BuildSubmitPayloadTests(unittest.TestCase):
    """build_submit_payload() 是純函式，組出來的內容要照 GAS 那支
    JobWorkflowService.processJobSubmission() 看得懂的格式（type/mode/
    pageId/updateAction 在最外層，其餘欄位包在 fields 裡）。"""

    def _base_kwargs(self, **overrides):
        kwargs = dict(
            applicant_name="胡少凱",
            mode="create",
            page_id="",
            update_action="create",
            vendor="欣興電子",
            title="製程技術員",
            internal_title="【山鶯】製程技術員(日班)",
            external_title="大廠高薪日班技術員",
            salary="月薪 38,000~45,000",
            interview_method="廠區面談",
            internal_desc="內部備忘",
            external_desc="對外文案",
            notes="",
            existing_image_url="",
            industry=["製造業"],
            category=["作業員"],
            job_type=["全職"],
            foreign_student=["否"],
            job_cycle=["長期"],
            city=["台北市"],
            district=["台北市大安區"],
            branch=["台北所(派遣組)"],
            shift=["日班"],
            leave_type=["週休"],
            pay_method=["月領"],
        )
        kwargs.update(overrides)
        return kwargs

    def test_builds_expected_shape(self):
        payload = submit_service.build_submit_payload(**self._base_kwargs())
        self.assertEqual(payload["type"], "SUBMIT_JOB")
        self.assertEqual(payload["applicant"], {"displayName": "胡少凱"})
        self.assertEqual(payload["mode"], "create")
        self.assertEqual(payload["pageId"], "")
        self.assertEqual(payload["updateAction"], "create")
        self.assertEqual(payload["fields"]["applicant_name"], "胡少凱")
        self.assertEqual(payload["fields"]["vendor"], "欣興電子")
        self.assertEqual(payload["fields"]["industry"], ["製造業"])
        self.assertEqual(payload["fields"]["district"], ["台北市大安區"])
        self.assertIn("image", payload)

    def test_update_mode_carries_page_id_and_update_action(self):
        payload = submit_service.build_submit_payload(
            **self._base_kwargs(mode="update", page_id="abc123", update_action="stop_recruiting")
        )
        self.assertEqual(payload["mode"], "update")
        self.assertEqual(payload["pageId"], "abc123")
        self.assertEqual(payload["updateAction"], "stop_recruiting")


class ComputeSubordinateNamesTests(unittest.TestCase):
    """compute_subordinate_names() 用帳號的 manager_usernames 反查「誰的
    主管是我」，方向跟 salary_repayment_service.build_manager_lookup_from_accounts()
    相反（那邊是查自己的主管，這裡是查自己的下屬）。"""

    def test_finds_accounts_managed_by_viewer(self):
        accounts = [
            {"username": "boss", "name": "老闆"},
            {"username": "alice", "name": "小明", "manager_usernames": ["boss"]},
            {"username": "bob", "name": "小華", "manager_usernames": ["boss", "carol"]},
            {"username": "carol", "name": "小美", "manager_usernames": []},
        ]
        result = submit_service.compute_subordinate_names("boss", accounts)
        self.assertEqual(sorted(result), ["小明", "小華"])

    def test_no_subordinates_returns_empty_list(self):
        accounts = [{"username": "alice", "name": "小明", "manager_usernames": []}]
        self.assertEqual(submit_service.compute_subordinate_names("alice", accounts), [])


class SubmitJobTests(unittest.TestCase):
    """submit_job() 呼叫 GAS 那支 Web App，涵蓋成功／GAS 自己擋下來（例如
    無異動權限）／連線層級問題（error vs unknown）這幾種情況——跟
    services/salary_repayment_submit_service.py 的 submit_salary_repayment()
    是同一套「確定沒送到」跟「不確定有沒有處理完」分開處理的原則。"""

    def test_not_configured_returns_error_without_network_call(self):
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", ""):
            with mock.patch.object(submit_service.requests, "post") as mock_post:
                result = submit_service.submit_job({"type": "SUBMIT_JOB"})
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "error")

    def test_timeout_returns_unknown(self):
        import requests as requests_module

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(
                submit_service.requests, "post", side_effect=requests_module.Timeout("boom")
            ):
                result = submit_service.submit_job({"type": "SUBMIT_JOB"})
        self.assertEqual(result["status"], "unknown")

    def test_success_response_passed_through(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"status": "success", "message": "職缺已成功送出審核", "pageId": "abc123"}

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_job({"type": "SUBMIT_JOB"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pageId"], "abc123")

    def test_forbidden_rejection_passed_through_unchanged(self):
        """GAS 自己判斷「非原刊登人亦非其主管」這類情況會回傳 status
        不是 success，這邊要原封不動轉發。"""
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {
            "status": "forbidden",
            "message": "【權限錯誤】此職缺之原刊登人為【王小明】！",
        }
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.submit_job({"type": "SUBMIT_JOB"})
        self.assertEqual(result["status"], "forbidden")


class FetchMaintainableJobsTests(unittest.TestCase):
    """fetch_maintainable_jobs() 對應現有表單的 GET_JOBS 請求，查詢失敗要
    安靜回傳空清單、不能讓整個表單頁面掛掉（這只是搜尋輔助功能）。"""

    def test_not_configured_returns_empty_list(self):
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", ""):
            result = submit_service.fetch_maintainable_jobs("胡少凱", [])
        self.assertEqual(result, [])

    def test_empty_user_name_returns_empty_list_without_network_call(self):
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post") as mock_post:
                result = submit_service.fetch_maintainable_jobs("", [])
        mock_post.assert_not_called()
        self.assertEqual(result, [])

    def test_network_error_returns_empty_list(self):
        import requests as requests_module

        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(
                submit_service.requests, "post", side_effect=requests_module.ConnectionError("boom")
            ):
                result = submit_service.fetch_maintainable_jobs("胡少凱", [])
        self.assertEqual(result, [])

    def test_success_response_returns_job_list(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"status": "success", "data": [{"id": "abc", "title": "技術員"}]}
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.fetch_maintainable_jobs("胡少凱", ["小明"])
        self.assertEqual(result, [{"id": "abc", "title": "技術員"}])

    def test_error_status_returns_empty_list(self):
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {"status": "error", "message": "boom"}
        with mock.patch.object(submit_service, "GAS_WEBAPP_URL", "https://example.com/exec"):
            with mock.patch.object(submit_service.requests, "post", return_value=fake_response):
                result = submit_service.fetch_maintainable_jobs("胡少凱", [])
        self.assertEqual(result, [])


class StaticOptionListTests(unittest.TestCase):
    """這些下拉選單選項是照抄現有 Netlify 表單原始碼的固定清單，這裡只
    檢查基本的資料完整性（不是空的、縣市對照表涵蓋全部 22 個縣市），不是
    要重新驗證每一個選項字串本身。"""

    def test_taiwan_city_districts_covers_all_22_counties(self):
        self.assertEqual(len(submit_service.TAIWAN_CITY_DISTRICTS), 22)

    def test_option_lists_are_not_empty(self):
        for options in [
            submit_service.INDUSTRY_OPTIONS, submit_service.CATEGORY_OPTIONS,
            submit_service.JOB_TYPE_OPTIONS, submit_service.FOREIGN_STUDENT_OPTIONS,
            submit_service.JOB_CYCLE_OPTIONS, submit_service.BRANCH_OPTIONS,
            submit_service.SHIFT_OPTIONS, submit_service.LEAVE_TYPE_OPTIONS,
            submit_service.PAY_METHOD_OPTIONS,
        ]:
            self.assertTrue(len(options) > 0)


if __name__ == "__main__":
    unittest.main()
