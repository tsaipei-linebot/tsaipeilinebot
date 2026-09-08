import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from tests import _stub_gcp
_stub_gcp.install()

from services import session_service as s


class NormalizeSessionTests(unittest.TestCase):
    def test_missing_session_returns_default_and_not_fresh(self):
        session, was_fresh = s._normalize_session(None, now=1000.0)
        self.assertEqual(session["slots"], s.DEFAULT_SLOTS)
        self.assertEqual(session["messages"], [])
        self.assertFalse(was_fresh)

    def test_fresh_session_passes_through_unchanged(self):
        raw = {"last_time": 1000.0, "messages": [{"role": "求職者", "text": "hi"}], "slots": {"location": "新莊", "category": "", "shift": "", "leave": "", "brand": ""}}
        session, was_fresh = s._normalize_session(raw, now=1000.0 + s.SESSION_TTL - 1)
        self.assertEqual(session["slots"]["location"], "新莊")
        self.assertEqual(session["messages"], [{"role": "求職者", "text": "hi"}])
        self.assertTrue(was_fresh)

    def test_expired_session_clears_history_but_keeps_location(self):
        raw = {"last_time": 1000.0, "messages": [{"role": "求職者", "text": "hi"}], "slots": {"location": "桃園", "category": "外送", "shift": "早班", "leave": "", "brand": "momo"}}
        session, was_fresh = s._normalize_session(raw, now=1000.0 + s.SESSION_TTL + 1)
        self.assertFalse(was_fresh)
        self.assertEqual(session["messages"], [])
        self.assertEqual(session["slots"]["location"], "桃園")
        self.assertEqual(session["slots"]["category"], "")
        self.assertEqual(session["slots"]["brand"], "")

    def test_missing_slots_or_messages_field_backfilled(self):
        # 模擬舊格式資料（欄位還沒補齊）也能正常運作
        raw = {"last_time": time.time()}
        session, was_fresh = s._normalize_session(raw, now=raw["last_time"])
        self.assertEqual(session["slots"], s.DEFAULT_SLOTS)
        self.assertEqual(session["messages"], [])
        self.assertTrue(was_fresh)


class MergeSlotUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.slots = {"location": "新莊", "category": "外送", "shift": "", "leave": "", "brand": ""}

    def test_empty_value_keeps_existing(self):
        merged = s._merge_slot_updates(self.slots, location="")
        self.assertEqual(merged["location"], "新莊")

    def test_new_value_overwrites(self):
        merged = s._merge_slot_updates(self.slots, location="桃園")
        self.assertEqual(merged["location"], "桃園")

    def test_clear_slot_empties_dimension(self):
        merged = s._merge_slot_updates(self.slots, category=s.CLEAR_SLOT)
        self.assertEqual(merged["category"], "")

    def test_does_not_mutate_input_dict(self):
        original = dict(self.slots)
        s._merge_slot_updates(self.slots, location="桃園")
        self.assertEqual(self.slots, original)

    def test_unrelated_dimensions_untouched(self):
        merged = s._merge_slot_updates(self.slots, shift="早班")
        self.assertEqual(merged["location"], "新莊")
        self.assertEqual(merged["category"], "外送")
        self.assertEqual(merged["shift"], "早班")


class AppendHistoryEntryTests(unittest.TestCase):
    def test_appends_new_entry(self):
        result = s._append_history_entry([], "求職者", "五股有工作嗎")
        self.assertEqual(result, [{"role": "求職者", "text": "五股有工作嗎"}])

    def test_does_not_mutate_input_list(self):
        original = [{"role": "求職者", "text": "a"}]
        s._append_history_entry(original, "招募顧問沛沛", "b")
        self.assertEqual(len(original), 1)

    def test_trims_to_max_len_keeping_most_recent(self):
        messages = [{"role": "求職者", "text": str(i)} for i in range(10)]
        result = s._append_history_entry(messages, "求職者", "10", max_len=10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0]["text"], "1")
        self.assertEqual(result[-1]["text"], "10")


if __name__ == "__main__":
    unittest.main()
