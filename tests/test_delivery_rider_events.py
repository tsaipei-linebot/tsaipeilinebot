import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from delivery import rider_events, rider_repository


class NotBoundOrBlockedTests(unittest.TestCase):
    """未綁定/已停用的騎士，不管傳什麼事件都只回一句擋下訊息，不會走到任何
    業務邏輯（不會查資料庫的門市/時段）。"""

    def test_unbound_user_gets_not_bound_message(self):
        with mock.patch.object(rider_repository, "get_rider_binding", return_value=None):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        self.assertEqual(len(messages), 1)
        self.assertIn("還沒有完成綁定", messages[0]["text"])

    def test_blocked_user_gets_blocked_message(self):
        binding = {"user_id": "U1", "status": "blocked", "name": "小明"}
        with mock.patch.object(rider_repository, "get_rider_binding", return_value=binding):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        self.assertEqual(len(messages), 1)
        self.assertIn("無法使用", messages[0]["text"])

    def test_missing_user_id_returns_no_messages(self):
        self.assertEqual(rider_events.handle_rider_event({"type": "message"}), [])


class _ActiveBindingMixin:
    def setUp(self):
        self.binding = {"user_id": "U1", "status": "active", "name": "小明"}
        patcher = mock.patch.object(rider_repository, "get_rider_binding", return_value=self.binding)
        self.addCleanup(patcher.stop)
        patcher.start()


class TextKeywordDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    def test_nearby_order_keyword_prompts_location_share(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        self.assertEqual(len(messages), 1)
        self.assertIn("位置資訊", messages[0]["text"])

    def test_shift_list_keyword_with_no_open_shifts(self):
        with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=[]):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "瀏覽報班"})
        self.assertIn("沒有開放中的報班時段", messages[0]["text"])

    def test_shift_list_keyword_with_open_shifts_returns_carousel(self):
        shifts = [{"id": "s1", "location": "中和門市", "capacity": 3, "registered_count": 1, "start_time": 0, "end_time": 0}]
        with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=shifts):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "瀏覽報班"})
        self.assertEqual(messages[0]["type"], "flex")

    def test_unrecognized_text_returns_no_messages(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "今天天氣真好"})
        self.assertEqual(messages, [])


class QuantityInputDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    def test_digit_text_without_pending_claim_gets_expired_message(self):
        with mock.patch.object(rider_repository, "pop_pending_claim", return_value=""):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "5"})
        self.assertIn("逾時失效", messages[0]["text"])

    def test_digit_text_with_pending_claim_calls_claim_store_delivery(self):
        with mock.patch.object(rider_repository, "pop_pending_claim", return_value="store1"):
            with mock.patch.object(rider_repository, "claim_store_delivery", return_value=(True, "承接成功！")) as mock_claim:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "5"})
        mock_claim.assert_called_once_with("store1", "U1", "小明", 5)
        self.assertIn("承接成功", messages[0]["text"])

    def test_zero_quantity_re_prompts_without_clearing_pending_claim(self):
        with mock.patch.object(rider_repository, "pop_pending_claim", return_value="store1"):
            with mock.patch.object(rider_repository, "set_pending_claim") as mock_set:
                with mock.patch.object(rider_repository, "claim_store_delivery") as mock_claim:
                    messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "0"})
        mock_claim.assert_not_called()
        mock_set.assert_called_once_with("U1", "store1")
        self.assertIn("大於 0", messages[0]["text"])


class LocationDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    def test_location_message_with_no_nearby_stores(self):
        with mock.patch.object(rider_repository, "list_nearby_open_stores", return_value=[]):
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
            )
        self.assertIn("沒有開放中", messages[0]["text"])

    def test_location_message_with_nearby_stores_returns_carousel(self):
        stores = [{"id": "s1", "store_name": "中和門市", "remaining_quantity": 5, "distance_km": 1.2}]
        with mock.patch.object(rider_repository, "list_nearby_open_stores", return_value=stores):
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
            )
        self.assertEqual(messages[0]["type"], "flex")

    def test_location_missing_coordinates_returns_no_messages(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "location"})
        self.assertEqual(messages, [])


class PostbackDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    def test_claim_store_postback_sets_pending_claim_and_prompts_quantity(self):
        store = {"id": "store1", "store_name": "中和門市", "status": "open", "remaining_quantity": 5}
        with mock.patch.object(rider_repository, "get_store_delivery", return_value=store):
            with mock.patch.object(rider_repository, "set_pending_claim") as mock_set:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "postback", "postback_data": "action=CLAIM_STORE&storeId=store1"}
                )
        mock_set.assert_called_once_with("U1", "store1")
        self.assertIn("剩餘可承接量", messages[0]["text"])

    def test_claim_store_postback_for_closed_store(self):
        with mock.patch.object(rider_repository, "get_store_delivery", return_value=None):
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "postback", "postback_data": "action=CLAIM_STORE&storeId=gone"}
            )
        self.assertIn("已經不存在或已關閉", messages[0]["text"])

    def test_register_shift_postback_calls_register_shift(self):
        with mock.patch.object(rider_repository, "register_shift", return_value=(True, "報名成功！")) as mock_register:
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "postback", "postback_data": "action=REGISTER_SHIFT&shiftId=shift1"}
            )
        mock_register.assert_called_once_with("shift1", "U1", "小明")
        self.assertIn("報名成功", messages[0]["text"])

    def test_shift_list_postback_returns_carousel_or_empty_message(self):
        with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=[]):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "postback", "postback_data": "action=SHIFT_LIST"})
        self.assertIn("沒有開放中的報班時段", messages[0]["text"])

    def test_unrecognized_postback_action_returns_no_messages(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "postback", "postback_data": "action=UNKNOWN"})
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
