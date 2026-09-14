import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from openpyxl import load_workbook

from services.contract_summary_excel import (
    _sanitize_cell,
    build_client_contract_summary_workbook,
    build_dispatch_contract_summary_workbook,
    build_merged_summary_workbook,
)


class BuildClientContractSummaryWorkbookTests(unittest.TestCase):
    def test_header_and_row_content(self):
        rows = [{
            "client_name": "測試客戶", "tax_id": "12345678", "year": 2026, "party_b_name": "材霈甲公司",
            "contract_version": "hourly_flat_rate", "pricing_summary": "時薪 196／管理費 20",
            "remit_day": "10", "submitted_by": "alice",
        }]
        contract_versions = {"hourly_flat_rate": {"label": "時薪一口價"}}
        content = build_client_contract_summary_workbook(rows, contract_versions)

        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        row = [cell.value for cell in ws[2]]
        self.assertEqual(header, ["客戶名稱", "統一編號", "合約年", "簽約公司", "合約版本", "報價方式", "匯款截止日", "送出人"])
        self.assertEqual(row, ["測試客戶", "12345678", 2026, "材霈甲公司", "時薪一口價", "時薪 196／管理費 20", "10", "alice"])

    def test_unknown_version_falls_back_to_raw_code(self):
        rows = [{
            "client_name": "A", "tax_id": "", "year": 2026, "party_b_name": "",
            "contract_version": "some_unknown_code", "pricing_summary": "", "remit_day": "", "submitted_by": "",
        }]
        content = build_client_contract_summary_workbook(rows, {})
        wb = load_workbook(io.BytesIO(content))
        row = [cell.value for cell in wb.active[2]]
        self.assertEqual(row[4], "some_unknown_code")


class BuildDispatchContractSummaryWorkbookTests(unittest.TestCase):
    def test_header_and_row_content(self):
        rows = [{
            "client_name": "pchome", "submitted_by": "bob", "updated_year": 2026, "title": "日班", "hours": "9-18",
            "wage": "196", "bonus": "－", "overtime": "－", "pay_cycle": "每月10號",
        }]
        content = build_dispatch_contract_summary_workbook(rows)
        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        row = [cell.value for cell in ws[2]]
        self.assertEqual(header, ["客戶名稱", "送出人", "最近異動年份", "職稱/班別", "工作時間", "時薪", "工時獎金", "加班", "結薪週期"])
        self.assertEqual(row, ["pchome", "bob", 2026, "日班", "9-18", "196", "－", "－", "每月10號"])

    def test_none_updated_year_becomes_blank_cell(self):
        # openpyxl 存讀空字串會正規化成 None（實測過），所以這裡驗證的是
        # 「不是 0 或字面上的 'None' 這種難看的值」，不是驗證真的存成 ""。
        rows = [{
            "client_name": "pchome", "submitted_by": "bob", "updated_year": None, "title": "日班", "hours": "",
            "wage": "", "bonus": "", "overtime": "", "pay_cycle": "",
        }]
        content = build_dispatch_contract_summary_workbook(rows)
        wb = load_workbook(io.BytesIO(content))
        row = [cell.value for cell in wb.active[2]]
        self.assertIsNone(row[2])


class BuildMergedSummaryWorkbookTests(unittest.TestCase):
    def test_header_has_a_column_group_per_shift_slot(self):
        rows = [{
            "client_name": "A公司", "tax_id": "12345678", "year": 2026, "party_b_name": "材霈甲公司",
            "contract_version": "hourly_flat_rate", "pricing_summary": "時薪 196／管理費 20",
            "remit_day": "10", "submitted_by": "alice",
            "dispatch_pay_cycle": "每月10號", "dispatch_submitted_by": "bob",
            "shift_columns": [
                {"title": "日班", "hours": "9-18", "wage": "196", "bonus": "－", "overtime": "－"},
                {"title": "夜班", "hours": "22-7", "wage": "210", "bonus": "－", "overtime": "－"},
            ],
        }]
        content = build_merged_summary_workbook(rows, 2, {"hourly_flat_rate": {"label": "時薪一口價"}})
        wb = load_workbook(io.BytesIO(content))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        row = [cell.value for cell in ws[2]]
        self.assertEqual(header, [
            "客戶名稱", "統一編號", "合約年", "簽約公司", "合約版本", "報價方式", "匯款截止日", "合約送出人",
            "契約結薪週期", "契約送出人",
            "班別1-職稱", "班別1-工作時間", "班別1-時薪", "班別1-工時獎金", "班別1-加班",
            "班別2-職稱", "班別2-工作時間", "班別2-時薪", "班別2-工時獎金", "班別2-加班",
        ])
        self.assertEqual(row[:10], ["A公司", "12345678", 2026, "材霈甲公司", "時薪一口價", "時薪 196／管理費 20", "10", "alice", "每月10號", "bob"])
        self.assertEqual(row[10:15], ["日班", "9-18", "196", "－", "－"])
        self.assertEqual(row[15:20], ["夜班", "22-7", "210", "－", "－"])

    def test_no_shift_slots_still_produces_valid_header(self):
        content = build_merged_summary_workbook([], 0, {})
        wb = load_workbook(io.BytesIO(content))
        header = [cell.value for cell in wb.active[1]]
        self.assertEqual(len(header), 10)


class SanitizeCellTests(unittest.TestCase):
    """防 Excel 公式注入：客戶名稱、統編、匯款截止日、班別這些欄位都是
    同仁填的自由文字，開頭是 =/+/-/@ 的話 openpyxl 會標記成公式，Excel
    打開時可能被當成可執行的公式跑出來（例如 HYPERLINK 導去釣魚網站）。"""

    def test_leading_equals_sign_is_escaped(self):
        self.assertEqual(_sanitize_cell("=HYPERLINK(\"http://evil.example\")"), "'=HYPERLINK(\"http://evil.example\")")

    def test_leading_plus_minus_at_are_escaped(self):
        self.assertEqual(_sanitize_cell("+1+1"), "'+1+1")
        self.assertEqual(_sanitize_cell("-1+1"), "'-1+1")
        self.assertEqual(_sanitize_cell("@SUM(A1)"), "'@SUM(A1)")

    def test_normal_text_is_unchanged(self):
        self.assertEqual(_sanitize_cell("測試客戶股份有限公司"), "測試客戶股份有限公司")

    def test_fullwidth_dash_placeholder_is_unchanged(self):
        # 「－」是全形符號，跟觸發公式的半形 "-" 不是同一個字元，班別總表
        # 常用它當「無資料」的顯示占位符，不該被誤判成需要跳脫。
        self.assertEqual(_sanitize_cell("－"), "－")

    def test_non_string_values_are_unchanged(self):
        self.assertEqual(_sanitize_cell(2026), 2026)
        self.assertEqual(_sanitize_cell(None), None)


class FormulaInjectionInWorkbooksTests(unittest.TestCase):
    def _formula_looking_text(self):
        return "=HYPERLINK(\"http://evil.example\",\"點我\")"

    def test_client_contract_summary_escapes_client_name(self):
        rows = [{
            "client_name": self._formula_looking_text(), "tax_id": "", "year": 2026, "party_b_name": "",
            "contract_version": "hourly_flat_rate", "pricing_summary": "", "remit_day": "", "submitted_by": "",
        }]
        content = build_client_contract_summary_workbook(rows, {})
        wb = load_workbook(io.BytesIO(content))
        cell = wb.active[2][0]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("'="))

    def test_dispatch_contract_summary_escapes_title(self):
        rows = [{
            "client_name": "A", "submitted_by": "bob", "updated_year": 2026,
            "title": self._formula_looking_text(), "hours": "", "wage": "", "bonus": "", "overtime": "",
            "pay_cycle": "",
        }]
        content = build_dispatch_contract_summary_workbook(rows)
        wb = load_workbook(io.BytesIO(content))
        cell = wb.active[2][3]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("'="))

    def test_merged_summary_escapes_shift_column_value(self):
        rows = [{
            "client_name": "A", "tax_id": "", "year": 2026, "party_b_name": "", "contract_version": "hourly_flat_rate",
            "pricing_summary": "", "remit_day": "", "submitted_by": "", "dispatch_pay_cycle": "", "dispatch_submitted_by": "",
            "shift_columns": [{"title": self._formula_looking_text(), "hours": "", "wage": "", "bonus": "", "overtime": ""}],
        }]
        content = build_merged_summary_workbook(rows, 1, {})
        wb = load_workbook(io.BytesIO(content))
        cell = wb.active[2][10]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("'="))


if __name__ == "__main__":
    unittest.main()
