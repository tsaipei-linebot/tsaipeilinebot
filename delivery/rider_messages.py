"""外送員接單媒合（2026-09-19 新增）：組出要回覆給騎士的 LINE 訊息內容。

這裡刻意不使用 linebot.models（那是給「Python 這邊自己呼叫 LineBotApi.
reply_message()」的情境用的，例如 services/flex_service.py），因為這幾支
訊息最終不是這裡直接送到 LINE——是 delivery-gas-project 那支 GAS 專案
收到 /api/rider-events 的回應後，自己拿它現有、已經在跑的 CHANNEL1 Token
呼叫 LINE Reply API 轉發出去（見 delivery/config.py 開頭的說明），所以這裡
只需要組出 LINE 訊息物件本身的 JSON 結構（跟 flex_service.py 組 Flex 內容
時一樣，直接寫成 dict），讓 GAS 原封不動塞進 `messages` 陣列轉發就好。
"""
from datetime import datetime

from config import TAIPEI_TZ

MAX_CAROUSEL_BUBBLES = 10


def text_message(text: str) -> dict:
    return {"type": "text", "text": text}


def not_bound_message() -> dict:
    return text_message(
        "您目前還沒有完成綁定，請先私訊本帳號「綁定+工號+姓名」完成綁定，才能使用即時接單／報班媒合功能。"
    )


def blocked_message() -> dict:
    return text_message("您目前無法使用這項功能，如有疑問請聯絡配送部管理員。")


def not_eligible_for_order_message() -> dict:
    return text_message("即時接單僅限承攬身份的合作騎士使用，您目前登記的合作身份無法使用這項功能，如有疑問請聯絡配送部管理員。")


def not_eligible_for_shift_message() -> dict:
    return text_message("報班媒合僅限雇傭身份的合作騎士使用，您目前登記的合作身份無法使用這項功能，如有疑問請聯絡配送部管理員。")


def prompt_share_location_message() -> dict:
    """請騎士分享目前位置——訊息本身附上一顆 LINE 的 Quick Reply「位置」
    按鈕（點一下直接跳出 LINE 內建的位置選擇畫面），不用像純文字說明那樣
    自己去點左下角「+」再選「位置資訊」，省一道操作步驟。這個按鈕只是
    LINE 訊息 JSON 裡多一個 quickReply 欄位，delivery-gas-project 那邊的
    replyLineRawMessages_() 本來就是原封不動轉發整包訊息物件，不需要另外
    修改 GAS 那邊的程式碼。"""
    message = text_message("請分享您目前的位置，系統會幫您找出附近還有貨量可以承接的門市（點下面的「分享目前位置」按鈕最快）。")
    message["quickReply"] = {
        "items": [
            {"type": "action", "action": {"type": "location", "label": "分享目前位置"}},
        ]
    }
    return message


def no_nearby_stores_message() -> dict:
    return text_message("目前附近沒有開放中、還有剩餘量的門市貨量，請稍後再試。")


def nearby_stores_carousel(stores: list) -> dict:
    bubbles = []
    for store in stores[:MAX_CAROUSEL_BUBBLES]:
        distance_km = store.get("distance_km")
        distance_text = f"約 {distance_km:.1f} 公里" if distance_km is not None else "距離未知"
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": store.get("store_name", ""), "weight": "bold", "size": "lg", "wrap": True},
                    {"type": "text", "text": distance_text, "size": "sm", "color": "#666666", "margin": "sm"},
                    {
                        "type": "text",
                        "text": f"可承接量：{store.get('remaining_quantity', 0)} 件",
                        "size": "md",
                        "color": "#D32F2F",
                        "weight": "bold",
                        "margin": "sm",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#ea580c",
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "承接",
                            "data": f"action=CLAIM_STORE&storeId={store.get('id', '')}",
                        },
                    }
                ],
            },
        }
        bubbles.append(bubble)
    return {"type": "flex", "altText": f"為您找到 {len(bubbles)} 間附近有貨量的門市", "contents": {"type": "carousel", "contents": bubbles}}


def prompt_claim_quantity_message(store: dict) -> dict:
    return text_message(
        f"「{store.get('store_name', '')}」目前剩餘可承接量 {store.get('remaining_quantity', 0)} 件，請直接回覆您要承接的件數（純數字）。"
    )


def claim_expired_message() -> dict:
    return text_message("這次承接的操作已經逾時失效，請重新查詢附近單一次。")


def invalid_quantity_message() -> dict:
    return text_message("請輸入大於 0 的整數件數，例如：5")


def no_open_shifts_message() -> dict:
    return text_message("目前沒有開放中的報班時段，請稍後再試。")


def shifts_carousel(shifts: list) -> dict:
    bubbles = []
    for shift in shifts[:MAX_CAROUSEL_BUBBLES]:
        capacity = shift.get("capacity") or 0
        registered = shift.get("registered_count") or 0
        remaining = max(capacity - registered, 0)
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": shift.get("location", ""), "weight": "bold", "size": "lg", "wrap": True},
                    {"type": "text", "text": format_shift_time_range(shift), "size": "sm", "color": "#666666", "margin": "sm", "wrap": True},
                    {
                        "type": "text",
                        "text": f"剩餘名額：{remaining} / {capacity} 人",
                        "size": "md",
                        "color": "#D32F2F",
                        "weight": "bold",
                        "margin": "sm",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#ea580c",
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "報名",
                            "data": f"action=REGISTER_SHIFT&shiftId={shift.get('id', '')}",
                        },
                    }
                ],
            },
        }
        bubbles.append(bubble)
    return {"type": "flex", "altText": f"目前有 {len(bubbles)} 個開放中的報班時段", "contents": {"type": "carousel", "contents": bubbles}}


def format_shift_time_range(shift: dict) -> str:
    start_time = shift.get("start_time")
    end_time = shift.get("end_time")
    if not start_time or not end_time:
        return ""
    start_str = datetime.fromtimestamp(start_time, TAIPEI_TZ).strftime("%m/%d %H:%M")
    end_str = datetime.fromtimestamp(end_time, TAIPEI_TZ).strftime("%H:%M")
    return f"{start_str} - {end_str}"
