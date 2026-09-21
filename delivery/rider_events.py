"""解析 delivery-gas-project 轉發過來的騎士 LINE 事件（1 對 1 私訊：文字／
位置訊息／Postback），決定要回覆哪些訊息。

跟 vehicle_report.py／incident_report.py 是同一種分工：這裡負責「事件長
什麼樣子該回什麼」的判斷邏輯，實際的 Firestore 讀寫在 rider_repository.py、
訊息內容組裝在 rider_messages.py，webhook_routes.py 只負責密鑰驗證跟呼叫
handle_rider_event()。

GAS 那邊轉發過來的 JSON 格式（見 delivery-gas-project 的
Project8_RiderMatching.js）：
    {
      "userId": "U123...",
      "type": "message" | "postback",
      "message_type": "text" | "location",   # type == "message" 才有
      "text": "...",                          # message_type == "text" 才有
      "latitude": 25.0330, "longitude": 121.5654,  # message_type == "location" 才有
      "postback_data": "action=CLAIM_STORE&storeId=abc123"  # type == "postback" 才有
    }
回傳值是一份 LINE 訊息物件的 list（可能是空 list，代表這則事件不需要回覆，
GAS 那邊就不會呼叫 LINE Reply API，replyToken 自然過期，不會有任何副作用）。
"""
from datetime import datetime
from urllib.parse import parse_qsl

from config import TAIPEI_TZ
from delivery import rider_messages, rider_repository


def handle_rider_event(body: dict) -> list:
    user_id = (body.get("userId") or "").strip()
    if not user_id:
        return []

    event_type = body.get("type") or ""
    message_type = body.get("message_type") or ""
    text = (body.get("text") or "").strip() if message_type == "text" else ""

    # GAS 那邊除了「綁定+工號+姓名」「接受本日發包任務」這兩種固定格式，
    # 其餘私訊文字一律轉發過來（見 Project8_RiderMatching.js），所以這裡
    # 一定要先判斷這則事件看起來是不是真的在跟這兩個功能互動，才去查
    # 綁定狀態——不然任何人（不管有沒有綁定過）傳一句不相干的閒聊，都會
    # 收到「尚未完成綁定」這種文不對題的回覆，比完全不回覆更糟。
    #
    # 位置訊息、純數字文字這兩種情況刻意額外要求「使用者剛做過對應的
    # 前置動作」才算相關（2026-09-19 使用者反映任何位置分享/任何數字
    # 文字都會觸發回覆，太容易誤觸發）：分享位置一定要先問過「查詢附近
    # 單」，純數字一定要先點過「承接」按鈕，兩者都有 10 分鐘的有效期限
    # （RIDER_PENDING_CLAIM_TTL_SECONDS）。這裡用 pop_awaiting_location()
    # 而不是單純檢查有沒有暫存，是因為判斷完相關與否後就不需要再保留這個
    # 一次性的暫存狀態；has_pending_claim() 則只是檢查、不清除，清除交給
    # 真的處理這則訊息時的 pop_pending_claim() 做。
    if event_type == "postback":
        is_relevant = True
    elif text in _NEARBY_ORDER_KEYWORDS or text in _SHIFT_LIST_KEYWORDS:
        is_relevant = True
    elif message_type == "location":
        is_relevant = rider_repository.pop_awaiting_location(user_id)
    elif text.isdigit():
        is_relevant = rider_repository.has_pending_claim(user_id)
    else:
        is_relevant = False
    if not is_relevant:
        return []

    binding = rider_repository.get_rider_binding(user_id)
    if not binding:
        return [rider_messages.not_bound_message()]
    if binding.get("status") != rider_repository.RIDER_STATUS_ACTIVE:
        return [rider_messages.blocked_message()]

    if event_type == "postback":
        return _handle_postback(user_id, binding, body.get("postback_data") or "")
    if message_type == "location":
        return _handle_location(body.get("latitude"), body.get("longitude"))
    if message_type == "text":
        return _handle_text(user_id, binding, text)
    return []


def _handle_postback(user_id: str, binding: dict, data: str) -> list:
    params = dict(parse_qsl(data))
    action = params.get("action", "")

    if action == "CLAIM_STORE":
        if not _is_eligible_for_order(binding):
            return [rider_messages.not_eligible_for_order_message()]
        store = rider_repository.get_store_delivery(params.get("storeId", ""))
        if not store or store.get("status") != rider_repository.STORE_DELIVERY_STATUS_OPEN:
            return [rider_messages.text_message("這筆門市當日量已經不存在或已關閉，請重新查詢附近單。")]
        rider_repository.set_pending_claim(user_id, store["id"])
        return [rider_messages.prompt_claim_quantity_message(store)]

    if action == "SHIFT_LIST":
        if not _is_eligible_for_shift(binding):
            return [rider_messages.not_eligible_for_shift_message()]
        return _list_open_shifts()

    if action == "REGISTER_SHIFT":
        if not _is_eligible_for_shift(binding):
            return [rider_messages.not_eligible_for_shift_message()]
        _, message = rider_repository.register_shift(params.get("shiftId", ""), user_id, binding.get("name", ""))
        return [rider_messages.text_message(message)]

    return []


def _handle_location(lat, lng) -> list:
    if lat is None or lng is None:
        return []
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    stores = rider_repository.list_nearby_open_stores(float(lat), float(lng), today)
    if not stores:
        return [rider_messages.no_nearby_stores_message()]
    return [rider_messages.nearby_stores_carousel(stores)]


# 私訊裡觸發「查詢附近單」「瀏覽報班」的關鍵字，刻意收斂成固定幾種常見說
# 法（跟現有「接受本日發包任務」允許好幾種變化寫法是同一個考量），不做成
# LINE 圖文選單（Rich Menu）——那需要另外呼叫 LINE Rich Menu API 設定，
# 增加不必要的複雜度，一階段用文字關鍵字就能達到一樣的效果。
_NEARBY_ORDER_KEYWORDS = {"查詢附近單", "即時接單", "附近單"}
_SHIFT_LIST_KEYWORDS = {"瀏覽報班", "報班媒合", "報班"}


def _is_eligible_for_order(binding: dict) -> bool:
    """即時接單僅限合作方式屬於「承攬」的騎士（見 config.py 的
    COOPERATION_CATEGORY_CONTRACT）——資格照這個人在人員名冊裡「目前」的
    合作方式即時查詢，工號核對不到人員名冊資料、或合作方式沒設定分類，
    都視同不符資格。"""
    return rider_repository.rider_feature_category(binding) == rider_repository.COOPERATION_CATEGORY_CONTRACT


def _is_eligible_for_shift(binding: dict) -> bool:
    """報班媒合僅限合作方式屬於「雇傭」的騎士，跟 _is_eligible_for_order()
    是同一種判斷方式，只是比對的分類不同。"""
    return rider_repository.rider_feature_category(binding) == rider_repository.COOPERATION_CATEGORY_EMPLOYED


def _handle_text(user_id: str, binding: dict, text: str) -> list:
    if text in _NEARBY_ORDER_KEYWORDS:
        if not _is_eligible_for_order(binding):
            return [rider_messages.not_eligible_for_order_message()]
        rider_repository.set_awaiting_location(user_id)
        return [rider_messages.prompt_share_location_message()]

    if text in _SHIFT_LIST_KEYWORDS:
        if not _is_eligible_for_shift(binding):
            return [rider_messages.not_eligible_for_shift_message()]
        return _list_open_shifts()

    if text.isdigit():
        store_id = rider_repository.pop_pending_claim(user_id)
        if not store_id:
            # 沒有暫存中的承接操作：可能是逾時、也可能只是騎士傳了一則
            # 剛好是純數字的不相干訊息，兩種情況都用同一句話回覆即可。
            return [rider_messages.claim_expired_message()]
        quantity = int(text)
        if quantity <= 0:
            rider_repository.set_pending_claim(user_id, store_id)
            return [rider_messages.invalid_quantity_message()]
        _, message = rider_repository.claim_store_delivery(store_id, user_id, binding.get("name", ""), quantity)
        return [rider_messages.text_message(message)]

    return []


def _list_open_shifts() -> list:
    shifts = rider_repository.list_open_shift_postings()
    if not shifts:
        return [rider_messages.no_open_shifts_message()]
    return [rider_messages.shifts_carousel(shifts)]
