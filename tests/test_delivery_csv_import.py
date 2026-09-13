import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.csv_import import parse_personnel_csv
from delivery.routes import import_routes


class ParsePersonnelCsvTests(unittest.TestCase):
    def test_valid_rows_with_vendor_name_and_code(self):
        content = (
            "廠商,姓名,身分證字號,電話\n"
            "蝦皮三輪,王小明,A123456789,0912345678\n"
            "ud,李小華,,\n"
        ).encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"row": 2, "ok": True, "vendor": "shopee", "name": "王小明", "id_number": "A123456789", "phone": "0912345678", "hire_date": ""})
        self.assertEqual(rows[1]["vendor"], "ud")
        self.assertTrue(rows[1]["ok"])

    def test_unrecognized_vendor_is_reported_as_error_not_raised(self):
        content = "廠商,姓名\n黑貓,王小明\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("黑貓", rows[0]["error"])

    def test_missing_name_is_reported_as_error(self):
        content = "廠商,姓名\n蝦皮三輪,\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["error"], "姓名為空")

    def test_completely_blank_row_is_skipped_silently(self):
        content = "廠商,姓名\n蝦皮三輪,王小明\n,\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)

    def test_missing_required_header_returns_header_error(self):
        content = "廠商\n蝦皮三輪\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertEqual(rows, [])
        self.assertIn("姓名", header_error)

    def test_empty_file_returns_header_error(self):
        rows, header_error = parse_personnel_csv(b"")
        self.assertEqual(rows, [])
        self.assertIsNotNone(header_error)

    def test_big5_encoded_file_is_decoded_correctly(self):
        content = "廠商,姓名\n蝦皮三輪,王小明\n".encode("cp950")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(rows[0]["name"], "王小明")

    def test_utf8_bom_is_stripped(self):
        content = "廠商,姓名\n蝦皮三輪,王小明\n".encode("utf-8-sig")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])

    def test_hire_date_with_dash_separator_is_normalized(self):
        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,2024-01-31\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["hire_date"], "2024-01-31")

    def test_hire_date_with_slash_separator_is_normalized(self):
        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,2024/1/31\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["hire_date"], "2024-01-31")

    def test_hire_date_left_blank_is_valid(self):
        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["hire_date"], "")

    def test_hire_date_missing_column_is_valid(self):
        content = "廠商,姓名\n蝦皮三輪,王小明\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["hire_date"], "")

    def test_unrecognizable_hire_date_is_reported_as_error(self):
        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,113年3月1日\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("到職日期", rows[0]["error"])
        self.assertIn("113年3月1日", rows[0]["error"])


class ImportTemplateDownloadTests(unittest.TestCase):
    """/import/template.csv：2026-09-12 使用者回報下載範本後欄位是亂碼，
    原因是原本用純 UTF-8（無 BOM）輸出，Windows 版 Excel 雙擊開啟 CSV 時
    會用系統的中文編碼（Big5/cp950）去猜、猜錯就整份亂碼。修正成
    utf-8-sig（帶 BOM）後，這裡驗證：(1) 檔案開頭真的有 BOM，(2) 這份
    範本檔案本身可以直接餵回 parse_personnel_csv() 正確解析（下載範本
    填完再上傳的流程不會被 BOM 影響）。"""

    def test_response_has_utf8_bom(self):
        response = import_routes.import_template(redirect=None)
        self.assertTrue(response.body.startswith(b"\xef\xbb\xbf"))

    def test_response_decodes_correctly_as_utf8_sig(self):
        response = import_routes.import_template(redirect=None)
        text = response.body.decode("utf-8-sig")
        self.assertIn("廠商", text)
        self.assertIn("姓名", text)
        self.assertIn("身分證字號", text)
        self.assertIn("電話", text)
        self.assertIn("到職日期", text)

    def test_downloaded_template_round_trips_through_parser(self):
        response = import_routes.import_template(redirect=None)
        rows, header_error = parse_personnel_csv(response.body)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[0]["name"], "王小明")
        self.assertEqual(rows[0]["hire_date"], "2024-01-31")


class ImportSubmitHireDateTests(unittest.TestCase):
    """POST /import：2026-09-12 新增選填的「到職日期」欄位，主要給整批搬遷
    已在職舊資料用。這裡驗證 CSV 裡有填的到職日期，真的會傳給
    repository.create_personnel()，不是解析完就被丟掉。"""

    class _FakeSession(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    class _FakeRequest:
        def __init__(self, user):
            self.session = ImportSubmitHireDateTests._FakeSession({"user": user})

    class _FakeUploadFile:
        def __init__(self, content: bytes):
            self._content = content

        async def read(self):
            return self._content

    def _account(self):
        return {"username": "bob", "modules": {"delivery": "staff"}, "is_platform_admin": False}

    def test_hire_date_from_csv_is_passed_to_create_personnel(self):
        import asyncio

        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,2024-01-31\n".encode("utf-8")
        with mock.patch.object(import_routes.repository, "find_active_personnel_by_name_and_phone", return_value=None):
            with mock.patch.object(import_routes.repository, "create_personnel") as mock_create:
                with mock.patch.object(import_routes, "templates") as mock_templates:
                    asyncio.run(import_routes.import_submit(
                        self._FakeRequest(self._account()), self._FakeUploadFile(content), redirect=None,
                    ))
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["hire_date"], "2024-01-31")
        context = mock_templates.TemplateResponse.call_args[0][2]
        self.assertEqual(len(context["result"]["created"]), 1)

    def test_blank_hire_date_from_csv_is_passed_as_empty_string(self):
        import asyncio

        content = "廠商,姓名,到職日期\n蝦皮三輪,王小明,\n".encode("utf-8")
        with mock.patch.object(import_routes.repository, "find_active_personnel_by_name_and_phone", return_value=None):
            with mock.patch.object(import_routes.repository, "create_personnel") as mock_create:
                with mock.patch.object(import_routes, "templates"):
                    asyncio.run(import_routes.import_submit(
                        self._FakeRequest(self._account()), self._FakeUploadFile(content), redirect=None,
                    ))
        self.assertEqual(mock_create.call_args.kwargs["hire_date"], "")


if __name__ == "__main__":
    unittest.main()
