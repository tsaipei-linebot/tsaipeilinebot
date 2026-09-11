import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

import docx

from services.dispatch_contract_service import (
    CLAUSE_DEFAULTS,
    CLAUSE_ORDER,
    SHIFT_COLUMN_CODES,
    build_shift_rows,
    render_contract_docx,
)

_SYSTEM_TOKENS = [
    "${com_full_name}", "${rec_name}", "${rec_id}", "${cus_name}", "${rec_rdate}",
    "${pap_sign}", "${com_leader}", "${com_id}", "${com_address}",
]


class BuildShiftRowsTests(unittest.TestCase):
    """build_shift_rows() 是純函式（不碰 Firestore），涵蓋欄位勾選跟
    忽略空白列這兩條核心規則。"""

    def test_disabled_column_shows_dash_even_if_filled(self):
        rows = build_shift_rows(
            [{"title": "日班", "hours": "08:00-17:00", "wage": "依法定", "bonus": "", "overtime": ""}],
            enabled_columns=["title", "wage"],
        )
        self.assertEqual(rows, [{"title": "日班", "hours": "－", "wage": "依法定", "bonus": "－", "overtime": "－"}])

    def test_enabled_column_left_blank_shows_dash(self):
        rows = build_shift_rows(
            [{"title": "", "hours": "", "wage": "196/hr", "bonus": "", "overtime": ""}],
            enabled_columns=SHIFT_COLUMN_CODES,
        )
        self.assertEqual(rows[0]["title"], "－")
        self.assertEqual(rows[0]["wage"], "196/hr")

    def test_completely_empty_row_is_dropped(self):
        rows = build_shift_rows(
            [{"title": "", "hours": "", "wage": "", "bonus": "", "overtime": ""},
             {"title": "PT", "hours": "", "wage": "", "bonus": "", "overtime": ""}],
            enabled_columns=SHIFT_COLUMN_CODES,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "PT")

    def test_unknown_column_code_is_ignored(self):
        rows = build_shift_rows(
            [{"title": "日班", "hours": "", "wage": "", "bonus": "", "overtime": ""}],
            enabled_columns=["title", "not_a_real_column"],
        )
        self.assertEqual(rows, [{"title": "日班", "hours": "－", "wage": "－", "bonus": "－", "overtime": "－"}])


class RenderContractDocxTests(unittest.TestCase):
    """render_contract_docx() 會真的讀 assets/ 底下的 master template 檔案、
    用 docxtpl 套版，但不碰 Firestore／GCS，照既有分工歸類為可以直接測的
    純邏輯（檔案讀取是本地固定資源，不是外部服務）。"""

    def _render(self, **overrides):
        defaults = dict(
            work_address="台北市內湖區瑞光路588號",
            work_content="收銀結帳",
            pay_cycle="每月1號至月底",
            shifts=[{"title": "日班", "hours": "08:00-17:00", "wage": "依法定", "bonus": "每小時+津貼", "overtime": "依法規"}],
            clauses={},
        )
        defaults.update(overrides)
        content = render_contract_docx(**defaults)
        path = "/tmp/_test_dispatch_contract_render.docx"
        with open(path, "wb") as f:
            f.write(content)
        return docx.Document(path)

    def test_system_placeholder_tokens_are_untouched(self):
        doc = self._render()
        full_text = "\n".join(p.text for p in doc.paragraphs)
        for token in _SYSTEM_TOKENS:
            self.assertIn(token, full_text, f"{token} 不見了，不應該被套版動到")

    def test_variable_fields_are_substituted(self):
        doc = self._render(work_address="測試地址ABC", work_content="測試內容XYZ")
        full_text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("測試地址ABC", full_text)
        self.assertIn("測試內容XYZ", full_text)
        self.assertNotIn("{{", full_text)
        self.assertNotIn("{%", full_text)

    def test_shift_rows_appear_in_table(self):
        doc = self._render(shifts=[
            {"title": "日班", "hours": "08:00-17:00", "wage": "依法定", "bonus": "每小時+津貼", "overtime": "依法規"},
            {"title": "夜班", "hours": "20:00-05:00", "wage": "依法定", "bonus": "每小時+津貼", "overtime": "依法規"},
        ])
        table = doc.tables[0]
        rows_text = [[c.text for c in row.cells] for row in table.rows]
        self.assertIn(["日班", "08:00-17:00", "依法定", "每小時+津貼", "依法規"], rows_text)
        self.assertIn(["夜班", "20:00-05:00", "依法定", "每小時+津貼", "依法規"], rows_text)

    def test_missing_clause_falls_back_to_default(self):
        doc = self._render(clauses={})
        full_text = "\n".join(p.text for p in doc.paragraphs)
        for key in CLAUSE_ORDER:
            # 預設文字可能被 \n 拆成好幾段，這裡只確認開頭那一小段有出現。
            snippet = CLAUSE_DEFAULTS[key].split("\n")[0][:10]
            self.assertIn(snippet, full_text)

    def test_clause_override_replaces_default(self):
        doc = self._render(clauses={"benefits_note": "這是測試用的自訂福利說明ABC"})
        full_text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("這是測試用的自訂福利說明ABC", full_text)
        self.assertNotIn(CLAUSE_DEFAULTS["benefits_note"], full_text)


class ClauseConfigConsistencyTests(unittest.TestCase):
    def test_clause_defaults_has_exactly_the_clause_order_keys(self):
        self.assertEqual(set(CLAUSE_DEFAULTS.keys()), set(CLAUSE_ORDER))


if __name__ == "__main__":
    unittest.main()
