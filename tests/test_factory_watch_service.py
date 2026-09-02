import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from tests import _stub_gcp
_stub_gcp.install()

from services import factory_watch_service as fw


class ResolveColumnsTests(unittest.TestCase):
    def test_matches_preferred_approval_date_column_first(self):
        # 資料集裡「工廠登記核准日期」跟「設立許可核准日期」都有出現時，
        # 業務要看的是登記核准日期，不能誤配到設立許可核准日期
        fieldnames = ["工廠名稱", "設立許可核准日期", "工廠登記核准日期", "統一編號"]
        columns = fw._resolve_columns(fieldnames)
        self.assertEqual(columns["approval_date"], "工廠登記核准日期")

    def test_falls_back_to_generic_approval_date_column(self):
        fieldnames = ["工廠名稱", "核准日期"]
        columns = fw._resolve_columns(fieldnames)
        self.assertEqual(columns["approval_date"], "核准日期")

    def test_missing_column_is_absent_from_result(self):
        columns = fw._resolve_columns(["工廠名稱"])
        self.assertNotIn("tax_id", columns)


class ParseRocOrGregorianDateTests(unittest.TestCase):
    def test_roc_seven_digit_date(self):
        self.assertEqual(fw._parse_roc_or_gregorian_date("1130215"), date(2024, 2, 15))

    def test_gregorian_eight_digit_date(self):
        self.assertEqual(fw._parse_roc_or_gregorian_date("20240215"), date(2024, 2, 15))

    def test_slash_separated_roc_date(self):
        self.assertEqual(fw._parse_roc_or_gregorian_date("113/02/15"), date(2024, 2, 15))

    def test_dash_separated_gregorian_date(self):
        self.assertEqual(fw._parse_roc_or_gregorian_date("2024-02-15"), date(2024, 2, 15))

    def test_empty_or_garbage_returns_none(self):
        self.assertIsNone(fw._parse_roc_or_gregorian_date(""))
        self.assertIsNone(fw._parse_roc_or_gregorian_date("不明"))


class WithinLookbackTests(unittest.TestCase):
    def test_recent_date_is_within_lookback(self):
        record = {"approval_date": date.today() - timedelta(days=3)}
        self.assertTrue(fw._within_lookback(record, lookback_days=10))

    def test_old_date_is_outside_lookback(self):
        record = {"approval_date": date.today() - timedelta(days=30)}
        self.assertFalse(fw._within_lookback(record, lookback_days=10))

    def test_unparseable_date_is_not_excluded(self):
        # 日期格式無法辨識時不能直接濾掉，要交給去重機制把關，避免漏掉真正的新工廠
        self.assertTrue(fw._within_lookback({"approval_date": None}, lookback_days=10))


class DedupKeyTests(unittest.TestCase):
    def test_prefers_tax_id(self):
        record = {"tax_id": "12345678", "reg_no": "REG001", "name": "測試工廠", "address": "台北市"}
        self.assertEqual(fw._dedup_key(record), "tax:12345678")

    def test_falls_back_to_reg_no(self):
        record = {"tax_id": "", "reg_no": "REG001", "name": "測試工廠", "address": "台北市"}
        self.assertEqual(fw._dedup_key(record), "reg:REG001")

    def test_falls_back_to_name_address_hash(self):
        record = {"tax_id": "", "reg_no": "", "name": "測試工廠", "address": "台北市"}
        key = fw._dedup_key(record)
        self.assertTrue(key.startswith("hash:"))
        # 同樣的名稱/地址要產生同一把 key，才能正確去重
        self.assertEqual(key, fw._dedup_key(dict(record)))


class ExtractCountyTests(unittest.TestCase):
    def test_extracts_city(self):
        self.assertEqual(fw._extract_county("新北市板橋區文化路一段"), "新北市")

    def test_extracts_county(self):
        self.assertEqual(fw._extract_county("彰化縣鹿港鎮中山路"), "彰化縣")

    def test_empty_address_returns_empty(self):
        self.assertEqual(fw._extract_county(""), "")


class BuildLineSummaryMessageTests(unittest.TestCase):
    def test_includes_count_and_preview_names(self):
        records = [{"name": f"工廠{i}", "address": "台中市西屯區"} for i in range(3)]
        message = fw.build_line_summary_message(records)
        self.assertIn("3 家新登記工廠", message)
        self.assertIn("工廠0", message)
        self.assertIn("工廠2", message)

    def test_truncates_preview_and_notes_remaining_count(self):
        records = [{"name": f"工廠{i}", "address": "台中市西屯區"} for i in range(8)]
        message = fw.build_line_summary_message(records, preview_limit=5)
        self.assertIn("工廠4", message)
        self.assertNotIn("工廠5", message)
        self.assertIn("等共 8 家", message)


class FindFirstCsvUrlTests(unittest.TestCase):
    def test_finds_csv_url_nested_in_dict_and_list(self):
        payload = {"result": {"distribution": [{"resourceDescription": "csv"}, {"resourceDownloadUrl": "https://example.com/data.csv"}]}}
        self.assertEqual(fw._find_first_csv_url(payload), "https://example.com/data.csv")

    def test_ignores_non_csv_strings(self):
        payload = {"a": "https://example.com/data.json", "b": "not a url"}
        self.assertEqual(fw._find_first_csv_url(payload), "")

    def test_csv_url_with_query_string(self):
        payload = {"url": "https://example.com/data.csv?download=1"}
        self.assertEqual(fw._find_first_csv_url(payload), "https://example.com/data.csv?download=1")


if __name__ == "__main__":
    unittest.main()
