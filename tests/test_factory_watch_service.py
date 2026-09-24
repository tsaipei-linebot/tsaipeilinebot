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

    def test_unparseable_date_is_excluded(self):
        """2026-09-24 改：名錄是全台好幾萬家工廠，日期看不懂也當成新的，第一次
        跑就會把沒填登記核准日期的全部灌進來（見 _within_lookback 的說明）。"""
        self.assertFalse(fw._within_lookback({"approval_date": None}, lookback_days=10))


class DedupKeyTests(unittest.TestCase):
    def test_prefers_factory_registration_number(self):
        """同一家公司（統一編號相同）常有好幾座工廠，要用工廠登記編號區分。"""
        record = {"tax_id": "16396083", "reg_no": "99641358", "name": "點鑫產業股份有限公司二廠", "address": "臺中市"}
        self.assertEqual(fw._dedup_key(record), "reg:99641358")

    def test_falls_back_to_tax_id(self):
        record = {"tax_id": "12345678", "reg_no": "", "name": "測試工廠", "address": "台北市"}
        self.assertEqual(fw._dedup_key(record), "tax:12345678")

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


# 2026-09-24 實測的真實檔案結構（使用者在 Cloud Shell 一步一步下載出來的）：
# data.gov.tw 資料集掛的 CSV 只是一份「目錄」，真正的名錄在目錄指到的 ZIP 裡。
_INDEX_CSV = (
    "﻿序號,年份,名稱,檔案格式,下載連結\r\n"
    "1,113,登記工廠名錄,ZIP,https://serv.gcis.nat.gov.tw/RDownLoad/Data/statistical/"
    "%E7%94%9F%E7%94%A2%E4%B8%AD%E5%B7%A5%E5%BB%A0%E6%B8%85%E5%86%8A.zip\r\n"
)
_REAL_HEADER = (
    "工廠名稱,工廠登記編號,工廠設立許可案號,工廠地址,工廠市鎮鄉村里,工廠負責人姓名,統一編號,"
    "工廠組織型態,工廠設立核准日期,工廠登記核准日期,工廠登記狀態,產業類別,主要產品"
)


def _roc(d):
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def _real_csv(rows):
    lines = [_REAL_HEADER] + [",".join(f'"{v}"' for v in row) for row in rows]
    return ("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8")


def _zip_of(csv_bytes, name="11508.csv"):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, csv_bytes)
    return buffer.getvalue()


def _factory_row(name, reg_no, tax_id, approval, industry="08食品製造業  "):
    return [name, reg_no, "", "臺中市太平區鵬儀路366巷1弄1號", "", "謝嫦娥", tax_id, "股份有限公司", "", approval, "生產中", industry, ""]


class IndexCsvTests(unittest.TestCase):
    def test_index_csv_points_to_zip(self):
        url = fw.pick_archive_url_from_index(_INDEX_CSV.lstrip("﻿"))
        self.assertTrue(url.startswith("https://serv.gcis.nat.gov.tw/"))
        self.assertTrue(url.endswith(".zip"))

    def test_picks_latest_year_when_several(self):
        text = "序號,年份,名稱,檔案格式,下載連結\n1,112,舊,ZIP,https://x/old.zip\n2,113,新,ZIP,https://x/new.zip\n"
        self.assertEqual(fw.pick_archive_url_from_index(text), "https://x/new.zip")

    def test_real_data_csv_is_not_an_index(self):
        self.assertEqual(fw.pick_archive_url_from_index(_REAL_HEADER + "\n"), "")

    def test_reads_csv_inside_zip(self):
        import csv

        content = _real_csv([_factory_row("點晶科技股份有限公司", "95A00371", "97334073", "0900615")])
        rows = list(csv.DictReader(fw._open_csv_text_from_zip(_zip_of(content))))
        self.assertEqual(rows[0]["工廠名稱"], "點晶科技股份有限公司")
        self.assertEqual(rows[0]["工廠登記核准日期"], "0900615")


class RunWeeklyScanTests(unittest.TestCase):
    """用真實的檔案結構（目錄 CSV → ZIP → 名錄 CSV）跑一次完整流程。"""

    def setUp(self):
        from unittest import mock

        from salesdev import repository
        from tests._fake_firestore import FakeFirestore

        self.db = FakeFirestore()
        recent = _roc(date.today() - timedelta(days=20))
        old = _roc(date.today() - timedelta(days=400))
        zip_bytes = _zip_of(_real_csv([
            _factory_row("點鑫產業股份有限公司", "99641359", "16396083", recent),
            # 同一家公司（統一編號相同）的第二座工廠，要當成另一家新工廠
            _factory_row("點鑫產業股份有限公司二廠", "99641358", "16396083", recent),
            _factory_row("老工廠", "11111111", "22222222", old),
            _factory_row("沒日期工廠", "33333333", "44444444", ""),
        ]))

        class _Resp:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        def fake_get(url, timeout=None):
            return _Resp(_INDEX_CSV.encode("utf-8") if url.endswith(".csv") else zip_bytes)

        for patcher in (
            mock.patch.object(fw, "db", self.db),
            mock.patch.object(repository, "get_db", return_value=self.db),
            mock.patch.object(fw, "_discover_csv_url", return_value="https://www.ida.gov.tw/opendata/02/SDD6569.csv"),
            mock.patch.object(fw, "FACTORY_WATCH_LINE_TARGET_ID", ""),
            mock.patch.object(fw, "FACTORY_WATCH_LOOKBACK_DAYS", 60),
            mock.patch.object(fw.requests, "get", side_effect=fake_get),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_first_run_saves_recent_factories_including_second_plant(self):
        from salesdev import repository

        summary = fw.run_weekly_scan(None)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["fetched"], 4)
        self.assertEqual(summary["undated"], 1)
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["new_count"], 2)
        factories = repository.list_factories()
        self.assertEqual(sorted(f["name"] for f in factories), ["點鑫產業股份有限公司", "點鑫產業股份有限公司二廠"])
        self.assertEqual(factories[0]["industry"], "08食品製造業")

    def test_second_run_does_not_repeat(self):
        fw.run_weekly_scan(None)
        summary = fw.run_weekly_scan(None)
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["new_count"], 0)


if __name__ == "__main__":
    unittest.main()