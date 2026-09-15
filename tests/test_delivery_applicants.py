import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery import repository
from delivery.repository import (
    _normalize_applicant,
    applicant_matches_filters,
    applicant_needs_test_drive,
    bulk_update_applicants,
    delete_applicant,
    normalize_applicant_status,
)
from delivery.routes import applicant_routes


class NormalizeApplicantStatusTests(unittest.TestCase):
    def test_new_style_status_field_wins(self):
        self.assertEqual(normalize_applicant_status({"status": "interviewed"}), "interviewed")

    def test_falls_back_to_legacy_hired_flag(self):
        self.assertEqual(normalize_applicant_status({"hired": True}), "hired")

    def test_falls_back_to_legacy_withdrawn_flag(self):
        self.assertEqual(normalize_applicant_status({"withdrawn": True}), "withdrawn")

    def test_falls_back_to_legacy_interviewed_flag(self):
        self.assertEqual(normalize_applicant_status({"interviewed": True}), "interviewed")

    def test_defaults_to_not_interviewed(self):
        self.assertEqual(normalize_applicant_status({}), "not_interviewed")

    def test_hired_takes_priority_over_other_legacy_flags(self):
        self.assertEqual(normalize_applicant_status({"hired": True, "interviewed": True}), "hired")


class ApplicantMatchesFiltersTests(unittest.TestCase):
    def _applicant(self, **overrides):
        base = {"name": "王小明", "phone": "0912345678", "status": "not_interviewed"}
        base.update(overrides)
        return base

    def test_not_interviewed_shows_by_default(self):
        self.assertTrue(applicant_matches_filters(self._applicant()))

    def test_withdrawn_hidden_by_default(self):
        applicant = self._applicant(status="withdrawn")
        self.assertFalse(applicant_matches_filters(applicant))

    def test_withdrawn_shown_when_searching_by_name(self):
        applicant = self._applicant(status="withdrawn")
        self.assertTrue(applicant_matches_filters(applicant, name_keyword="王小明"))

    def test_withdrawn_shown_when_explicitly_filtering_status(self):
        applicant = self._applicant(status="withdrawn")
        self.assertTrue(applicant_matches_filters(applicant, status_filter="withdrawn"))

    def test_hired_hidden_by_default(self):
        """2026-09-15 使用者要求：「已錄取」比照「放棄」，平常盤點應徵
        名單時不用一直看到已經走完流程的紀錄。"""
        applicant = self._applicant(status="hired")
        self.assertFalse(applicant_matches_filters(applicant))

    def test_hired_shown_when_searching_by_name(self):
        applicant = self._applicant(status="hired")
        self.assertTrue(applicant_matches_filters(applicant, name_keyword="王小明"))

    def test_hired_shown_when_explicitly_filtering_status(self):
        applicant = self._applicant(status="hired")
        self.assertTrue(applicant_matches_filters(applicant, status_filter="hired"))

    def test_name_keyword_excludes_non_matching(self):
        applicant = self._applicant()
        self.assertFalse(applicant_matches_filters(applicant, name_keyword="李小華"))

    def test_phone_keyword_excludes_non_matching(self):
        applicant = self._applicant()
        self.assertFalse(applicant_matches_filters(applicant, phone_keyword="0900000000"))

    def test_status_filter_excludes_non_matching_status(self):
        applicant = self._applicant(status="interviewed")
        self.assertFalse(applicant_matches_filters(applicant, status_filter="hired"))

    def test_status_filter_matching_status_passes(self):
        applicant = self._applicant(status="interviewed")
        self.assertTrue(applicant_matches_filters(applicant, status_filter="interviewed"))

    def test_unspecified_vendor_shown_by_default(self):
        # 廠商還沒判斷出來的人正常顯示，不特別隱藏。
        applicant = self._applicant(vendor="")
        self.assertTrue(applicant_matches_filters(applicant))

    def test_vendor_filter_excludes_non_matching(self):
        applicant = self._applicant(vendor="ud")
        self.assertFalse(applicant_matches_filters(applicant, vendor_filter="shopee"))

    def test_vendor_filter_matching_passes(self):
        applicant = self._applicant(vendor="ud")
        self.assertTrue(applicant_matches_filters(applicant, vendor_filter="ud"))


class ApplicantNeedsTestDriveTests(unittest.TestCase):
    def test_ud_always_needs_test_drive(self):
        self.assertTrue(applicant_needs_test_drive("ud", ""))
        self.assertTrue(applicant_needs_test_drive("ud", "two_wheel_contract"))

    def test_uc_always_needs_test_drive(self):
        self.assertTrue(applicant_needs_test_drive("uc", ""))

    def test_sf_never_needs_test_drive(self):
        self.assertFalse(applicant_needs_test_drive("sf", ""))
        self.assertFalse(applicant_needs_test_drive("sf", "three_wheel_employed"))

    def test_shopee_needs_test_drive_only_for_three_wheel_employed(self):
        self.assertTrue(applicant_needs_test_drive("shopee", "three_wheel_employed"))
        self.assertFalse(applicant_needs_test_drive("shopee", "two_wheel_contract"))
        self.assertFalse(applicant_needs_test_drive("shopee", "two_wheel_employed"))
        self.assertFalse(applicant_needs_test_drive("shopee", ""))

    def test_unspecified_vendor_does_not_need_test_drive(self):
        self.assertFalse(applicant_needs_test_drive("", ""))


class NormalizeApplicantNoteTests(unittest.TestCase):
    """2026-09-15 新增：應徵名單的自由文字備註欄位。"""

    def test_missing_note_defaults_to_empty_string(self):
        self.assertEqual(_normalize_applicant({})["note"], "")

    def test_existing_note_is_kept(self):
        self.assertEqual(_normalize_applicant({"note": "電話一直沒接"})["note"], "電話一直沒接")


class BulkUpdateApplicantsNoteTests(unittest.TestCase):
    """`bulk_update_applicants()` 的「備註」欄位是自由文字，跟其他有固定
    選項的欄位（狀態/廠商/合作方式/試駕）不一樣，不做內容限制，只要有帶
    這個鍵就整段存入（含清空成空字串）。"""

    def _run(self, updates):
        fake_batch = mock.Mock()
        fake_db = mock.Mock()
        fake_db.batch.return_value = fake_batch
        fake_ref = mock.Mock()
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_ref
        with mock.patch.object(repository, "get_db", return_value=fake_db):
            with mock.patch.object(repository, "applicants_ref", return_value=fake_collection):
                bulk_update_applicants(updates)
        return fake_batch, fake_ref

    def test_note_is_written_as_is(self):
        fake_batch, fake_ref = self._run({"a1": {"note": "已電話聯繫，約下週面試"}})
        fake_batch.update.assert_called_once_with(fake_ref, {"note": "已電話聯繫，約下週面試"})
        fake_batch.commit.assert_called_once()

    def test_note_is_stripped_of_surrounding_whitespace(self):
        fake_batch, fake_ref = self._run({"a1": {"note": "  多餘空白  "}})
        fake_batch.update.assert_called_once_with(fake_ref, {"note": "多餘空白"})

    def test_empty_note_clears_existing_value(self):
        fake_batch, fake_ref = self._run({"a1": {"note": ""}})
        fake_batch.update.assert_called_once_with(fake_ref, {"note": ""})

    def test_missing_note_key_does_not_touch_field(self):
        fake_batch, fake_ref = self._run({"a1": {"status": "interviewed"}})
        fake_batch.update.assert_called_once_with(fake_ref, {"status": "interviewed"})

    def test_no_writes_at_all_skips_commit(self):
        fake_batch, fake_ref = self._run({"a1": {"vendor": "not-a-real-vendor"}})
        fake_batch.update.assert_not_called()
        fake_batch.commit.assert_not_called()


class _FakeFormData:
    def __init__(self, pairs):
        self._pairs = pairs

    def multi_items(self):
        return self._pairs


class _FakeRequest:
    def __init__(self, pairs):
        self._form = _FakeFormData(pairs)

    async def form(self):
        return self._form


class BulkUpdateApplicantsRouteTests(unittest.TestCase):
    """POST /applicants/bulk-update：驗證 `note_{id}` 欄位會被正確解析成
    要交給 `repository.bulk_update_applicants()` 的 updates 字典，跟既有
    的 `status_`/`vendor_`/`cooperation_type_`/`test_drive_` 欄位並存。"""

    def test_note_field_is_parsed_into_updates(self):
        with mock.patch.object(applicant_routes.repository, "bulk_update_applicants") as mock_bulk_update:
            asyncio.run(applicant_routes.bulk_update_applicants(
                _FakeRequest([("note_a1", "已電話聯繫")]), redirect=None,
            ))
        mock_bulk_update.assert_called_once_with({"a1": {"note": "已電話聯繫"}})

    def test_note_and_other_fields_for_same_applicant_are_merged(self):
        with mock.patch.object(applicant_routes.repository, "bulk_update_applicants") as mock_bulk_update:
            asyncio.run(applicant_routes.bulk_update_applicants(
                _FakeRequest([("status_a1", "interviewed"), ("note_a1", "備註內容")]), redirect=None,
            ))
        mock_bulk_update.assert_called_once_with({"a1": {"status": "interviewed", "note": "備註內容"}})


class DeleteApplicantTests(unittest.TestCase):
    """`repository.delete_applicant()`：2026-09-15 新增，純粹刪掉
    `applicants` 集合裡的那一筆文件，不動任何其他集合。"""

    def test_deletes_the_document(self):
        fake_ref = mock.Mock()
        fake_collection = mock.Mock()
        fake_collection.document.return_value = fake_ref
        with mock.patch.object(repository, "applicants_ref", return_value=fake_collection):
            delete_applicant("a1")
        fake_collection.document.assert_called_once_with("a1")
        fake_ref.delete.assert_called_once()


class DeleteApplicantRouteTests(unittest.TestCase):
    """POST /applicants/{applicant_id}/delete：只有主管（admin_required）
    能刪，路由本身不讀表單內容，直接呼叫 repository 刪除後導回列表頁。"""

    class _FakeRequest:
        pass

    def test_deletes_and_redirects_when_authorized(self):
        with mock.patch.object(applicant_routes.repository, "delete_applicant") as mock_delete:
            result = applicant_routes.delete_applicant_submit("a1", self._FakeRequest(), redirect=None)
        mock_delete.assert_called_once_with("a1")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/delivery/applicants")

    def test_returns_redirect_without_deleting_when_not_authorized(self):
        """`redirect` 有值代表 admin_required 這個依賴已經判定這個帳號沒有
        主管權限，直接短路回傳那個 redirect，完全不呼叫刪除。"""
        from fastapi.responses import RedirectResponse
        blocking_redirect = RedirectResponse(url="/delivery/", status_code=303)
        with mock.patch.object(applicant_routes.repository, "delete_applicant") as mock_delete:
            result = applicant_routes.delete_applicant_submit("a1", self._FakeRequest(), redirect=blocking_redirect)
        mock_delete.assert_not_called()
        self.assertIs(result, blocking_redirect)


if __name__ == "__main__":
    unittest.main()
