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


if __name__ == "__main__":
    unittest.main()
