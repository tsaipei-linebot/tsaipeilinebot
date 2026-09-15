"""同仁如果不是在 LINE 群組回報，而是直接在配送部系統網站填寫車輛領還車
或新增意外事件，也把通知推播到「配送組作業群組」，跟 LINE 群組回報的
體驗一致。

真正推播用的 LINE Channel Token 一直留在 delivery-gas-project（GAS 專案）
那邊，這裡只負責呼叫它的 doGet(?type=DELIVERY_NOTIFY) 橋接，把訊息內容
送過去，完全不需要、也不會拿到那個 Token（見該專案 Project7_
DeliveryNotify.js 的說明）。

推播失敗（沒設定好、逾時、網路錯誤）都只回傳 False、不會拋例外——這是
附加的通知功能，不該因為推播失敗就讓網站表單本身的送出跟著失敗。
"""
from delivery.config import DELIVERY_NOTIFY_WEBHOOK_SECRET, DELIVERY_NOTIFY_WEBHOOK_URL

_TIMEOUT_SECONDS = 5


def is_configured() -> bool:
    return bool(DELIVERY_NOTIFY_WEBHOOK_URL and DELIVERY_NOTIFY_WEBHOOK_SECRET)


def notify_group(text: str) -> bool:
    """回傳是否有實際送出成功。"""
    if not is_configured():
        return False
    try:
        import requests

        response = requests.get(
            DELIVERY_NOTIFY_WEBHOOK_URL,
            params={"type": "DELIVERY_NOTIFY", "secret": DELIVERY_NOTIFY_WEBHOOK_SECRET, "text": text},
            timeout=_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[配送部系統] 群組通知推播失敗：{e}")
        return False
