import asyncio
import os
import sys
from unittest import mock
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.routes import vendor_routes


class _FakeRequest:
    """假的 Request，只提供 bulk_update_personnel() 用得到的 async form()
    方法——回傳一個普通 dict 就夠了，這支路由只用 form.get()／"key" in form，
    沒有用到 getlist()。"""

    def __init__(self, form_dict):
        self._form_dict = form_dict

    async def form(self):
        return self._form_dict


class BulkUpdatePersonnelVendorFieldTests(unittest.TestCase):
    """人員詳細頁新增的「修改所屬廠商」欄位（2026-09-13，蝦皮廠商拆分後
    順便補上的功能——人員建立後原本沒有地方可以再改廠商）：合法的廠商
    代碼才會真的呼叫 update_personnel_vendor()，不合法的值安靜忽略，
    跟這支路由裡 cooperation_type／client 的既有驗證邏輯一致。"""

    PERSON = {"id": "p1", "vendor": "shopee", "cooperation_type": "", "client": ""}

    def test_valid_vendor_calls_update_personnel_vendor(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_vendor") as mock_update:
                    asyncio.run(
                        vendor_routes.bulk_update_personnel(
                            "p1", _FakeRequest({"vendor": "shopee_contract"}), redirect=None
                        )
                    )
        mock_update.assert_called_once_with("p1", "shopee_contract")

    def test_invalid_vendor_is_ignored(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_vendor") as mock_update:
                    asyncio.run(
                        vendor_routes.bulk_update_personnel(
                            "p1", _FakeRequest({"vendor": "not-a-real-vendor"}), redirect=None
                        )
                    )
        mock_update.assert_not_called()

    def test_missing_vendor_field_skips_update(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_vendor") as mock_update:
                    asyncio.run(
                        vendor_routes.bulk_update_personnel("p1", _FakeRequest({}), redirect=None)
                    )
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
