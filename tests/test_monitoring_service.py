import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)

from services import monitoring_service as mon


def _capture_printed_line(**kwargs) -> str:
    with patch("builtins.print") as mock_print:
        mon.log_ai_decision_event(**kwargs)
    return mock_print.call_args[0][0]


class LogAiDecisionEventTests(unittest.TestCase):
    def test_line_starts_with_marker(self):
        line = _capture_printed_line(path="ai_decision", action="ASK")
        self.assertTrue(line.startswith(mon.AI_DECISION_LOG_MARKER))


class ParseLogLineRoundTripTests(unittest.TestCase):
    def test_round_trip_recovers_original_event_fields(self):
        line = _capture_printed_line(
            path="ai_decision", action="RECOMMEND", fallback_triggered=False,
            latency_seconds=3.14159, matched_category="製造/作業員", matched_brand="美光",
        )

        event = mon.parse_log_line(line)
        self.assertEqual(event["path"], "ai_decision")
        self.assertEqual(event["action"], "RECOMMEND")
        self.assertEqual(event["matched_category"], "製造/作業員")
        self.assertEqual(event["matched_brand"], "美光")
        self.assertAlmostEqual(event["latency_seconds"], 3.142, places=3)

    def test_fallback_triggered_round_trips_as_true(self):
        line = _capture_printed_line(path="ai_decision", fallback_triggered=True)
        self.assertTrue(mon.parse_log_line(line)["fallback_triggered"])

    def test_ai_decision_empty_round_trips_as_true(self):
        line = _capture_printed_line(path="ai_decision", ai_decision_empty=True)
        self.assertTrue(mon.parse_log_line(line)["ai_decision_empty"])

    def test_ai_decision_empty_defaults_to_false(self):
        line = _capture_printed_line(path="ai_decision")
        self.assertFalse(mon.parse_log_line(line)["ai_decision_empty"])

    def test_non_marker_line_returns_none(self):
        self.assertIsNone(mon.parse_log_line("普通的一行 log，跟結構化格式無關"))

    def test_malformed_json_after_marker_returns_none(self):
        self.assertIsNone(mon.parse_log_line(f"{mon.AI_DECISION_LOG_MARKER}not-json"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(mon.parse_log_line(""))
        self.assertIsNone(mon.parse_log_line(None))


if __name__ == "__main__":
    unittest.main()
