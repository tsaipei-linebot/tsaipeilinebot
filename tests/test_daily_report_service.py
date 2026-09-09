import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from tests import _stub_gcp
_stub_gcp.install()

from services import daily_report_service as dr


def _event(path="ai_decision", offset_minutes=0, latency=1.0, fallback=False, ai_empty=False, category="", brand="", delivery_mode=""):
    ts = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)
    return {
        "ts": ts.isoformat(),
        "path": path,
        "latency_seconds": latency,
        "fallback_triggered": fallback,
        "ai_decision_empty": ai_empty,
        "matched_category": category,
        "matched_brand": brand,
        "delivery_mode": delivery_mode,
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

    def test_single_ai_decision_empty_triggers_level1(self):
        # Gemini「優雅降級」回傳空字串時不會丟例外、fallback_triggered 會是 False，
        # 但這仍然代表使用者沒拿到真正的判斷結果，一樣要算進第一層異常門檻
        events = [_event(ai_empty=True)]
        health = dr.compute_health_summary(events)
        self.assertTrue(health["level1_triggered"])
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(health["ai_empty_count"], 1)

    def test_ai_decision_empty_on_non_ai_path_is_ignored(self):
        # direct_intercept／high_confidence_faq 這兩條路徑根本不會呼叫 Gemini，
        # ai_decision_empty 欄位在這兩種事件上沒有意義，不該被誤算
        events = [_event(path="direct_intercept", ai_empty=True)]
        health = dr.compute_health_summary(events)
        self.assertFalse(health["level1_triggered"])
        self.assertEqual(health["ai_empty_count"], 0)

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


class ComputeDeliveryModeSummaryTests(unittest.TestCase):
    def test_counts_sync_and_push_separately(self):
        events = (
            [_event(delivery_mode="sync") for _ in range(7)]
            + [_event(delivery_mode="push") for _ in range(3)]
        )
        summary = dr.compute_delivery_mode_summary(events)
        self.assertEqual(summary, {"sync_count": 7, "push_count": 3, "push_ratio_percent": 30.0})

    def test_no_ai_decision_events_returns_zero_ratio(self):
        summary = dr.compute_delivery_mode_summary([])
        self.assertEqual(summary, {"sync_count": 0, "push_count": 0, "push_ratio_percent": 0.0})

    def test_non_ai_decision_path_events_are_ignored(self):
        events = [_event(path="direct_intercept", delivery_mode="sync") for _ in range(5)]
        summary = dr.compute_delivery_mode_summary(events)
        self.assertEqual(summary["sync_count"], 0)


class BuildReportTextTests(unittest.TestCase):
    def test_healthy_report_has_check_marks_no_warning(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 3.2}
        text = dr.build_report_text(health, [], [], "日")
        self.assertIn("✅", text)
        self.assertNotIn("⚠️", text)

    def test_delivery_mode_section_shown_only_when_provided_and_non_empty(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 1.0}
        delivery_mode = {"sync_count": 60, "push_count": 40, "push_ratio_percent": 40.0}
        text = dr.build_report_text(health, [], [], "週", delivery_mode)
        self.assertIn("同步回覆／背景補發比例", text)
        self.assertIn("40.0%", text)

    def test_delivery_mode_section_omitted_when_none(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 1.0}
        text = dr.build_report_text(health, [], [], "日", None)
        self.assertNotIn("同步回覆／背景補發比例", text)

    def test_delivery_mode_section_omitted_when_no_events(self):
        health = {"level1_triggered": False, "fallback_count": 0, "level2_triggered": False, "worst_bucket_p95_seconds": 1.0}
        empty_delivery_mode = {"sync_count": 0, "push_count": 0, "push_ratio_percent": 0.0}
        text = dr.build_report_text(health, [], [], "週", empty_delivery_mode)
        self.assertNotIn("同步回覆／背景補發比例", text)

    def test_unhealthy_report_includes_warnings(self):
        health = {"level1_triggered": True, "fallback_count": 2, "level2_triggered": True, "worst_bucket_p95_seconds": 15.0}
        text = dr.build_report_text(health, [], [], "日")
        self.assertIn("⚠️", text)
        self.assertIn("2 次保底訊息", text)

    def test_ai_decision_empty_warning_shown_separately_from_fallback(self):
        health = {
            "level1_triggered": True, "fallback_count": 0, "ai_empty_count": 3,
            "level2_triggered": False, "worst_bucket_p95_seconds": 1.0,
        }
        text = dr.build_report_text(health, [], [], "日")
        self.assertIn("3 次 AI 決策安靜失敗", text)
        self.assertNotIn("次保底訊息", text)

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


class RunDailyReportTests(unittest.TestCase):
    """config.py 明確寫著第二層門檻在週報當天要用「過去 7*24 小時」判斷，不是
    只看 24 小時——這裡驗證 run_daily_report() 真的照這個規則走，且週報當天
    只查一次 log（拿同一批資料同時算健康狀況跟 FAQ 關鍵字缺口），不會为了
    健康狀況跟關鍵字缺口分別各查一次 Cloud Logging。"""

    def test_weekly_day_uses_7day_window_for_health_and_only_fetches_once(self):
        with patch("services.daily_report_service.is_weekly_report_day", return_value=True), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]) as mock_fetch, \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=[]):
            dr.run_daily_report(line_bot_api=None)

        mock_fetch.assert_called_once_with(hours=24 * 7)

    def test_non_weekly_day_uses_24h_window(self):
        with patch("services.daily_report_service.is_weekly_report_day", return_value=False), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]) as mock_fetch:
            dr.run_daily_report(line_bot_api=None)

        mock_fetch.assert_called_once_with(hours=24)

    def test_weekly_report_health_reflects_events_from_7day_fetch(self):
        # 過去 24 小時（如果真的另外查）沒有任何 fallback，但過去 7 天（唯一真正
        # 會被拿去用的那批資料）裡有 2 次——健康狀況要反映後者，不是誤用前者。
        weekly_events_with_fallback = [_event(fallback=True), _event(fallback=True)]
        with patch("services.daily_report_service.is_weekly_report_day", return_value=True), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=weekly_events_with_fallback), \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=[]):
            summary = dr.run_daily_report(line_bot_api=None)

        self.assertEqual(summary["health"]["fallback_count"], 2)
        self.assertTrue(summary["health"]["level1_triggered"])


class FaqReportDailyModeOverrideTests(unittest.TestCase):
    """FAQ_REPORT_DAILY_MODE（上線初期使用，見 config.py）開啟時，FAQ 候選清單／
    建議關鍵字要不管星期幾都出現，但健康狀況檢查的時間窗口不能被連帶拉長——
    只有真正的「週報日」才用過去 7 天，其餘每天都還是過去 24 小時。"""

    def test_daily_mode_shows_faq_section_on_non_weekly_day(self):
        with patch("services.daily_report_service.is_weekly_report_day", return_value=False), \
             patch("services.daily_report_service.FAQ_REPORT_DAILY_MODE", True), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]), \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=["颱風天上班算加班嗎"]) as mock_faq:
            summary = dr.run_daily_report(line_bot_api=None)

        mock_faq.assert_called_once()
        self.assertEqual(summary["faq_candidate_count"], 1)

    def test_daily_mode_off_hides_faq_section_on_non_weekly_day(self):
        with patch("services.daily_report_service.is_weekly_report_day", return_value=False), \
             patch("services.daily_report_service.FAQ_REPORT_DAILY_MODE", False), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]), \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=["颱風天上班算加班嗎"]) as mock_faq:
            summary = dr.run_daily_report(line_bot_api=None)

        mock_faq.assert_not_called()
        self.assertEqual(summary["faq_candidate_count"], 0)

    def test_daily_mode_does_not_widen_health_window_on_non_weekly_day(self):
        with patch("services.daily_report_service.is_weekly_report_day", return_value=False), \
             patch("services.daily_report_service.FAQ_REPORT_DAILY_MODE", True), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]) as mock_fetch, \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=[]):
            dr.run_daily_report(line_bot_api=None)

        mock_fetch.assert_called_once_with(hours=24)

    def test_daily_mode_does_not_enable_delivery_mode_section_on_non_weekly_day(self):
        # FAQ_REPORT_DAILY_MODE 只影響 FAQ 候選清單／建議關鍵字，不影響
        # 同步回覆／背景補發比例——那個只在真正的週報日才顯示，範圍沒有擴大。
        with patch("services.daily_report_service.is_weekly_report_day", return_value=False), \
             patch("services.daily_report_service.FAQ_REPORT_DAILY_MODE", True), \
             patch("services.daily_report_service.fetch_recent_log_events", return_value=[]), \
             patch("services.daily_report_service.fetch_pending_faq_candidates", return_value=[]):
            summary = dr.run_daily_report(line_bot_api=None)

        self.assertIsNone(summary["delivery_mode"])


if __name__ == "__main__":
    unittest.main()
