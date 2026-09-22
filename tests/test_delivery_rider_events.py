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
    """未綁定/已停用的騎士，傳跟這兩個功能相關的事件（關鍵字/位置/Postback/
    數字）只回一句擋下訊息，不會走到任何業務邏輯（不會查資料庫的門市/
    時段）。"""

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

    def test_unrelated_text_from_unbound_user_stays_silent(self):
        """2026-09-19 修正的迴歸測試：GAS 那邊除了「綁定+工號+姓名」跟
        「接受本日發包任務」，其餘私訊文字一律轉發過來，如果不先判斷這
        則事件是不是真的在跟接單/報班互動就查綁定狀態，會變成任何人傳
        任何一句不相干的閒聊都收到「尚未完成綁定」，比完全不回覆更糟。
        這裡確認不相干文字連 Firestore 都不會查，直接安靜略過。"""
        with mock.patch.object(rider_repository, "get_rider_binding") as mock_get_binding:
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "message", "message_type": "text", "text": "今天天氣真好"}
            )
        self.assertEqual(messages, [])
        mock_get_binding.assert_not_called()


class _ActiveBindingMixin:
    def setUp(self):
        self.binding = {"user_id": "U1", "status": "active", "name": "小明"}
        patcher = mock.patch.object(rider_repository, "get_rider_binding", return_value=self.binding)
        self.addCleanup(patcher.stop)
        patcher.start()


class TextKeywordDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    """2026-09-21 起，即時接單關鍵字要求 rider_feature_category() 回傳
    「承攬」、報班媒合關鍵字要求回傳「雇傭」，這裡每個情境各自 patch
    對應的分類，跟資格判斷本身的邊界情況（見 EligibilityGatingTests）
    分開測。"""

    def test_nearby_order_keyword_prompts_location_share(self):
        """2026-09-19 改成附上 Quick Reply「位置」按鈕，一鍵分享位置，不用
        自己點左下角「+」→「位置資訊」（見 rider_messages.py）。"""
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "set_awaiting_location") as mock_set:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        mock_set.assert_called_once_with("U1")
        self.assertEqual(len(messages), 1)
        self.assertIn("分享", messages[0]["text"])
        self.assertEqual(messages[0]["quickReply"]["items"][0]["action"]["type"], "location")

    def test_shift_list_keyword_prompts_location_share(self):
        """2026-09-21 起，報班媒合改成也要先分享位置（幾公里內才看得到），
        跟即時接單同一種 Quick Reply 位置按鈕機制，不再馬上列出全部時段。"""
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(rider_repository, "set_awaiting_shift_location") as mock_set:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "瀏覽報班"})
        mock_set.assert_called_once_with("U1")
        self.assertEqual(len(messages), 1)
        self.assertIn("分享", messages[0]["text"])
        self.assertEqual(messages[0]["quickReply"]["items"][0]["action"]["type"], "location")

    def test_shift_status_query_keyword_returns_registration_status_message(self):
        """2026-09-21 新增：報班改成人工審核制，騎士傳「查詢報名狀態」
        查詢最近幾筆報名的審核結果。"""
        registrations = [{"shift_location": "台北車站", "status": "pending"}]
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(
                rider_repository, "list_registrations_by_rider", return_value=registrations
            ) as mock_list:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "message", "message_type": "text", "text": "查詢報名狀態"}
                )
        mock_list.assert_called_once_with("U1")
        self.assertEqual(len(messages), 1)
        self.assertIn("台北車站", messages[0]["text"])

    def test_unrecognized_text_returns_no_messages(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "今天天氣真好"})
        self.assertEqual(messages, [])


class EligibilityGatingTests(_ActiveBindingMixin, unittest.TestCase):
    """2026-09-21 新增：即時接單限承攬、報班媒合限雇傭，身份不符（含工號
    對不到人員名冊、合作方式沒設定分類等情況，rider_feature_category()
    一律回傳空字串）都要擋下，不能走到查詢/操作邏輯。"""

    def test_order_keyword_blocked_for_employed_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(rider_repository, "set_awaiting_location") as mock_set:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        mock_set.assert_not_called()
        self.assertIn("即時接單僅限承攬", messages[0]["text"])

    def test_order_keyword_blocked_when_category_unresolved(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value=""):
            messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "查詢附近單"})
        self.assertIn("即時接單僅限承攬", messages[0]["text"])

    def test_shift_keyword_blocked_for_contract_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "list_open_shift_postings") as mock_list:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "text", "text": "瀏覽報班"})
        mock_list.assert_not_called()
        self.assertIn("報班媒合僅限雇傭", messages[0]["text"])

    def test_claim_store_postback_blocked_for_employed_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(rider_repository, "get_store_delivery") as mock_get:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "postback", "postback_data": "action=CLAIM_STORE&storeId=store1"}
                )
        mock_get.assert_not_called()
        self.assertIn("即時接單僅限承攬", messages[0]["text"])

    def test_shift_list_postback_blocked_for_contract_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "list_open_shift_postings") as mock_list:
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "postback", "postback_data": "action=SHIFT_LIST"})
        mock_list.assert_not_called()
        self.assertIn("報班媒合僅限雇傭", messages[0]["text"])

    def test_register_shift_postback_blocked_for_contract_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "register_shift") as mock_register:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "postback", "postback_data": "action=REGISTER_SHIFT&shiftId=shift1"}
                )
        mock_register.assert_not_called()
        self.assertIn("報班媒合僅限雇傭", messages[0]["text"])

    def test_shift_status_query_keyword_blocked_for_contract_category(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "list_registrations_by_rider") as mock_list:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "message", "message_type": "text", "text": "查詢報名狀態"}
                )
        mock_list.assert_not_called()
        self.assertIn("報班媒合僅限雇傭", messages[0]["text"])


class DigitTextIsNoLongerRelevantTests(_ActiveBindingMixin, unittest.TestCase):
    """2026-09-22 改版：承接不再需要回覆件數，純數字訊息已經不是這個功能
    的一部分，應該完全安靜略過（不能再回「操作逾時」那種文不對題的訊息）。"""

    def test_digit_text_stays_silent(self):
        with mock.patch.object(rider_repository, "claim_store_delivery") as mock_claim:
            messages = rider_events.handle_rider_event(
                {"userId": "U1", "type": "message", "message_type": "text", "text": "5"}
            )
        self.assertEqual(messages, [])
        mock_claim.assert_not_called()


class LocationDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    """2026-09-19 使用者反映任何位置分享都被當成相關事件太容易誤觸發，
    改成只有先問過「查詢附近單」或「瀏覽報班」其中一種（分別對應
    pop_awaiting_location()／pop_awaiting_shift_location() 為 True）才算
    相關（2026-09-21 報班媒合加入服務半徑篩選後，也一併要求先分享位置）。
    一則位置訊息可能同時符合兩者（理論上極少發生），這裡兩個 pop 一律
    都會執行；有沒有觸發列出附近單/附近報班則看各自的回傳值。"""

    def test_location_without_prior_request_stays_silent(self):
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=False):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=False):
                with mock.patch.object(rider_repository, "list_nearby_open_stores") as mock_list_stores:
                    with mock.patch.object(rider_repository, "list_open_shift_postings") as mock_list_shifts:
                        messages = rider_events.handle_rider_event(
                            {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
                        )
        self.assertEqual(messages, [])
        mock_list_stores.assert_not_called()
        mock_list_shifts.assert_not_called()

    def test_location_message_with_no_nearby_stores(self):
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=True):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=False):
                with mock.patch.object(rider_repository, "list_nearby_open_stores", return_value=[]):
                    messages = rider_events.handle_rider_event(
                        {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
                    )
        self.assertIn("沒有開放中", messages[0]["text"])

    def test_location_message_with_nearby_stores_returns_carousel(self):
        stores = [
            {
                "id": "s1",
                "store_name": "中和門市",
                "total_quantity": 30,
                "rider_capacity": 3,
                "remaining_rider_slots": 2,
                "distance_km": 1.2,
            }
        ]
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=True):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=False):
                with mock.patch.object(rider_repository, "list_nearby_open_stores", return_value=stores) as mock_list:
                    messages = rider_events.handle_rider_event(
                        {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
                    )
        self.assertEqual(messages[0]["type"], "flex")
        # 2026-09-22 新增：附近單查詢要帶這位騎士的 userId，才能把他自己
        # 已經承接過的門市濾掉。
        self.assertEqual(mock_list.call_args.kwargs["rider_id"], "U1")

    def test_location_message_with_no_nearby_shifts(self):
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=False):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=True):
                with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=[]) as mock_list_shifts:
                    messages = rider_events.handle_rider_event(
                        {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
                    )
        mock_list_shifts.assert_called_once_with(25.0, 121.5)
        self.assertIn("附近目前沒有開放中的報班時段", messages[0]["text"])

    def test_location_message_with_nearby_shifts_returns_carousel(self):
        shifts = [{"id": "s1", "location": "台北車站", "capacity": 3, "registered_count": 1, "start_time": 0, "end_time": 0, "distance_km": 1.2}]
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=False):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=True):
                with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=shifts):
                    messages = rider_events.handle_rider_event(
                        {"userId": "U1", "type": "message", "message_type": "location", "latitude": 25.0, "longitude": 121.5}
                    )
        self.assertEqual(messages[0]["type"], "flex")

    def test_location_missing_coordinates_returns_no_messages(self):
        with mock.patch.object(rider_repository, "pop_awaiting_location", return_value=True):
            with mock.patch.object(rider_repository, "pop_awaiting_shift_location", return_value=False):
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "message", "message_type": "location"})
        self.assertEqual(messages, [])


class PostbackDispatchTests(_ActiveBindingMixin, unittest.TestCase):
    """2026-09-22 改版：點「承接」直接完成，不再多問一次件數；承接成功
    要同步推播一則到配送組作業群組。"""

    _STORE = {"id": "store1", "store_name": "中和門市", "status": "open", "rider_capacity": 3}
    _CLAIM_INFO = {
        "store_name": "中和門市",
        "rider_capacity": 3,
        "claimed_rider_count": 2,
        "remaining_rider_slots": 1,
    }

    def _claim_postback(self, claim_result):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "get_store_delivery", return_value=self._STORE):
                with mock.patch.object(
                    rider_repository, "claim_store_delivery", return_value=claim_result
                ) as mock_claim:
                    with mock.patch.object(rider_events.group_notify, "notify_group") as mock_notify:
                        messages = rider_events.handle_rider_event(
                            {"userId": "U1", "type": "postback", "postback_data": "action=CLAIM_STORE&storeId=store1"}
                        )
        return messages, mock_claim, mock_notify

    def test_claim_store_postback_claims_immediately(self):
        messages, mock_claim, _ = self._claim_postback(
            (True, "✅ 已登記承攬請前往配送\n門市：中和門市", self._CLAIM_INFO)
        )
        mock_claim.assert_called_once_with("store1", "U1", "小明")
        self.assertIn("已登記承攬請前往配送", messages[0]["text"])

    def test_successful_claim_notifies_delivery_group(self):
        _, _, mock_notify = self._claim_postback(
            (True, "✅ 已登記承攬請前往配送\n門市：中和門市", self._CLAIM_INFO)
        )
        mock_notify.assert_called_once()
        text = mock_notify.call_args.args[0]
        self.assertIn("小明", text)
        self.assertIn("中和門市", text)
        self.assertIn("還缺 1 位騎士", text)

    def test_failed_claim_does_not_notify_group(self):
        messages, _, mock_notify = self._claim_postback((False, "這間門市需要的騎士人數已經額滿，請改承接其他門市。", None))
        mock_notify.assert_not_called()
        self.assertIn("額滿", messages[0]["text"])

    def test_claim_store_postback_for_closed_store(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="contract"):
            with mock.patch.object(rider_repository, "get_store_delivery", return_value=None):
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "postback", "postback_data": "action=CLAIM_STORE&storeId=gone"}
                )
        self.assertIn("已經不存在或已關閉", messages[0]["text"])

    def test_register_shift_postback_calls_register_shift(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(rider_repository, "register_shift", return_value=(True, "報名成功！")) as mock_register:
                messages = rider_events.handle_rider_event(
                    {"userId": "U1", "type": "postback", "postback_data": "action=REGISTER_SHIFT&shiftId=shift1"}
                )
        mock_register.assert_called_once_with("shift1", "U1", "小明")
        self.assertIn("報名成功", messages[0]["text"])

    def test_shift_list_postback_returns_carousel_or_empty_message(self):
        with mock.patch.object(rider_repository, "rider_feature_category", return_value="employed"):
            with mock.patch.object(rider_repository, "list_open_shift_postings", return_value=[]):
                messages = rider_events.handle_rider_event({"userId": "U1", "type": "postback", "postback_data": "action=SHIFT_LIST"})
        self.assertIn("沒有開放中的報班時段", messages[0]["text"])

    def test_unrecognized_postback_action_returns_no_messages(self):
        messages = rider_events.handle_rider_event({"userId": "U1", "type": "postback", "postback_data": "action=UNKNOWN"})
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
