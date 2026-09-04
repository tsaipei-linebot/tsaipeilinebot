import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.incident_report import handle_incident_report, parse_incident_report
from delivery.repository import incident_matches_filters

_VALID_TEXT = (
    "意外事件回傳格式\n"
    "1.廠商名稱：UD\n"
    "2.身分類別：雇傭\n"
    "3.人員名稱：林子椉\n"
    "4.發生時間：9/4 11:00\n"
    "5.發生地點：金山南路一段126號\n"
    "6.執行勤務中/上下班途中：執行勤務中\n"
    "7.是否報警：有\n"
    "8.受傷情形：無\n"
    "9.是否聯繫家屬：無\n"
    "10.是否牽扯他人：有\n"
    "11.意外事件經過：行進其間與汽車後照鏡擦撞\n"
    "\n"
    "★風險等級：(此欄不用填寫)"
)


def _replace_field(text: str, old_line_prefix: str, new_line: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(old_line_prefix):
            lines[i] = new_line
            break
    return "\n".join(lines)


class ParseIncidentReportTests(unittest.TestCase):
    def test_valid_message_parses(self):
        result = parse_incident_report(_VALID_TEXT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["vendor"], "ud")
        self.assertEqual(result["identity_type"], "雇傭")
        self.assertEqual(result["personnel_name"], "林子椉")
        self.assertEqual(result["occurred_at"], f"{__import__('datetime').date.today().year}-09-04 11:00")
        self.assertEqual(result["location"], "金山南路一段126號")
        self.assertEqual(result["duty_status"], "執行勤務中")
        self.assertEqual(result["police_called"], "有")
        self.assertEqual(result["injury"], "無")
        self.assertEqual(result["family_contacted"], "無")
        self.assertEqual(result["third_party_involved"], "有")
        self.assertEqual(result["description"], "行進其間與汽車後照鏡擦撞")

    def test_tolerates_alternate_numbering_style(self):
        text = _VALID_TEXT.replace("1.廠商名稱", "1、廠商名稱").replace("2.身分類別", "2．身分類別").replace(
            "3.人員名稱", "人員名稱"
        )
        result = parse_incident_report(text)
        self.assertTrue(result["ok"], result)

    def test_missing_trigger_line_is_not_a_report(self):
        text = _VALID_TEXT.replace("意外事件回傳格式\n", "")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_a_report")

    def test_unrelated_text_is_not_a_report(self):
        result = parse_incident_report("早安，今天天氣不錯")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_a_report")

    def test_missing_field_is_missing_fields(self):
        text = _replace_field(_VALID_TEXT, "11.意外事件經過", "11.意外事件經過：")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_fields")

    def test_invalid_vendor(self):
        text = _replace_field(_VALID_TEXT, "1.廠商名稱", "1.廠商名稱：黑貓")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_vendor")

    def test_invalid_identity_type(self):
        text = _replace_field(_VALID_TEXT, "2.身分類別", "2.身分類別：正職")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_identity_type")

    def test_invalid_duty_status(self):
        text = _replace_field(_VALID_TEXT, "6.執行勤務中/上下班途中", "6.執行勤務中/上下班途中：休假中")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_duty_status")

    def test_invalid_police_called(self):
        text = _replace_field(_VALID_TEXT, "7.是否報警", "7.是否報警：已報案")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_police_called")

    def test_invalid_family_contacted(self):
        text = _replace_field(_VALID_TEXT, "9.是否聯繫家屬", "9.是否聯繫家屬：正在聯繫")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_family_contacted")

    def test_invalid_third_party_involved(self):
        text = _replace_field(_VALID_TEXT, "10.是否牽扯他人", "10.是否牽扯他人：不確定")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_third_party_involved")

    def test_invalid_datetime(self):
        text = _replace_field(_VALID_TEXT, "4.發生時間", "4.發生時間：昨天中午")
        result = parse_incident_report(text)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_datetime")

    def test_handle_incident_report_stays_silent_for_unrelated_text(self):
        self.assertEqual(handle_incident_report("早安，今天天氣不錯"), "")

    @patch("delivery.repository.create_incident_event")
    def test_handle_incident_report_writes_and_replies(self, mock_create):
        mock_create.return_value = "incident123"
        reply = handle_incident_report(_VALID_TEXT)
        self.assertTrue(mock_create.called)
        self.assertIn("林子椉", reply)
        self.assertIn("✅", reply)


class IncidentMatchesFiltersTests(unittest.TestCase):
    def _incident(self, **overrides):
        base = {
            "vendor": "ud",
            "status": "open",
            "risk_level": "",
            "personnel_name": "林子椉",
        }
        base.update(overrides)
        return base

    def test_no_filters_matches(self):
        self.assertTrue(incident_matches_filters(self._incident()))

    def test_vendor_filter_excludes_non_matching(self):
        self.assertFalse(incident_matches_filters(self._incident(), vendor_filter="shopee"))

    def test_status_filter_excludes_non_matching(self):
        self.assertFalse(incident_matches_filters(self._incident(), status_filter="closed"))

    def test_risk_level_filter_excludes_non_matching(self):
        self.assertFalse(incident_matches_filters(self._incident(risk_level="低"), risk_level_filter="高"))

    def test_risk_level_filter_matches(self):
        self.assertTrue(incident_matches_filters(self._incident(risk_level="高"), risk_level_filter="高"))

    def test_personnel_name_filter_matches_substring(self):
        self.assertTrue(incident_matches_filters(self._incident(), personnel_name_filter="子椉"))

    def test_personnel_name_filter_excludes_non_matching(self):
        self.assertFalse(incident_matches_filters(self._incident(), personnel_name_filter="王小明"))


if __name__ == "__main__":
    unittest.main()
