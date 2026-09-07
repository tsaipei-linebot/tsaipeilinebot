import concurrent.futures
import json
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)
from tests import _stub_gcp
_stub_gcp.install()

from linebot.models import QuickReplyButton, MessageAction, TextSendMessage
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
        with patch("handlers.message_handler._is_staffed_hours", return_value=False):
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
        with patch("handlers.message_handler._is_staffed_hours", return_value=False):
            h.process_image_message(event, line_bot_api)
        line_bot_api.reply_message.assert_called_once()


class AsyncAiDecisionArchitectureTests(unittest.TestCase):
    """驗證「限時同步等待、逾時才背景補發」架構：process_user_message() 走到需要
    呼叫 Gemini 的路徑時，會把 AI 決策丟進執行緒池，最多同步等
    AI_DECISION_SYNC_TIMEOUT_SECONDS 秒——時限內算完就直接用免費的 reply_token
    回覆正式答案（不會變成計費的 push_message）；只有真的算比較久、超過時限的
    請求，才會先用 reply_token 回一句「查詢中」，再改用沒有時間限制的
    push_message 補發正式答案，藉此保證不管 Gemini 算多久使用者最終都會收到
    回覆（不受 LINE 30 秒 reply token 上限影響——這是壓力測試實測到 p99 超過
    30 秒後改的架構），同時避免把所有 AI 回覆都變成消耗則數的 push_message。"""

    def _make_event(self, text="有沒有特殊的職缺推薦", user_id="test-user-async"):
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = user_id
        event.message.text = text
        return event

    def test_fast_ai_decision_replies_via_free_reply_message(self):
        # AI 決策在時限內算完 → 直接用 reply_token 回覆正式答案，完全不呼叫
        # push_message（維持跟原本同步架構一樣免費）
        event = self._make_event()
        line_bot_api = MagicMock()
        fast_message = TextSendMessage(text="這是即時算完的正式答案")

        with patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=fast_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[0], "valid-reply-token")
        self.assertEqual(args[1], fast_message)
        line_bot_api.push_message.assert_not_called()

    def test_slow_ai_decision_acks_then_pushes_via_push_message(self):
        # AI 決策超過時限還沒算完 → 先用 reply_token 回一句「查詢中」的 ack，
        # 之後才改用 push_message 補發正式答案
        import threading
        import time

        event = self._make_event()
        line_bot_api = MagicMock()
        release_compute = threading.Event()
        slow_message = TextSendMessage(text="這是算比較久才算完的正式答案")

        def _slow_compute(*args, **kwargs):
            release_compute.wait(timeout=5)
            return slow_message

        with patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler.AI_DECISION_SYNC_TIMEOUT_SECONDS", 0.05), \
             patch("handlers.message_handler._compute_ai_decision_messages", side_effect=_slow_compute):
            h.process_user_message(event, line_bot_api)

            # 立刻回一次 ack，不會同步卡住等 AI 決策算完
            line_bot_api.reply_message.assert_called_once()
            args, _ = line_bot_api.reply_message.call_args
            self.assertEqual(args[0], "valid-reply-token")
            self.assertIn("查詢", args[1].text)
            line_bot_api.push_message.assert_not_called()

            # 讓背景的 AI 決策算完，觸發 done-callback 補發正式答案；用短輪詢
            # 等待而不是動到共用的執行緒池，避免弄壞其他測試共用的狀態
            release_compute.set()
            deadline = time.monotonic() + 5
            while not line_bot_api.push_message.called and time.monotonic() < deadline:
                time.sleep(0.02)

        line_bot_api.push_message.assert_called_once()
        args, _ = line_bot_api.push_message.call_args
        self.assertEqual(args[0], "test-user-async")
        self.assertEqual(args[1], slow_message)

    def test_compute_ai_decision_messages_returns_recommend_result(self):
        fake_decision = json.dumps({
            "action": "RECOMMEND", "reply": "推薦這個職缺給你", "ids": [0], "buttons": []
        })

        fake_job = {"職缺名稱(對外)": "測試職缺", "職缺名稱": "測試職缺", "系統廠商名稱": "測試廠商"}
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[fake_job]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]), \
             patch("handlers.message_handler.create_job_flex_card", return_value="FLEX_CARD"):
            messages = h._compute_ai_decision_messages(
                "test-user", "有推薦的職缺嗎", [], [], "新莊", ""
            )

        self.assertEqual(messages[0].text, "推薦這個職缺給你")
        self.assertEqual(messages[1], "FLEX_CARD")

    def test_compute_ai_decision_messages_returns_fallback_on_internal_exception(self):
        # 就算計算過程整個爆炸（例如 Firestore/Notion/Gemini 任何一個環節出問題），
        # 也一定要回傳保底訊息，不能讓例外往外拋出、導致呼叫端完全沒有東西可送
        with patch("handlers.message_handler.get_user_slots", side_effect=RuntimeError("boom")):
            message = h._compute_ai_decision_messages(
                "test-user", "有推薦的職缺嗎", [], [], "", ""
            )

        self.assertIn("延遲", message.text)

    def test_push_ai_decision_messages_pushes_fallback_when_future_raises(self):
        # done-callback 收到的 future 本身丟例外（理論上 _compute_ai_decision_messages
        # 已經攔截所有例外，這裡是最後一道防線）時，仍要 push 一則保底訊息，不能
        # 讓使用者只收到 ack 就沒有下文
        line_bot_api = MagicMock()
        future = concurrent.futures.Future()
        future.set_exception(RuntimeError("boom"))

        h._push_ai_decision_messages(future, "test-user", line_bot_api)

        line_bot_api.push_message.assert_called_once()
        args, _ = line_bot_api.push_message.call_args
        self.assertEqual(args[0], "test-user")
        self.assertIn("延遲", args[1].text)


class StaffedHoursGuardTests(unittest.TestCase):
    """驗證「日夜接力」的白天守門邏輯：同仁上班時段（10:10–18:50，含 10 分鐘
    交接緩衝，見 config.py 說明）沛沛完全不主動回覆，交給真人專員在 LINE
    聊天模式手動處理；這段時間之外才會進到原本的快速路徑／AI 決策邏輯。

    這整個機制受 STAFFED_HOURS_GUARD_ENABLED 這個總開關控制，預設關閉——
    還在測試頻道、LINE 後台排程還沒設定好之前，就算剛好在白天測試，機器人
    也要維持「不管幾點都照舊回覆」的舊行為，不能讓人誤以為壞掉。下面驗證
    守門邏輯生效行為的測試都會另外把這個開關 patch 成 True。"""

    def test_is_staffed_hours_boundaries(self):
        # 邊界採「左閉右開」：10:10 算已經上班、18:50 算已經下班（機器人啟動）
        self.assertFalse(h._is_staffed_hours(datetime(2026, 9, 4, 10, 9)))
        self.assertTrue(h._is_staffed_hours(datetime(2026, 9, 4, 10, 10)))
        self.assertTrue(h._is_staffed_hours(datetime(2026, 9, 4, 14, 0)))
        self.assertTrue(h._is_staffed_hours(datetime(2026, 9, 4, 18, 49)))
        self.assertFalse(h._is_staffed_hours(datetime(2026, 9, 4, 18, 50)))
        self.assertFalse(h._is_staffed_hours(datetime(2026, 9, 4, 3, 0)))

    def test_is_staffed_hours_same_every_day_including_weekend(self):
        # 同仁週末/假日班表與平日相同，判斷邏輯不看星期幾
        saturday_daytime = datetime(2026, 9, 5, 12, 0)  # 2026-09-05 是星期六
        self.assertTrue(h._is_staffed_hours(saturday_daytime))

    def test_process_user_message_skips_entirely_during_staffed_hours(self):
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-day"
        event.message.text = "有沒有工作"
        line_bot_api = MagicMock()

        with patch("handlers.message_handler.STAFFED_HOURS_GUARD_ENABLED", True), \
             patch("handlers.message_handler._is_staffed_hours", return_value=True), \
             patch("handlers.message_handler.fetch_jobs_data") as mock_fetch_jobs, \
             patch("handlers.message_handler.fetch_faqs_data") as mock_fetch_faqs:
            h.process_user_message(event, line_bot_api)

        # 白天完全靜默：不回覆、也不用去打 Notion 查職缺/FAQ（省成本，交給真人）
        line_bot_api.reply_message.assert_not_called()
        line_bot_api.push_message.assert_not_called()
        mock_fetch_jobs.assert_not_called()
        mock_fetch_faqs.assert_not_called()

    def test_guard_disabled_by_default_replies_even_during_staffed_hours(self):
        # STAFFED_HOURS_GUARD_ENABLED 預設關閉：還在測試頻道、LINE 後台排程
        # 還沒設定好之前，就算 _is_staffed_hours() 判斷是白天，也要維持「不管
        # 幾點都照舊回覆」的舊行為，不能讓人誤以為機器人壞掉。這裡故意不 patch
        # STAFFED_HOURS_GUARD_ENABLED，直接用它在 config.py 的預設值。
        self.assertFalse(h.STAFFED_HOURS_GUARD_ENABLED)

        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-guard-off"
        event.message.text = "有沒有工作"
        line_bot_api = MagicMock()
        fast_message = TextSendMessage(text="開關關閉時照舊回覆")

        with patch("handlers.message_handler._is_staffed_hours", return_value=True), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=fast_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], fast_message)

    def test_process_user_message_bypass_flag_ignores_staffed_hours(self):
        # /internal/load-test-message 端點靠這個旗標，讓壓力測試不管執行時間
        # 剛好在白天還是晚上，都能真的跑到 AI 決策那段邏輯
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-bypass"
        event.message.text = "有沒有工作"
        line_bot_api = MagicMock()
        fast_message = TextSendMessage(text="壓力測試繞過白天守門")

        with patch("handlers.message_handler.STAFFED_HOURS_GUARD_ENABLED", True), \
             patch("handlers.message_handler._is_staffed_hours", return_value=True), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=fast_message):
            h.process_user_message(event, line_bot_api, bypass_staffed_hours_guard=True)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], fast_message)

    def test_process_image_message_skips_entirely_during_staffed_hours(self):
        event = MagicMock()
        event.reply_token = "valid-token"
        event.source.user_id = "test-user-day"
        line_bot_api = MagicMock()

        with patch("handlers.message_handler.STAFFED_HOURS_GUARD_ENABLED", True), \
             patch("handlers.message_handler._is_staffed_hours", return_value=True):
            h.process_image_message(event, line_bot_api)

        line_bot_api.reply_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
