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


def notify_group(text: str, also_notify_incident_group: bool = False) -> bool:
    """回傳是否有實際送出成功。

    also_notify_incident_group=True 時，額外帶一個 alsoNotify=incident
    參數給 GAS 那支橋接——GAS 收到後除了推播到「配送組作業群組」
    （VEHICLE_REPORT_GROUP_ID），還會**用它自己手上的**
    INCIDENT_NOTIFY_GROUP_ID 指令碼屬性，把同一則訊息也推播到 LINE 群組
    回報原本就有的「管理／督導」第二個群組（見 Project7_
    DeliveryNotify.js）。這裡故意不直接把第二個群組的 ID 當參數傳過去
    ——那個 ID 只有 GAS 那邊知道，維持「Cloud Run 不需要、也不會拿到
    群組 ID／Token 這類憑證」的既有安全邊界，Python 這邊只表達「這是一筆
    意外事件，麻煩也通知第二個群組」的意圖，實際群組 ID 由 GAS 自己決定。"""
    if not is_configured():
        return False
    try:
        import requests

        params = {"type": "DELIVERY_NOTIFY", "secret": DELIVERY_NOTIFY_WEBHOOK_SECRET, "text": text}
        if also_notify_incident_group:
            params["alsoNotify"] = "incident"
        response = requests.get(DELIVERY_NOTIFY_WEBHOOK_URL, params=params, timeout=_TIMEOUT_SECONDS)
        return response.status_code == 200
    except Exception as e:
        print(f"[配送部系統] 群組通知推播失敗：{e}")
        return False
