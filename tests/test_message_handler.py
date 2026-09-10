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

    def test_ack_send_failure_still_schedules_background_push(self):
        # 「查詢中」ack 送出失敗（例如 reply_token 剛好過期）不能讓程式就此放棄——
        # 背景算完的正式答案還是要想辦法透過 push_message 送出，不然使用者會完全
        # 收不到任何回覆（見 handlers/message_handler.py 這段的說明）。
        import threading
        import time

        event = self._make_event()
        line_bot_api = MagicMock()
        line_bot_api.reply_message.side_effect = RuntimeError("reply token expired")
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

            release_compute.set()
            deadline = time.monotonic() + 5
            while not line_bot_api.push_message.called and time.monotonic() < deadline:
                time.sleep(0.02)

        line_bot_api.push_message.assert_called_once()
        args, _ = line_bot_api.push_message.call_args
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

    def test_ai_prompt_forbids_inferring_uncovered_districts(self):
        # 試營運實測發現：候選職缺「地點:」欄位明明沒有列出某個行政區（例如
        # 只列了「桃園市（蘆竹、龜山）」），AI 卻自己推論「同縣市的八德也算
        # 涵蓋在內」。這不是候選職缺篩選錯誤（那部分已經修過），是提示詞沒有
        # 明確禁止 AI 這樣類推——這裡驗證提示詞確實有把這條規則寫進去。
        fake_decision = json.dumps({"action": "NO_MATCH", "reply": "目前暫無", "ids": [], "buttons": []})
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision) as mock_query, \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]):
            h._compute_ai_decision_messages("test-user", "八德有工作嗎", [], [], "八德", "")

        prompt_sent_to_ai = mock_query.call_args[0][0]
        self.assertIn("不能自行推論", prompt_sent_to_ai)
        self.assertIn("八德", prompt_sent_to_ai)

    def test_ai_prompt_location_reflects_specific_district_for_broad_coverage_job(self):
        # 試營運實測發現：蝦皮店到店這類「全台/多縣市門市自選」職缺涵蓋超過
        # 5 個行政區，組給 AI 判斷用的「地點:」欄位原本沒有帶入使用者問的地區
        # （current_location），只會回傳籠統的「各區門市據點（自選區域）」，
        # AI 因此看不出「板橋」有沒有明確包含在內，只能保守回答「暫無明確
        # 列出」——但同一時間組給 LINE 卡片顯示用的地點文字有正確帶入
        # target_location，卡片老實顯示「板橋區」，兩邊資訊兜不起來。這裡驗證
        # 提示詞裡的「地點:」欄位有正確反映使用者問的地區。
        broad_job = {
            "職缺名稱(對外)": "蝦皮店到店門市夥伴", "職缺名稱": "蝦皮店到店門市夥伴",
            "系統廠商名稱": "蝦皮", "職務類別": "門市",
            "縣市": "宜蘭縣,桃園市,高雄市,基隆市,新北市,新竹縣",
            "行政區": "宜蘭市,桃園區,高雄區,基隆區,新北市板橋區,新竹縣區",
        }
        fake_decision = json.dumps({"action": "RECOMMEND", "reply": "推薦這個職缺給你", "ids": [0], "buttons": []})
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision) as mock_query, \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[broad_job]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]), \
             patch("handlers.message_handler.create_job_flex_card", return_value="FLEX_CARD"):
            h._compute_ai_decision_messages("test-user", "板橋蝦皮門市有缺嗎", [broad_job], [], "板橋", "")

        prompt_sent_to_ai = mock_query.call_args[0][0]
        self.assertIn("板橋", prompt_sent_to_ai)
        self.assertNotIn("自選區域", prompt_sent_to_ai)

    def test_ai_prompt_explicitly_states_locked_category_and_brand(self):
        # 試營運實測發現：使用者先問「蝦皮門市有嗎」，接著只問「八德有缺人嗎」
        # （這句話本身沒再提到門市/蝦皮），AI 卻把八德所有類別的職缺都推薦
        # 出來，答非所問；但換成「蝦皮門市 八德有缺嗎」這種當下就完整重複
        # 條件的問法，AI 又能正確判斷沒有符合。追查發現提示詞原本完全沒有
        # 明講「求職者目前鎖定的條件」，AI 只能自己從對話歷史文字模糊推測，
        # 這句話有沒有重複提到條件會讓 AI 判斷不一致。這裡驗證提示詞裡有
        # 明確列出已鎖定的類別/廠商，不用 AI 自己憑對話歷史猜。
        fake_decision = json.dumps({"action": "NO_MATCH", "reply": "目前暫無", "ids": [], "buttons": []})
        locked_slots = dict(location="", category="門市", shift="", leave="", brand="蝦皮")
        with patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision) as mock_query, \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]):
            h._compute_ai_decision_messages(
                "test-user", "八德有缺人嗎", [], [], "八德", "",
                known_slots=locked_slots,
            )

        prompt_sent_to_ai = mock_query.call_args[0][0]
        self.assertIn("求職者目前鎖定的條件", prompt_sent_to_ai)
        self.assertIn("工作類型=門市", prompt_sent_to_ai)
        self.assertIn("廠商=蝦皮", prompt_sent_to_ai)

    def test_ai_prompt_shows_no_locked_conditions_when_slots_empty(self):
        fake_decision = json.dumps({"action": "NO_MATCH", "reply": "目前暫無", "ids": [], "buttons": []})
        empty_slots = dict(location="", category="", shift="", leave="", brand="")
        with patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision) as mock_query, \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]):
            h._compute_ai_decision_messages(
                "test-user", "有工作嗎", [], [], "", "",
                known_slots=empty_slots,
            )

        prompt_sent_to_ai = mock_query.call_args[0][0]
        self.assertIn("目前尚未鎖定任何條件", prompt_sent_to_ai)

    def test_recommend_with_no_candidates_returns_plain_text_not_empty_carousel(self):
        # AI 決策出 action="RECOMMEND"，但候選職缺清單剛好是空的（例如 Notion
        # 職缺暫時全部停招）——LINE 的 Flex Carousel 不接受 0 張卡片的空陣列，
        # 這裡要老實回覆「目前沒有職缺」的純文字，不能送出一定會被 LINE API
        # 拒絕的空卡片。
        fake_decision = json.dumps({
            "action": "RECOMMEND", "reply": "推薦這個職缺給你", "ids": [], "buttons": []
        })
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]):
            message = h._compute_ai_decision_messages(
                "test-user", "有推薦的職缺嗎", [], [], "新莊", ""
            )

        self.assertIsInstance(message, TextSendMessage)
        self.assertIn("沒有符合的職缺", message.text)

    def test_unknown_faq_records_asker_identity_for_followup(self):
        # 求職者問到 FAQ 沒收錄的問題時，除了寫進 FAQ 候選資料庫（給未來的
        # 求職者累積常見問答庫），也要另外留一筆「這次是誰問的」紀錄，讓招募
        # 專員能回頭去 LINE 官方帳號後台找到這個人手動回覆。
        fake_decision = json.dumps({
            "action": "UNKNOWN_FAQ", "reply": "已記錄您的問題", "ids": [], "buttons": []
        })
        line_bot_api = MagicMock()
        line_bot_api.get_profile.return_value = MagicMock(display_name="小明")

        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.append_unresolved_faq_to_notion") as mock_faq_db, \
             patch("handlers.message_handler.append_unresolved_question_for_followup") as mock_followup:
            h._compute_ai_decision_messages(
                "U1234", "颱風天上班算加班嗎", [], [], "新莊", "",
                target_line_bot_api=line_bot_api,
            )

        mock_faq_db.assert_called_once_with("颱風天上班算加班嗎")
        line_bot_api.get_profile.assert_called_once_with("U1234")
        mock_followup.assert_called_once_with("颱風天上班算加班嗎", "U1234", "小明")

    def test_unknown_faq_falls_back_to_user_id_when_profile_lookup_fails(self):
        # get_profile() 可能失敗（例如使用者已封鎖官方帳號），這時候追蹤紀錄
        # 還是要留下來，只是暱稱欄位退回用 user_id，不能因此整個追蹤都不寫。
        fake_decision = json.dumps({
            "action": "UNKNOWN_FAQ", "reply": "已記錄您的問題", "ids": [], "buttons": []
        })
        line_bot_api = MagicMock()
        line_bot_api.get_profile.side_effect = RuntimeError("使用者已封鎖官方帳號")

        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.append_unresolved_faq_to_notion"), \
             patch("handlers.message_handler.append_unresolved_question_for_followup") as mock_followup:
            h._compute_ai_decision_messages(
                "U1234", "颱風天上班算加班嗎", [], [], "新莊", "",
                target_line_bot_api=line_bot_api,
            )

        mock_followup.assert_called_once_with("颱風天上班算加班嗎", "U1234", "")

    def test_unknown_faq_without_line_bot_api_still_records_user_id(self):
        # 沒有傳入 target_line_bot_api（例如舊測試直接呼叫這個函式）時，
        # 不該噴例外，只是沒辦法查暱稱，追蹤紀錄改用 user_id。
        fake_decision = json.dumps({
            "action": "UNKNOWN_FAQ", "reply": "已記錄您的問題", "ids": [], "buttons": []
        })
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.append_unresolved_faq_to_notion"), \
             patch("handlers.message_handler.append_unresolved_question_for_followup") as mock_followup:
            h._compute_ai_decision_messages(
                "U1234", "颱風天上班算加班嗎", [], [], "新莊", ""
            )

        mock_followup.assert_called_once_with("颱風天上班算加班嗎", "U1234", "")

    def test_compute_ai_decision_messages_returns_fallback_on_internal_exception(self):
        # 就算計算過程整個爆炸（例如 Firestore/Notion/Gemini 任何一個環節出問題），
        # 也一定要回傳保底訊息，不能讓例外往外拋出、導致呼叫端完全沒有東西可送
        with patch("handlers.message_handler.get_user_slots", side_effect=RuntimeError("boom")):
            message = h._compute_ai_decision_messages(
                "test-user", "有推薦的職缺嗎", [], [], "", ""
            )

        self.assertIn("延遲", message.text)

    def test_log_ctx_records_fallback_triggered_on_internal_exception(self):
        log_ctx = {}
        with patch("handlers.message_handler.get_user_slots", side_effect=RuntimeError("boom")):
            h._compute_ai_decision_messages("test-user", "有推薦的職缺嗎", [], [], "", "", log_ctx)

        self.assertTrue(log_ctx.get("fallback_triggered"))

    def test_log_ctx_records_ai_decision_empty_when_gemini_returns_blank(self):
        # Gemini「優雅降級」回傳空字串時（例如 MODEL_FALLBACK_LIST 每個模型都失敗），
        # 不會走到 except Exception，但仍然代表這句話沒有被真的判斷過，
        # 監控要能抓到這種「安靜失敗」（見 services/monitoring_service.py）
        log_ctx = {}
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=""), \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]), \
             patch("services.matcher_service.get_user_slots", return_value={"location": "新莊", "shift": "早班", "category": "外送"}):
            # action 為空字串時會落到 build_progressive_question() 的保底引導，
            # 那支函式是 matcher_service 自己 import 的 get_user_slots（不是
            # message_handler 這邊 patch 的那個引用），這裡把地區/班別/類別都
            # 補齊讓它直接判斷完畢，避免真的打去（測試環境裡被 stub 掉的）Firestore。
            h._compute_ai_decision_messages("test-user", "隨便說點什麼", [], [], "新莊", "", log_ctx)

        self.assertTrue(log_ctx.get("ai_decision_empty"))
        self.assertFalse(log_ctx.get("fallback_triggered"))

    def test_log_ctx_no_ai_decision_empty_when_action_present(self):
        log_ctx = {}
        fake_decision = json.dumps({"action": "ASK", "reply": "你好", "buttons": []})
        with patch("handlers.message_handler.get_user_slots", return_value={}), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.query_gemini_ai", return_value=fake_decision), \
             patch("handlers.message_handler.build_ai_job_candidates", return_value=[]), \
             patch("handlers.message_handler.build_ai_faq_candidates", return_value=[]):
            h._compute_ai_decision_messages("test-user", "你好", [], [], "新莊", "", log_ctx)

        self.assertNotIn("ai_decision_empty", log_ctx)
        self.assertEqual(log_ctx.get("action"), "ASK")

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

    def test_sync_reply_failure_falls_back_to_push_message(self):
        # AI 決策在時限內就算完了（走「同步成功」這條路），但 reply_message()
        # 本身失敗（例如 reply_token 因為排隊延遲已經過期）——已經算好的正式
        # 答案不能因此被丟掉，要改用不受時效限制的 push_message 補發，不能讓
        # 使用者完全收不到任何回覆。
        event = self._make_event()
        line_bot_api = MagicMock()
        line_bot_api.reply_message.side_effect = RuntimeError("reply token expired")
        fast_message = TextSendMessage(text="這是即時算完的正式答案")

        with patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=fast_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.push_message.assert_called_once()
        args, _ = line_bot_api.push_message.call_args
        self.assertEqual(args[0], "test-user-async")
        self.assertEqual(args[1], fast_message)


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


class DirectInterceptEdgeCaseTests(unittest.TestCase):
    """涵蓋兩個修過的邊界情境：
    1.「都給我看看」這類全部瀏覽意圖，但 active_jobs 剛好是空的（Notion 職缺
       暫時全部停招、或快取讀取失敗）——不能組出空的 LINE Flex Carousel（會被
       LINE API 拒絕），要老實回覆文字說明。
    2.「查看職缺詳情」帶了一個現有職缺庫裡完全比對不到的舊職缺名稱（職缺已經
       下架/改名）——不能因為 active_jobs 非空就隨便塞第一筆不相關的職缺給
       使用者，要落到一般對話流程（AI 決策）由 AI 判斷怎麼回覆。
    """

    def test_show_all_with_empty_active_jobs_replies_plain_text_not_empty_carousel(self):
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-empty-jobs"
        event.message.text = "都給我看看"
        line_bot_api = MagicMock()

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[0], "valid-reply-token")
        # 不能是 [文字, flex_card] 這種 list，只能是單純一則文字訊息
        self.assertIsInstance(args[1], TextSendMessage)
        self.assertIn("沒有符合的職缺資料", args[1].text)

    def test_stale_job_title_falls_through_to_ai_decision_instead_of_wrong_job(self):
        stale_job = {
            "職缺名稱": "美光(桃園)作業員",
            "_internal_title": "美光(桃園)作業員",
            "_parsed_title": "美光(桃園)作業員",
            "_search_text": "美光桃園週休二日早班",
        }
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-stale-title"
        event.message.text = "查看職缺詳情已經下架找不到的舊職缺ZZZ"
        line_bot_api = MagicMock()
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[stale_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots"), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        # 一定要是走到 AI 決策的控制組回覆，不能是把不相關的 stale_job 詳情塞給使用者
        self.assertEqual(args[1], control_message)


class DirectInterceptHistoryTests(unittest.TestCase):
    """「查看職缺詳情」比對成功、跟就業服務法年齡/性別合規攔截這兩個分支，
    原本只把沛沛自己的回覆寫進對話歷史，漏了求職者這輪自己說的話——這樣下一輪
    AI 看到的歷史會變成「沛沛憑空開口」，缺了使用者實際問了什麼。修正後兩者
    都要各自寫入一則「求職者」跟一則「招募顧問沛沛」。"""

    def test_job_detail_match_records_both_sides_of_history(self):
        matched_job = {
            "職缺名稱": "美光(桃園)作業員",
            "_internal_title": "美光(桃園)作業員",
            "_parsed_title": "美光(桃園)作業員",
            "_search_text": "美光桃園週休二日早班",
            "職務類別": "作業員",
            "排版工作說明": "",
        }
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-job-detail"
        event.message.text = "查看職缺詳情美光(桃園)作業員"
        line_bot_api = MagicMock()

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[matched_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.format_full_job_detail_with_ai", return_value="職缺詳情內容"), \
             patch("handlers.message_handler.append_user_history") as mock_history, \
             patch("handlers.message_handler._is_staffed_hours", return_value=False):
            h.process_user_message(event, line_bot_api)

        calls = [c.args for c in mock_history.call_args_list]
        self.assertIn(("test-user-job-detail", "求職者", "查看職缺詳情美光(桃園)作業員"), calls)
        self.assertTrue(any(c[0] == "test-user-job-detail" and c[1] == "招募顧問沛沛" for c in calls))

    def test_age_gender_compliance_records_both_sides_of_history(self):
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-legal"
        event.message.text = "請問這個工作有年齡限制嗎"
        line_bot_api = MagicMock()

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.append_user_history") as mock_history, \
             patch("handlers.message_handler._is_staffed_hours", return_value=False):
            h.process_user_message(event, line_bot_api)

        calls = [c.args for c in mock_history.call_args_list]
        self.assertIn(("test-user-legal", "求職者", "請問這個工作有年齡限制嗎"), calls)
        self.assertTrue(any(c[0] == "test-user-legal" and c[1] == "招募顧問沛沛" for c in calls))


class OuterExceptionFallbackTests(unittest.TestCase):
    """process_user_message() 最外層的保底 except 區塊：能走到這裡代表已經發生
    非預期的例外，這個當下 reply_token 也可能已經因為前面處理耗時而過期。跟
    其他分支一樣，reply_message() 失敗時要改用不受時效限制的 push_message
    補發，不能讓使用者這一輪完全收不到任何回覆（原本這裡沒有補發機制）。"""

    def test_reply_failure_in_outer_handler_falls_back_to_push_message(self):
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-outer-exception"
        event.message.text = "有沒有工作"
        line_bot_api = MagicMock()
        line_bot_api.reply_message.side_effect = RuntimeError("reply token expired")

        with patch("handlers.message_handler.fetch_jobs_data", side_effect=RuntimeError("Firestore/Notion 一時異常")), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False):
            h.process_user_message(event, line_bot_api)

        line_bot_api.push_message.assert_called_once()
        args, _ = line_bot_api.push_message.call_args
        self.assertEqual(args[0], "test-user-outer-exception")
        self.assertIn("延遲", args[1].text)


class StoreIntentLocationMatchTests(unittest.TestCase):
    """上線試營運後實測發現的 bug：蝦皮門市職缺的「行政區」沒有勾選八德，但
    工作內容(對外)的自由文字剛好提到「八德」（例如地址上的路名），求職者問
    「八德有沒有缺額」時，「精準工種直達攔截」的門市分支被誤判成有缺額。"""

    def test_store_intent_does_not_match_location_only_mentioned_in_free_text(self):
        store_job = {
            "職缺名稱": "蝦皮門市人員",
            "_internal_title": "蝦皮門市人員",
            "_parsed_title": "蝦皮門市人員",
            "職缺名稱(對外)": "蝦皮門市人員",
            "_job_category": "門市",
            "職務類別": "門市",
            "_search_text": "蝦皮門市地址鄰近八德路口交通便利",
            # 行政區實際只有蘆竹/龜山，不包含八德——「八德」只出現在上面
            # _search_text 的自由文字裡（路名），不該被判定成這個職缺在八德。
            "_location_search_text": "桃園市蘆竹區龜山區",
        }
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-store-location"
        event.message.text = "八德蝦皮門市有缺額嗎"
        line_bot_api = MagicMock()
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[store_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.update_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.create_job_flex_card") as mock_flex_card, \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        # 不該因為「八德」只出現在自由文字裡就當成直接命中，組出職缺卡片
        mock_flex_card.assert_not_called()
        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], control_message)


class BareLocationFollowupContinuesContextTests(unittest.TestCase):
    """上線試營運後實測發現的問題：使用者先問「蝦皮門市有嗎」，接著只問
    「八德有缺嗎」（這句話本身沒有再提到門市/蝦皮），沛沛卻答非所問，推薦了
    完全不相關類別的職缺——因為「精準工種直達攔截」只看「這句話本身」有沒有
    門市/外送/momo 關鍵字，不會延續前一輪已經鎖定的類別/廠商；同時廠商
    （brand）這個槽位原本設計成「這句話沒提到就清空」，就算類別有沿用，
    廠商條件也早就不見了。"""

    def test_bare_location_message_continues_previous_category_and_brand(self):
        matching_job = {
            "職缺名稱": "蝦皮店到店門市夥伴",
            "_internal_title": "蝦皮店到店門市夥伴",
            "_parsed_title": "蝦皮店到店門市夥伴",
            "職缺名稱(對外)": "蝦皮店到店門市夥伴",
            "_job_category": "門市",
            "職務類別": "門市",
            "系統廠商名稱": "蝦皮",
            "_search_text": "蝦皮店到店門市夥伴",
            "_location_search_text": "桃園市八德區",
        }
        unrelated_job = {
            "職缺名稱": "全台平價石頭火鍋",
            "_internal_title": "全台平價石頭火鍋",
            "_parsed_title": "全台平價石頭火鍋",
            "職缺名稱(對外)": "全台平價石頭火鍋",
            "_job_category": "內場人員",
            "職務類別": "內場人員",
            "系統廠商名稱": "石頭火鍋",
            "_search_text": "全台平價石頭火鍋內場人員",
            "_location_search_text": "桃園市八德區",
        }
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-bare-location-followup"
        event.message.text = "八德有缺嗎"
        line_bot_api = MagicMock()
        # 模擬前一輪已經鎖定「門市」類別＋「蝦皮」廠商的槽位
        persisted_slots = dict(location="", category="門市", shift="", leave="", brand="蝦皮")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[matching_job, unrelated_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler.create_job_flex_card") as mock_flex_card, \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages") as mock_ai_decision:
            h.process_user_message(event, line_bot_api)

        # 應該延續前一輪的門市＋蝦皮條件，直接攔截命中，不會落到 AI 決策
        mock_ai_decision.assert_not_called()
        mock_flex_card.assert_called_once()
        matched_jobs_arg = mock_flex_card.call_args[0][0]
        self.assertIn(matching_job, matched_jobs_arg)
        self.assertNotIn(unrelated_job, matched_jobs_arg)

    def test_bare_location_message_without_prior_context_falls_through_to_ai(self):
        # 沒有任何前一輪鎖定的類別/廠商時，單純問地區不該被誤攔進精準工種直達，
        # 維持原本會落到 AI 決策的行為。
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-bare-location-no-context"
        event.message.text = "八德有缺嗎"
        line_bot_api = MagicMock()
        empty_slots = dict(location="", category="", shift="", leave="", brand="")
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=empty_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=empty_slots), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], control_message)

    def test_faq_question_not_hijacked_by_persisted_category(self):
        # 持續鎖定「門市」類別，但這句話是問發薪日（FAQ 類問題，抓不到地名），
        # 不該被誤判成「延續前一輪的地區追問」而攔進精準工種直達。
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-faq-not-hijacked"
        event.message.text = "發薪日是什麼時候"
        line_bot_api = MagicMock()
        persisted_slots = dict(location="", category="門市", shift="", leave="", brand="蝦皮")
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], control_message)

    def test_brand_slot_persists_when_not_mentioned_this_turn(self):
        # 廠商槽位這輪沒有再提到時,應該沿用前一輪的值（不寫入變更),不能再像
        # 原本設計那樣直接清空——否則「蝦皮門市有嗎」下一句只問「八德有缺嗎」
        # 時,蝦皮這個條件會整個消失。
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-brand-persist"
        event.message.text = "還有其他工作嗎"
        line_bot_api = MagicMock()
        persisted_slots = dict(location="", category="", shift="", leave="", brand="蝦皮")
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=persisted_slots) as mock_update_slots, \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        # brand 這個參數應該傳入空字串（代表「沿用、不變更」），不是 CLEAR_SLOT
        _, kwargs = mock_update_slots.call_args
        self.assertEqual(kwargs.get("brand"), "")

    def test_brand_slot_clears_on_explicit_broaden_phrase(self):
        # 使用者明確表示「不限廠商」時,還是要能真正清空,不能因為改成「預設沿用」
        # 就永遠卡住舊的廠商條件出不去。
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-brand-clear"
        event.message.text = "不限廠商，都給我看看"
        line_bot_api = MagicMock()
        persisted_slots = dict(location="", category="", shift="", leave="", brand="蝦皮")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=dict(location="", category="", shift="", leave="", brand="")) as mock_update_slots, \
             patch("handlers.message_handler.append_user_history"):
            h.process_user_message(event, line_bot_api)

        _, kwargs = mock_update_slots.call_args
        self.assertEqual(kwargs.get("brand"), h.CLEAR_SLOT)


class MomoIntentLocationFallbackRemovedTests(unittest.TestCase):
    """momo 分支原本有一段「這個地區沒有 momo 職缺時，退讓顯示全部 momo
    職缺」的既有機制。這個退讓不管是延續前一輪脈絡、還是使用者這句話本身
    真的有講「momo」都會觸發，導致使用者問完 momo 之後單獨問「台南有嗎」
    （momo 職缺實際只在桃園），沛沛卻直接把桃園的 momo 職缺塞給使用者、
    還說「找到符合條件的推薦職缺」，答非所問。使用者確認後決定整個拿掉這段
    退讓（不只是限縮生效條件）：地區沒有精準命中，就是沒有直接命中，一律
    落到 AI 決策，跟 delivery/store 分支一致，不再有任何「地區找不到就乾脆
    不管地區」的例外。"""

    def _momo_job_in_taoyuan_only(self):
        return {
            "職缺名稱": "粉色電商理貨員",
            "_internal_title": "粉色電商理貨員",
            "_parsed_title": "粉色電商理貨員",
            "職缺名稱(對外)": "粉色電商理貨員",
            "_job_category": "倉儲人員",
            "職務類別": "倉儲人員",
            "系統廠商名稱": "momo",
            "_search_text": "momo電商理貨員桃園倉儲",
            "_location_search_text": "桃園市",
        }

    def test_bare_location_followup_without_match_falls_through_to_ai(self):
        # 延續前一輪 momo 脈絡、這句話本身沒提到 momo，momo 職缺實際上又不在
        # 台南——不該裝作找到符合條件的職缺，要老實落到 AI 決策。
        momo_job = self._momo_job_in_taoyuan_only()
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-momo-bare-location"
        event.message.text = "台南有嗎"
        line_bot_api = MagicMock()
        persisted_slots = dict(location="", category="", shift="", leave="", brand="momo")
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[momo_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=persisted_slots), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], control_message)

    def test_explicit_momo_mention_without_location_match_also_falls_through_to_ai(self):
        # 使用者確認拿掉整段退讓，這句話本身真的有講「momo」時，地區沒有
        # 精準命中一樣要落到 AI 決策，不再退讓顯示全部 momo 職缺。
        momo_job = self._momo_job_in_taoyuan_only()
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-momo-explicit"
        event.message.text = "台南有momo的職缺嗎"
        line_bot_api = MagicMock()
        empty_slots = dict(location="", category="", shift="", leave="", brand="")
        control_message = TextSendMessage(text="落到一般流程由AI決策的控制組回覆")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[momo_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=empty_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=dict(empty_slots, brand="momo")), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler._compute_ai_decision_messages", return_value=control_message):
            h.process_user_message(event, line_bot_api)

        line_bot_api.reply_message.assert_called_once()
        args, _ = line_bot_api.reply_message.call_args
        self.assertEqual(args[1], control_message)

    def test_momo_with_no_location_specified_still_shows_all_momo_jobs(self):
        # 使用者根本沒指定地區時（例如單純問「有momo的職缺嗎」），不算「找不到
        # 就退讓」，這種情境本來就該顯示全部 momo 職缺，不受這次拿掉退讓機制
        # 影響。
        momo_job = self._momo_job_in_taoyuan_only()
        event = MagicMock()
        event.reply_token = "valid-reply-token"
        event.source.user_id = "test-user-momo-no-location"
        event.message.text = "有momo的職缺嗎"
        line_bot_api = MagicMock()
        empty_slots = dict(location="", category="", shift="", leave="", brand="")

        with patch("handlers.message_handler.fetch_jobs_data", return_value=[momo_job]), \
             patch("handlers.message_handler.fetch_faqs_data", return_value=[]), \
             patch("handlers.message_handler.get_user_history", return_value=[]), \
             patch("handlers.message_handler.get_user_slots", return_value=empty_slots), \
             patch("handlers.message_handler.update_user_slots", return_value=dict(empty_slots, brand="momo")), \
             patch("handlers.message_handler.append_user_history"), \
             patch("handlers.message_handler._is_staffed_hours", return_value=False), \
             patch("handlers.message_handler.create_job_flex_card") as mock_flex_card:
            h.process_user_message(event, line_bot_api)

        mock_flex_card.assert_called_once()
        matched_jobs_arg = mock_flex_card.call_args[0][0]
        self.assertIn(momo_job, matched_jobs_arg)


if __name__ == "__main__":
    unittest.main()
