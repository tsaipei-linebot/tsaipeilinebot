import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from tests import _stub_gcp
_stub_gcp.install()

from services import daily_report_service as dr


def _event(path="ai_decision", offset_minutes=0, latency=1.0, fallback=False, category="", brand=""):
    ts = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)
    return {
        "ts": ts.isoformat(),
        "path": path,
        "latency_seconds": latency,
        "fallback_triggered": fallback,
        "matched_category": category,
        "matched_brand": brand,
    }


class ComputeHealthSummaryTests(unittest.TestCase):
    def test_no_events_is_healthy(self):
        health = dr.compute_health_summary([])
        self.assertFalse(health["level1_triggered"])
        self.assertFalse(health["level2_triggered"])
        self.assertEqual(health["fallback_count"], 0)

    def test_single_fallback_triggers_level1(self):
        events = [_event(fallback=True)]
        health = dr.compute_health_summary(events)
        self.assertTrue(health["level1_triggered"])
        self.assertEqual(health["fallback_count"], 1)

    def test_high_latency_bucket_triggers_level2(self):
        # 同一個 5 分鐘區塊塞 20 筆 latency=20 秒的請求，p95 一定超過門檻 12 秒
        events = [_event(offset_minutes=1, latency=20.0) for _ in range(20)]
        health = dr.compute_health_summary(events, latency_threshold_seconds=12, bucket_minutes=5)
        self.assertTrue(health["level2_triggered"])
        self.assertGreater(health["worst_bucket_p95_seconds"], 12)

    def test_normal_latency_does_not_trigger_level2(self):
        events = [_event(offset_minutes=i, latency=2.0) for i in range(10)]
        health = dr.compute_health_summary(events, latency_threshold_seconds=12, bucket_minutes=5)
        self.assertFalse(health["level2_triggered"])

    def test_direct_intercept_events_excluded_from_latency_bucket(self):
        # path 不是 ai_decision 的事件（例如直達攔截）不該拿去算 p95，
        # 否則會稀釋掉真正需要關注的 AI 決策延遲分布
        events = [_event(path="direct_intercept", latency=999.0)]
        health = dr.compute_health_summary(events)
        self.assertFalse(health["level2_triggered"])
        self.assertIsNone(health["worst_bucket_start"])

    def test_malformed_timestamp_is_skipped_not_crashed(self):
        events = [{"path": "ai_decision", "ts": "not-a-timestamp", "latency_seconds": 5.0}]
        health = dr.compute_health_summary(events)
        self.assertEqual(health["worst_bucket_p95_seconds"], 0.0)


class ComputeKeywordGapCandidatesTests(unittest.TestCase):
    def test_frequent_uncovered_category_is_suggested(self):
        events = [_event(category="理貨") for _ in range(6)]
        candidates = dr.compute_keyword_gap_candidates(events, min_count=5)
        self.assertEqual(candidates, [{"label": "類別:理貨", "count": 6}])

    def test_already_direct_intercepted_category_is_excluded(self):
        # 「外送」「門市」已經有專屬直達路徑，就算被問很多次也不該再建議
        events = [_event(category="外送") for _ in range(10)]
        self.assertEqual(dr.compute_keyword_gap_candidates(events, min_count=5), [])

    def test_below_threshold_is_not_suggested(self):
        events = [_event(category="理貨") for _ in range(3)]
        self.assertEqual(dr.compute_keyword_gap_candidates(events, min_count=5), [])

    def test_direct_intercept_path_events_are_ignored(self):
        events = [_event(path="direct_intercept", category="理貨") for _ in range(10)]
        self.assertEqual(dr.compute_keyword_gap_candidates(events, min_count=5), [])

    def test_brand_and_category_counted_separately_and_sorted_desc(self):
        events = (
            [_event(brand="Coupang") for _ in range(8)]
            + [_event(category="理貨") for _ in range(5)]
        )
        candidates = dr.compute_keyword_gap_candidates(events, min_count=5)
        self.assertEqual(candidates[0], {"label": "廠商:Coupang", "count": 8})
        self.assertEqual(candidates[1], {"label": "類別:理貨", "count": 5})


class BuildReportTextTests(unittest.TestCase):
    def test_healthy_report_has_check_marks_no_warning(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 3.2}
        text = dr.build_report_text(health, [], [], "日")
        self.assertIn("✅", text)
        self.assertNotIn("⚠️", text)

    def test_unhealthy_report_includes_warnings(self):
        health = {"level1_triggered": True, "fallback_count": 2, "level2_triggered": True, "worst_bucket_p95_seconds": 15.0}
        text = dr.build_report_text(health, [], [], "日")
        self.assertIn("⚠️", text)
        self.assertIn("2 次保底訊息", text)

    def test_faq_candidates_and_keyword_gaps_included_only_when_present(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 1.0}
        text = dr.build_report_text(health, ["加班費怎麼計算？"], [{"label": "類別:理貨", "count": 6}], "週")
        self.assertIn("FAQ 候選清單", text)
        self.assertIn("加班費怎麼計算？", text)
        self.assertIn("建議新增的職缺關鍵字", text)
        self.assertIn("類別:理貨", text)

    def test_no_candidates_sections_omitted(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 1.0}
        text = dr.build_report_text(health, [], [], "日")
        self.assertNotIn("FAQ 候選清單", text)
        self.assertNotIn("建議新增的職缺關鍵字", text)


class IsWeeklyReportDayTests(unittest.TestCase):
    def test_matches_configured_weekday(self):
        monday = datetime(2026, 9, 7)  # 2026-09-07 是星期一 (weekday()==0)
        self.assertEqual(monday.weekday(), 0)
        self.assertTrue(dr.is_weekly_report_day(monday))

    def test_other_weekday_is_false(self):
        tuesday = datetime(2026, 9, 8)
        self.assertFalse(dr.is_weekly_report_day(tuesday))


if __name__ == "__main__":
    unittest.main()
