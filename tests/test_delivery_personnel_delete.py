import os
import sys
from unittest import mock
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import vendor_routes


class DeletePersonnelRouteTests(unittest.TestCase):
    """人員刪除功能（2026-09-13 新增；2026-09-24 起按鈕在人員詳細頁）：
    只有主管能刪（走 admin_required，這裡直接測 redirect 參數，不重測
    admin_required 本身——那是 platform_accounts.require_module_admin()
    共用的邏輯，其他模組已經測過），真的整筆刪掉 Firestore 紀錄跟上傳過
    的所有檔案。"""

    def test_redirect_present_skips_deletion(self):
        fake_redirect = object()
        with mock.patch.object(vendor_routes.repository, "get_personnel") as mock_get:
            result = vendor_routes.delete_personnel_submit("p1", request=None, redirect=fake_redirect)
        mock_get.assert_not_called()
        self.assertIs(result, fake_redirect)

    def test_missing_personnel_redirects_to_search(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=None):
            with mock.patch.object(vendor_routes.repository, "delete_personnel") as mock_delete:
                result = vendor_routes.delete_personnel_submit("p1", request=None, redirect=None)
        mock_delete.assert_not_called()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/delivery/search")

    def test_deletes_files_and_record_then_redirects_to_search(self):
        """2026-09-24 起廠商人員清單頁拿掉了，刪除後回查詢人員頁。"""
        person = {"id": "p1", "name": "王小明", "vendor": "shopee"}
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=person):
            with mock.patch.object(vendor_routes.repository, "delete_personnel") as mock_delete:
                with mock.patch.object(vendor_routes, "delete_entity_files") as mock_delete_files:
                    result = vendor_routes.delete_personnel_submit("p1", request=None, redirect=None)
        mock_delete_files.assert_called_once_with("personnel-docs", "p1")
        mock_delete.assert_called_once_with("p1")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/delivery/search")


if __name__ == "__main__":
    unittest.main()
