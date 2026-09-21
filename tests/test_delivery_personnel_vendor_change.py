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


class BulkUpdatePersonnelCooperationTypeVendorMatchTests(unittest.TestCase):
    """2026-09-21 修正：使用者反映「所屬廠商跟合作方式可以連動嗎」時發現
    的既有漏洞——原本這支路由只檢查送進來的 cooperation_type 這個 ID
    存不存在，沒有檢查是不是真的適用「這次要存的廠商」。畫面上改廠商後
    瀏覽器端 JS 會即時把合作方式選單換成新廠商的選項，但 JS 沒執行、或
    表單被竄改的情況下，伺服器端也要自己擋掉「廠商 A 配上廠商 B 的合作
    方式」這種不合理組合，不能照單全收。"""

    PERSON = {"id": "p1", "vendor": "shopee", "cooperation_type": "", "client": ""}

    def test_cooperation_type_matching_unchanged_vendor_is_saved(self):
        coop = {"id": "two_wheel_contract", "vendors": ["shopee"]}
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "get_cooperation_type", return_value=coop):
                    with mock.patch.object(
                        vendor_routes.repository, "update_personnel_cooperation_type"
                    ) as mock_update:
                        asyncio.run(
                            vendor_routes.bulk_update_personnel(
                                "p1", _FakeRequest({"cooperation_type": "two_wheel_contract"}), redirect=None
                            )
                        )
        mock_update.assert_called_once_with("p1", "two_wheel_contract")

    def test_cooperation_type_not_applicable_to_current_vendor_is_cleared(self):
        # 合作方式存在，但適用廠商清單裡沒有這個人目前的廠商（shopee）。
        coop = {"id": "sf_only", "vendors": ["sf"]}
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "get_cooperation_type", return_value=coop):
                    with mock.patch.object(
                        vendor_routes.repository, "update_personnel_cooperation_type"
                    ) as mock_update:
                        asyncio.run(
                            vendor_routes.bulk_update_personnel(
                                "p1", _FakeRequest({"cooperation_type": "sf_only"}), redirect=None
                            )
                        )
        mock_update.assert_called_once_with("p1", "")

    def test_cooperation_type_valid_for_newly_submitted_vendor_is_saved(self):
        # 同一次送出裡廠商也一起改了——驗證要照「這次要存的新廠商」，
        # 不能照這個人原本（送出前）的舊廠商。
        coop = {"id": "sf_only", "vendors": ["sf"]}
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_vendor"):
                    with mock.patch.object(vendor_routes.repository, "get_cooperation_type", return_value=coop):
                        with mock.patch.object(
                            vendor_routes.repository, "update_personnel_cooperation_type"
                        ) as mock_update:
                            asyncio.run(
                                vendor_routes.bulk_update_personnel(
                                    "p1",
                                    _FakeRequest({"vendor": "sf", "cooperation_type": "sf_only"}),
                                    redirect=None,
                                )
                            )
        mock_update.assert_called_once_with("p1", "sf_only")

    def test_nonexistent_cooperation_type_is_cleared(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "get_cooperation_type", return_value=None):
                    with mock.patch.object(
                        vendor_routes.repository, "update_personnel_cooperation_type"
                    ) as mock_update:
                        asyncio.run(
                            vendor_routes.bulk_update_personnel(
                                "p1", _FakeRequest({"cooperation_type": "no-such-id"}), redirect=None
                            )
                        )
        mock_update.assert_called_once_with("p1", "")


class BulkUpdatePersonnelRedirectTests(unittest.TestCase):
    """一鍵全部更新送出成功後，2026-09-13 改成跳回原本的「人員狀況」清單頁
    （不是留在詳細頁）——但驗證失敗（身分證字號格式錯誤）時還是要留在
    詳細頁顯示錯誤訊息，不能跳走讓同仁看不到哪裡沒填對。"""

    PERSON = {"id": "p1", "vendor": "shopee", "cooperation_type": "", "client": ""}

    def test_successful_update_redirects_to_vendor_list(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                result = asyncio.run(
                    vendor_routes.bulk_update_personnel("p1", _FakeRequest({}), redirect=None)
                )
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/delivery/vendor/shopee"))

    def test_redirect_uses_vendor_from_before_this_submission(self):
        # 就算這次同時把所屬廠商改到別的廠商，也是跳回「送出前」那個
        # 廠商的清單——同仁是從那個清單點進來的，改完理所當然回到那裡。
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                with mock.patch.object(vendor_routes.repository, "update_personnel_vendor"):
                    result = asyncio.run(
                        vendor_routes.bulk_update_personnel(
                            "p1", _FakeRequest({"vendor": "shopee_contract"}), redirect=None
                        )
                    )
        self.assertTrue(result.headers["location"].endswith("/delivery/vendor/shopee"))

    def test_id_number_error_stays_on_detail_page(self):
        with mock.patch.object(vendor_routes.repository, "get_personnel", return_value=dict(self.PERSON)):
            with mock.patch.object(vendor_routes.repository, "applicable_doc_types", return_value=[]):
                result = asyncio.run(
                    vendor_routes.bulk_update_personnel(
                        "p1", _FakeRequest({"id_number": "not-a-valid-id"}), redirect=None
                    )
                )
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.headers["location"].endswith("/delivery/personnel/p1?error=id_number"))


if __name__ == "__main__":
    unittest.main()
