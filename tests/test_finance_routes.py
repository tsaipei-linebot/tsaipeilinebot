import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import finance_routes
import main
from fastapi.testclient import TestClient


class _FakeSession(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRequest:
    def __init__(self, user=None):
        self.session = _FakeSession()
        if user is not None:
            self.session["user"] = user


def _finance_account():
    return {"username": "carol", "name": "Carol", "department": "財務部", "is_platform_admin": False}


def _other_department_account():
    return {"username": "bob", "name": "Bob", "department": "新北所", "is_platform_admin": False}


class FinanceRoutingSmokeTests(unittest.TestCase):
    """未登入時導去登入頁——跟其他根層級模組同一種寫法。"""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_home_redirects_to_login_when_not_authenticated(self):
        resp = self.client.get("/finance", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/finance")

    def test_export_pdf_redirects_to_login_when_not_authenticated(self):
        resp = self.client.post("/finance/export-pdf", data={"start_date": "2026-09-01", "end_date": "2026-09-30"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/finance")

    def test_help_redirects_to_login_when_not_authenticated(self):
        """使用說明頁（2026-09-22 新增）走跟主頁同一組 _require_access，
        跟 /portal 卡片顯不顯示「使用說明」按鈕是同一組權限判斷。"""
        resp = self.client.get("/finance/help", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/login?next=/finance")


class RequireAccessDependencyTests(unittest.TestCase):
    """_require_access()：沒登入導去登入頁；登入了但部門不是財務部（也不是
    全平台管理員）導回 /portal；符合的話放行。"""

    def test_no_session_redirects_to_login(self):
        result = finance_routes._require_access(_FakeRequest())
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/login?next=/finance")

    def test_other_department_redirects_to_portal(self):
        result = finance_routes._require_access(_FakeRequest(_other_department_account()))
        self.assertIsNotNone(result)
        self.assertEqual(result.headers["location"], "/portal")

    def test_finance_department_allows_through(self):
        result = finance_routes._require_access(_FakeRequest(_finance_account()))
        self.assertIsNone(result)

    def test_platform_admin_allows_through(self):
        admin = {"username": "gary", "name": "Gary", "department": "", "is_platform_admin": True}
        result = finance_routes._require_access(_FakeRequest(admin))
        self.assertIsNone(result)


class FinanceExportPdfRouteTests(unittest.TestCase):
    """finance_export_pdf()：日期區間檢查、GAS 失敗訊息顯示、成功時把
    base64 解碼回的 ZIP 檔案內容直接當附件回給瀏覽器。"""

    def test_end_date_before_start_date_rejected_without_calling_gas(self):
        with mock.patch.object(finance_routes, "get_all_approved_repayment_records", return_value=([], None)):
            with mock.patch.object(finance_routes, "export_approved_salary_pdfs_zip") as mock_export:
                result = finance_routes.finance_export_pdf(
                    _FakeRequest(_finance_account()), start_date="2026-09-30", end_date="2026-09-01", redirect=None
                )
        mock_export.assert_not_called()
        self.assertEqual(result.status_code, 400)

    def test_gas_failure_message_rendered(self):
        with mock.patch.object(finance_routes, "get_all_approved_repayment_records", return_value=([], None)):
            with mock.patch.object(
                finance_routes, "export_approved_salary_pdfs_zip",
                return_value={"status": "error", "message": "這個日期區間內沒有已核准的補款紀錄。"},
            ):
                result = finance_routes.finance_export_pdf(
                    _FakeRequest(_finance_account()), start_date="2026-09-01", end_date="2026-09-30", redirect=None
                )
        self.assertEqual(result.status_code, 400)

    def test_success_returns_zip_attachment(self):
        import base64

        zip_bytes = b"PK\x03\x04fake zip content"
        with mock.patch.object(finance_routes, "get_all_approved_repayment_records", return_value=([], None)):
            with mock.patch.object(
                finance_routes, "export_approved_salary_pdfs_zip",
                return_value={
                    "status": "success",
                    "filename": "薪資補款存查單_2026-09-01_2026-09-30.zip",
                    "base64": base64.b64encode(zip_bytes).decode("ascii"),
                    "count": 2,
                },
            ):
                result = finance_routes.finance_export_pdf(
                    _FakeRequest(_finance_account()),
                    start_date="2026-09-01",
                    end_date="2026-09-30",
                    export_format="pdf",
                    redirect=None,
                )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, zip_bytes)
        self.assertEqual(result.media_type, "application/zip")
        self.assertIn("attachment", result.headers["content-disposition"])


class FinanceExportFormatTests(unittest.TestCase):
    """下載格式（2026-09-23 新增）：財務可以選 PDF 或圖片，內容一樣、只換
    檔案格式。圖片是為了「一次全選、右鍵列印」——PDF 在檔案總管裡沒辦法
    多選一起印。"""

    def _export(self, export_format, gas_zip=b"PK\x03\x04fake zip content"):
        import base64

        with mock.patch.object(finance_routes, "get_all_approved_repayment_records", return_value=([], None)):
            with mock.patch.object(
                finance_routes, "export_approved_salary_pdfs_zip",
                return_value={
                    "status": "success",
                    "filename": "薪資補款存查單_2026-09-01_2026-09-30.zip",
                    "base64": base64.b64encode(gas_zip).decode("ascii"),
                    "count": 2,
                },
            ):
                return finance_routes.finance_export_pdf(
                    _FakeRequest(_finance_account()),
                    start_date="2026-09-01",
                    end_date="2026-09-30",
                    export_format=export_format,
                    redirect=None,
                )

    def test_image_format_converts_and_renames_the_download(self):
        png_zip = b"PK\x03\x04converted to images"
        with mock.patch.object(finance_routes, "convert_pdf_zip_to_png_zip", return_value=png_zip) as mock_convert:
            result = self._export("image")
        mock_convert.assert_called_once()
        self.assertEqual(result.body, png_zip)
        self.assertIn("%E5%9C%96%E7%89%87", result.headers["content-disposition"])  # 檔名含「圖片」

    def test_pdf_format_does_not_touch_the_zip_at_all(self):
        with mock.patch.object(finance_routes, "convert_pdf_zip_to_png_zip") as mock_convert:
            result = self._export("pdf")
        mock_convert.assert_not_called()
        self.assertEqual(result.body, b"PK\x03\x04fake zip content")

    def test_unknown_format_falls_back_to_pdf_rather_than_failing(self):
        """新功能不該因為請求少帶/帶錯這個欄位就改變原本的行為。"""
        with mock.patch.object(finance_routes, "convert_pdf_zip_to_png_zip") as mock_convert:
            result = self._export("IMAGE")
        mock_convert.assert_not_called()
        self.assertEqual(result.body, b"PK\x03\x04fake zip content")

    def test_conversion_failure_shows_a_plain_message_instead_of_a_broken_file(self):
        with mock.patch.object(finance_routes, "convert_pdf_zip_to_png_zip", return_value=None):
            result = self._export("image")
        self.assertEqual(result.status_code, 400)


class FinanceHelpPageTests(unittest.TestCase):
    def test_renders_help_template_with_user_context(self):
        account = _finance_account()
        with mock.patch.object(finance_routes, "templates") as mock_templates:
            finance_routes.finance_help_page(_FakeRequest(account), redirect=None)
        args = mock_templates.TemplateResponse.call_args[0]
        self.assertEqual(args[1], "finance_help.html")
        self.assertEqual(args[2]["user"]["username"], "carol")


if __name__ == "__main__":
    unittest.main()
