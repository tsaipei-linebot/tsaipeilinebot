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
from services import tabular_upload


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
    """/import/template.xlsx。

    **這個類別原本斷言的是「CSV 範本要帶 UTF-8 BOM」**（2026-09-12 修
    Windows 版 Excel 開 CSV 亂碼時加的）。2026-09-23 範本整個改成 .xlsx
    之後那兩條斷言就失效了——BOM 是 CSV 才有的東西——所以直接改成驗證
    Excel 範本。改成 Excel 的原因見 services/tabular_upload.py 開頭：
    同仁另存成 CSV 時 Big5 放不下的姓名用字會被 Excel 換成 `?`，加 BOM
    只解決「我們輸出的檔案」的編碼，解決不了「同仁存回去」那一步。"""

    def test_response_is_a_real_xlsx_file(self):
        response = import_routes.import_template(redirect=None)
        self.assertTrue(tabular_upload.looks_like_xlsx(response.body))
        self.assertIn("xlsx", response.headers["content-disposition"])

    def test_template_headers_are_all_present(self):
        response = import_routes.import_template(redirect=None)
        rows, header_error = tabular_upload.read_rows(response.body)
        self.assertIsNone(header_error)
        self.assertEqual(
            tabular_upload.header_names(rows), {"廠商", "姓名", "身分證字號", "電話", "到職日期"}
        )

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


class ParsePersonnelFromExcelTests(unittest.TestCase):
    """2026-09-23 起匯入也收 .xlsx（為什麼要改見 services/tabular_upload.py
    開頭）。這裡走的是「產範本 → 當成同仁填好的檔案 → 解析」完整一圈，
    不是只測解析。"""

    def _filled_template(self, extra_rows):
        import io

        import openpyxl

        from services import tabular_upload

        content = tabular_upload.build_template_xlsx(
            ["廠商", "姓名", "身分證字號", "電話", "到職日期"],
            ["蝦皮三輪", "王小明", "A123456789", "0912345678", "2024-01-31"],
            ("身分證字號", "電話"),
        )
        workbook = openpyxl.load_workbook(io.BytesIO(content))
        for row in extra_rows:
            workbook.active.append(row)
        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()

    def test_template_sample_row_parses_and_keeps_leading_zero_phone(self):
        rows, header_error = parse_personnel_csv(self._filled_template([]))
        self.assertIsNone(header_error)
        self.assertTrue(rows[0]["ok"], rows[0])
        self.assertEqual(rows[0]["row"], 2)
        self.assertEqual(rows[0]["phone"], "0912345678")
        self.assertEqual(rows[0]["hire_date"], "2024-01-31")

    def test_name_with_characters_big5_cannot_hold_survives(self):
        """「堃」「喆」不在 Big5 裡，存成 CSV 會被 Excel 換成 `?`；
        走 .xlsx 要完好無缺——這就是這次改動要解決的問題本身。"""
        content = self._filled_template([["蝦皮三輪", "陳堃喆", "B234567890", "0987654321", "2024-02-01"]])
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(rows[1]["name"], "陳堃喆")
        self.assertEqual(rows[1]["row"], 3)

    def test_real_date_cell_is_accepted(self):
        # 同仁在 Excel 打日期，Excel 存的是日期型別而不是文字
        import datetime

        content = self._filled_template([["蝦皮三輪", "李四", "C345678901", "0911222333", datetime.datetime(2024, 3, 5)]])
        rows, _ = parse_personnel_csv(content)
        self.assertEqual(rows[1]["hire_date"], "2024-03-05")

    def test_old_utf8_csv_still_works(self):
        """舊的、已經填好的 CSV 不能因為改版就突然匯不進去。"""
        csv_bytes = "廠商,姓名,身分證字號,電話,到職日期\n蝦皮三輪,陳堃喆,B234567890,0987654321,2024-02-01\n".encode("utf-8-sig")
        rows, header_error = parse_personnel_csv(csv_bytes)
        self.assertIsNone(header_error)
        self.assertEqual(rows[0]["name"], "陳堃喆")
        self.assertEqual(rows[0]["row"], 2)
