"""薪資補款搬離 GAS 階段 2 第 2 步：佐證照片 Drive → Cloud Storage（2026-09-25）。測試資料全部是假的。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import requests
from fastapi.testclient import TestClient

import finance_routes
import main
import platform_accounts
from services import salary_repayment_photos as photos
from services import salary_repayment_store as store
from tests._fake_firestore import FakeFirestore

ADMIN = {"username": "boss", "name": "老闆", "department": "", "is_platform_admin": True, "modules": []}
FINANCE = {"username": "carol", "name": "Carol", "department": "財務部", "is_platform_admin": False, "modules": []}
HEADER = ["補款單號", "申請人姓名", "補款佐證(照片)"]
FILE_A = "1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FILE_B = "1BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def url(file_id):
    return f"https://lh3.googleusercontent.com/d/{file_id}"


class _Resp:
    def __init__(self, status=200, content=JPEG, content_type="image/jpeg"):
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": content_type}


class PhotoTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeFirestore()
        self.blobs = {}
        for p in (
            mock.patch.object(store, "get_db", return_value=self.db),
            mock.patch.object(photos, "upload_photo", side_effect=self._upload),
            mock.patch.object(photos, "download_photo", side_effect=lambda path: self.blobs.get(path, (None, None))),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _upload(self, doc_id, content, content_type):
        path = f"salary/photos/{doc_id}/{len(self.blobs)}.jpg"
        self.blobs[path] = (content, content_type)
        return path

    def sync(self, *rows):
        store.sync_from_sheet([["員工姓名"]], [HEADER] + [list(r) for r in rows], ADMIN)

    def doc(self, doc_id):
        return self.db.docs(store.RECORDS_COLLECTION)[doc_id]


class PhotoColumnTests(unittest.TestCase):
    def test_real_gas_header(self):
        self.assertEqual(photos.photo_url({"補款佐證(照片)": f" {url(FILE_A)} "}), url(FILE_A))

    def test_other_header_with_keyword(self):
        self.assertEqual(photos.photo_url({"補款佐證圖檔": url(FILE_A)}), url(FILE_A))

    def test_header_renamed_but_value_is_a_drive_link(self):
        self.assertEqual(photos.photo_url({"備註": "無", "U": url(FILE_A)}), url(FILE_A))

    def test_empty_known_column_means_no_photo(self):
        self.assertEqual(photos.photo_url({"補款佐證(照片)": "", "備註": url(FILE_A)}), "")


class DownloadTests(unittest.TestCase):
    def test_file_id_from_gas_url(self):
        self.assertEqual(photos.drive_file_id(url(FILE_A)), FILE_A)
        self.assertEqual(photos.drive_file_id(f"https://drive.google.com/file/d/{FILE_A}/view"), FILE_A)
        self.assertEqual(photos.drive_file_id("沒有網址"), "")

    def test_tries_original_download_first(self):
        with mock.patch.object(requests, "get", return_value=_Resp()) as get:
            content, content_type, error = photos.download_drive_image(FILE_A)
        self.assertEqual((content, content_type, error), (JPEG, "image/jpeg", ""))
        self.assertIn("uc?export=download", get.call_args_list[0].args[0])

    def test_html_login_page_is_a_failure_then_falls_back_to_lh3(self):
        responses = [_Resp(content=b"<!DOCTYPE html><html>", content_type="text/html"), _Resp()]
        with mock.patch.object(requests, "get", side_effect=responses) as get:
            content, _, error = photos.download_drive_image(FILE_A)
        self.assertEqual((content, error), (JPEG, ""))
        self.assertIn("lh3.googleusercontent.com", get.call_args_list[1].args[0])

    def test_both_fail_gives_reason(self):
        with mock.patch.object(requests, "get", return_value=_Resp(status=404)):
            content, _, error = photos.download_drive_image(FILE_A)
        self.assertIsNone(content)
        self.assertIn("404", error)

    def test_non_jpeg_is_kept_as_is(self):
        with mock.patch.object(requests, "get", return_value=_Resp(content=b"HEICDATA", content_type="image/heic")):
            content, content_type, error = photos.download_drive_image(FILE_A)
        self.assertEqual((content, content_type, error), (b"HEICDATA", "image/heic", ""))


class CopyBatchTests(PhotoTestCase):
    def test_copies_pending_and_records_failures(self):
        self.sync(["S001", "王小明", url(FILE_A)], ["S002", "王小明", url(FILE_B)], ["S003", "王小明", ""])

        def fake_get(u, **kwargs):
            return _Resp() if FILE_A in u else _Resp(status=403)

        with mock.patch.object(requests, "get", side_effect=fake_get):
            result = photos.copy_batch()
        self.assertEqual(result, {"copied": 1, "failed": 1, "remaining": 0})
        self.assertEqual(self.blobs[self.doc("S001")["photo_blob"]], (JPEG, "image/jpeg"))
        self.assertIn("403", self.doc("S002")["photo_error"])
        summary = photos.summary()
        self.assertEqual(summary["counts"], {"none": 1, "done": 1, "failed": 1, "pending": 0})
        self.assertEqual(summary["with_photo"], 2)
        self.assertEqual(summary["failed"][0]["salary_id"], "S002")

    def test_failed_ones_only_retried_when_asked(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp(status=500)):
            photos.copy_batch()
        with mock.patch.object(requests, "get", return_value=_Resp()) as get:
            self.assertEqual(photos.copy_batch()["copied"], 0)
            get.assert_not_called()
            self.assertEqual(photos.copy_batch(include_failed=True)["copied"], 1)
        self.assertEqual(photos.photo_status(self.doc("S001")), "done")

    def test_resync_keeps_copied_photo_and_changed_url_is_recopied(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp()):
            photos.copy_batch()
        self.sync(["S001", "王小明（改名）", url(FILE_A)])
        self.assertEqual(photos.photo_status(self.doc("S001")), "done")
        self.sync(["S001", "王小明", url(FILE_B)])
        self.assertEqual(photos.photo_status(self.doc("S001")), "pending")

    def test_stops_when_time_is_up(self):
        self.sync(["S001", "a", url(FILE_A)], ["S002", "b", url(FILE_B)])
        with mock.patch.object(requests, "get", return_value=_Resp()):
            result = photos.copy_batch(seconds=-1)
        self.assertEqual(result, {"copied": 0, "failed": 0, "remaining": 2})

    def test_upload_failure_is_recorded(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp()), \
                mock.patch.object(photos, "upload_photo", side_effect=RuntimeError("bucket 不見了")):
            photos.copy_batch()
        self.assertIn("bucket 不見了", self.doc("S001")["photo_error"])


class PhotoRouteTests(PhotoTestCase):
    def setUp(self):
        super().setUp()
        self.account = ADMIN
        for p in (
            mock.patch.object(platform_accounts, "current_account", side_effect=lambda request: self.account),
            mock.patch.object(finance_routes, "SALARY_PHOTO_GCS_BUCKET", "bucket"),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)

    def test_page_asks_to_sync_first(self):
        self.assertIn("請先完成第 1 項的同步", self.client.get("/finance/migration").text)

    def test_button_copies_and_page_shows_progress(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp()):
            resp = self.client.post("/finance/migration/photos", follow_redirects=False)
        self.assertIn("notice=photos&copied=1&failed=0&remaining=0", resp.headers["location"])
        html = self.client.get(resp.headers["location"]).text
        self.assertIn("這一批搬好 1 張、失敗 0 張，還有 0 張待搬", html)
        self.assertIn('href="/finance/migration/photo/S001"', html)

    def test_photo_view(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp()):
            photos.copy_batch()
        resp = self.client.get("/finance/migration/photo/S001")
        self.assertEqual((resp.status_code, resp.content, resp.headers["content-type"]), (200, JPEG, "image/jpeg"))
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.client.get("/finance/migration/photo/S999").status_code, 404)

    def test_unusual_type_is_downloaded_not_shown(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(requests, "get", return_value=_Resp(content=b"SVGDATA", content_type="image/svg+xml")):
            photos.copy_batch()
        resp = self.client.get("/finance/migration/photo/S001")
        self.assertEqual(resp.headers["content-type"], "application/octet-stream")
        self.assertTrue(resp.headers["content-disposition"].startswith("attachment"))

    def test_finance_staff_cannot_copy_or_view(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        self.account = FINANCE
        with mock.patch.object(requests, "get", return_value=_Resp()) as get:
            self.client.post("/finance/migration/photos", follow_redirects=False)
            get.assert_not_called()
        self.assertEqual(self.client.get("/finance/migration/photo/S001", follow_redirects=False).status_code, 303)

    def test_missing_bucket_setting(self):
        self.sync(["S001", "王小明", url(FILE_A)])
        with mock.patch.object(finance_routes, "SALARY_PHOTO_GCS_BUCKET", ""):
            resp = self.client.post("/finance/migration/photos")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("DELIVERY_GCS_BUCKET", resp.text)


if __name__ == "__main__":
    unittest.main()
