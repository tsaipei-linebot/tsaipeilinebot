import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from delivery import rider_messages


class PromptShareLocationMessageTests(unittest.TestCase):
    """2026-09-19：使用者反映要騎士自己點「+」→「位置資訊」不方便，改成
    附上 LINE Quick Reply 的「位置」按鈕，點一下直接跳出位置選擇畫面。"""

    def test_is_a_text_message(self):
        message = rider_messages.prompt_share_location_message()
        self.assertEqual(message["type"], "text")
        self.assertTrue(message["text"])

    def test_has_location_quick_reply_button(self):
        message = rider_messages.prompt_share_location_message()
        items = message["quickReply"]["items"]
        self.assertEqual(len(items), 1)
        action = items[0]["action"]
        self.assertEqual(action["type"], "location")
        self.assertTrue(action["label"])


class PromptShareLocationForShiftMessageTests(unittest.TestCase):
    """2026-09-21 新增：報班媒合加入服務半徑篩選後，也要先請騎士分享
    位置，跟即時接單版本是同一種 Quick Reply 位置按鈕機制。"""

    def test_is_a_text_message(self):
        message = rider_messages.prompt_share_location_for_shift_message()
        self.assertEqual(message["type"], "text")
        self.assertTrue(message["text"])

    def test_has_location_quick_reply_button(self):
        message = rider_messages.prompt_share_location_for_shift_message()
        items = message["quickReply"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"]["type"], "location")


class ShiftsCarouselDistanceTests(unittest.TestCase):
    """2026-09-21 新增：shifts_carousel() 有 distance_km 時要顯示距離，
    沒有（例如 Postback 觸發、後台管理用的呼叫）就不顯示，不能讓畫面
    印出奇怪的空白或錯誤文字。"""

    def _shift(self, distance_km=None):
        return {
            "id": "s1", "location": "台北車站", "capacity": 3, "registered_count": 1,
            "start_time": 0, "end_time": 0, "distance_km": distance_km,
        }

    def test_shows_distance_when_present(self):
        message = rider_messages.shifts_carousel([self._shift(distance_km=1.2)])
        body_text = message["contents"]["contents"][0]["body"]["contents"][1]["text"]
        self.assertIn("1.2 公里", body_text)

    def test_omits_distance_when_absent(self):
        shift = self._shift()
        del shift["distance_km"]
        message = rider_messages.shifts_carousel([shift])
        body_text = message["contents"]["contents"][0]["body"]["contents"][1]["text"]
        self.assertNotIn("公里", body_text)


if __name__ == "__main__":
    unittest.main()
