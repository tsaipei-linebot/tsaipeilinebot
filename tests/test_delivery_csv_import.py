import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.csv_import import parse_personnel_csv


class ParsePersonnelCsvTests(unittest.TestCase):
    def test_valid_rows_with_vendor_name_and_code(self):
        content = (
            "廠商,姓名,身分證字號,電話\n"
            "蝦皮,王小明,A123456789,0912345678\n"
            "ud,李小華,,\n"
        ).encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"row": 2, "ok": True, "vendor": "shopee", "name": "王小明", "id_number": "A123456789", "phone": "0912345678"})
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
        content = "廠商,姓名\n蝦皮,\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["error"], "姓名為空")

    def test_completely_blank_row_is_skipped_silently(self):
        content = "廠商,姓名\n蝦皮,王小明\n,\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)

    def test_missing_required_header_returns_header_error(self):
        content = "廠商\n蝦皮\n".encode("utf-8")
        rows, header_error = parse_personnel_csv(content)
        self.assertEqual(rows, [])
        self.assertIn("姓名", header_error)

    def test_empty_file_returns_header_error(self):
        rows, header_error = parse_personnel_csv(b"")
        self.assertEqual(rows, [])
        self.assertIsNotNone(header_error)

    def test_big5_encoded_file_is_decoded_correctly(self):
        content = "廠商,姓名\n蝦皮,王小明\n".encode("cp950")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(rows[0]["name"], "王小明")

    def test_utf8_bom_is_stripped(self):
        content = "廠商,姓名\n蝦皮,王小明\n".encode("utf-8-sig")
        rows, header_error = parse_personnel_csv(content)
        self.assertIsNone(header_error)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])


if __name__ == "__main__":
    unittest.main()
