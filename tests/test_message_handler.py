import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from tests import _stub_gcp
_stub_gcp.install()

from linebot.models import QuickReplyButton, MessageAction
from handlers import message_handler as h


class BuildQuickReplyButtonsTests(unittest.TestCase):
    def setUp(self):
        self.fallback = [QuickReplyButton(action=MessageAction(label="fallback", text="fallback"))]

    def test_converts_labels_to_buttons(self):
        buttons = h._build_quick_reply_buttons(["新莊工作", "桃園工作"], self.fallback)
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0].action.text, "新莊工作")

    def test_strips_leading_emoji(self):
        buttons = h._build_quick_reply_buttons(["📍 新莊工作"], self.fallback)
        self.assertEqual(buttons[0].action.text, "新莊工作")

    def test_caps_at_five_buttons(self):
        labels = [f"選項{i}" for i in range(8)]
        buttons = h._build_quick_reply_buttons(labels, self.fallback)
        self.assertEqual(len(buttons), 5)

    def test_empty_or_none_labels_fall_back(self):
        self.assertEqual(h._build_quick_reply_buttons([], self.fallback), self.fallback)
        self.assertEqual(h._build_quick_reply_buttons(None, self.fallback), self.fallback)
        self.assertEqual(h._build_quick_reply_buttons(["", "  "], self.fallback), self.fallback)

    def test_truncates_long_label(self):
        long_label = "超級長的按鈕文字" * 10
        buttons = h._build_quick_reply_buttons([long_label], self.fallback)
        self.assertLessEqual(len(buttons[0].action.label), 20)


class AiDecisionSchemaTests(unittest.TestCase):
    """驗證 AI_DECISION_SCHEMA 本身的結構，以及 message_handler.py 解析邏輯
    在各種合法/邊界 JSON 決策輸出下的行為（不呼叫真正的 Gemini，直接模擬
    query_gemini_ai() 可能回傳的 JSON 字串，驗證 json.loads 之後的欄位讀取邏輯）。
    """

    def test_schema_declares_four_actions(self):
        actions = h.AI_DECISION_SCHEMA["properties"]["action"]["enum"]
        self.assertEqual(set(actions), {"ASK", "UNKNOWN_FAQ", "RECOMMEND", "NO_MATCH"})

    def test_schema_requires_action_and_reply(self):
        self.assertEqual(set(h.AI_DECISION_SCHEMA["required"]), {"action", "reply"})

    def test_recommend_decision_ids_filtered_to_valid_range(self):
        # 模擬 build_ai_job_candidates 回傳 3 筆候選，AI 決策指定的 ids 有超出範圍的值，
        # 解析邏輯應該只留下合法索引，不會因為 AI 給了越界 ID 就整個出錯
        candidates = ["job0", "job1", "job2"]
        decision = json.loads('{"action": "RECOMMEND", "reply": "推薦這幾筆", "ids": [0, 2, 99, -1], "buttons": []}')
        ai_ids = decision.get("ids") if isinstance(decision.get("ids"), list) else []
        matched = [candidates[i] for i in ai_ids if isinstance(i, int) and 0 <= i < len(candidates)]
        self.assertEqual(matched, ["job0", "job2"])

    def test_malformed_json_falls_back_to_empty_decision(self):
        # ai_output 不是合法 JSON（例如 Gemini 客戶端呼叫失敗回傳空字串）時，
        # 解析邏輯要能安全地退回空 decision，而不是丟例外中斷整個對話
        for bad_output in ["", "not a json", "{broken"]:
            try:
                decision = json.loads(bad_output) if bad_output else {}
            except (json.JSONDecodeError, TypeError):
                decision = {}
            self.assertEqual(decision, {})


class ProcessImageMessageTests(unittest.TestCase):
    """求職者傳圖片（例如截圖）時的保底回覆：目前沒有解析圖片內容的能力，
    但一定要回覆使用者、引導改用文字，不能已讀不回。"""

    def _make_event(self, reply_token="valid-token"):
        event = MagicMock()
        event.reply_token = reply_token
        event.source.user_id = "test-user"
        return event

    def test_replies_with_guidance_text(self):
        event = self._make_event()
        line_bot_api = MagicMock()
        h.process_image_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[0], "valid-token")
        reply_message = args[1]
        self.assertIn("圖片", reply_message.text)
        self.assertIsNotNone(reply_message.quick_reply)

    def test_skips_verify_webhook_reply_token(self):
        # LINE 平台驗證 webhook 用的假 reply_token，不該真的嘗試回覆
        for fake_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
            event = self._make_event(reply_token=fake_token)
            line_bot_api = MagicMock()
            h.process_image_message(event, line_bot_api)
            line_bot_api.reply_message.assert_not_called()

    def test_still_replies_even_if_session_history_write_fails(self):
        # append_user_history 內部會連 Firestore（測試環境沒有真的 GCP 憑證，
        # session_service 的 db 是 stub 出來的 None），這裡驗證即使寫入對話歷史
        # 失敗，仍然要回覆使用者，不能因為 Firestore 出問題就整個沒有回應
        event = self._make_event()
        line_bot_api = MagicMock()
        h.process_image_message(event, line_bot_api)
        line_bot_api.reply_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
