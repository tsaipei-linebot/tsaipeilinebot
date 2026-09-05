"""管理部專屬的 LINE 官方帳號——跟招募機器人（沛沛）、配送部完全獨立的
另一個 LINE Messaging API Channel。這裡刻意只做兩件事：
1. 推播通知（見 push_group_message()），目前只有「門號繳費提醒」會用到
   （見 routes/reminder_routes.py）。
2. Webhook 收到訊息時回覆這個聊天室的 ID（見 routes/line_webhook_routes.py），
   方便設定 MANAGEMENT_LINE_GROUP_ID 環境變數，不用像配送部那樣去 Cloud
   Logging 撈。

完全不接任何自動對話/AI 邏輯——如果跟沛沛共用同一個 Channel，管理部群組
裡的任何訊息都會被沛沛現有的求職者對話 AI 接手處理，內部群組聊天會被
誤判成求職者在問工作機會，所以才另外申請一個全新帳號，兩邊完全隔離。
"""
from linebot import LineBotApi, WebhookHandler

from management.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, LINE_NOTIFY_GROUP_ID

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None


def is_configured() -> bool:
    return bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_NOTIFY_GROUP_ID)


def push_group_message(text: str) -> bool:
    """推播一則文字訊息到設定好的管理部群組。回傳是否有實際送出（未設定
    好 token 或群組 ID 時直接回傳 False，不會拋例外中斷呼叫端的其他處理）。"""
    if not is_configured():
        return False
    try:
        from linebot.models import TextSendMessage

        line_bot_api.push_message(LINE_NOTIFY_GROUP_ID, TextSendMessage(text=text))
        return True
    except Exception as e:
        print(f"[管理部系統] LINE 群組推播失敗：{e}")
        return False
