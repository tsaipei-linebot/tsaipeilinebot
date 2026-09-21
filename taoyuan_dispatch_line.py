"""桃園所派遣專屬的 LINE 官方帳號——跟招募機器人（沛沛）、配送部、管理部
完全獨立的第四個 LINE Messaging API Channel（2026-09-21 新增，桃園所專區
Phase 2）。這裡刻意只做「客戶端」這一件事：實例化 LineBotApi/WebhookHandler、
提供 push_message()。webhook 路由本身跟訊息處理邏輯在
`taoyuan_dispatch_webhook_routes.py`／`taoyuan_dispatch_bot.py`，拆開的
理由跟 `management/line_bot.py` 拆給 `management/routes/line_webhook_routes.py`
一樣：這支檔案不需要知道任何指令解析邏輯，只單純是這組帳號的客戶端。

完全不接招募機器人那套 AI 對話邏輯——桃園所人員在這個帳號傳的每一句話都
是「綁定」「需求列表」「報名」這類固定指令，不需要、也不應該被誤判成
求職者在問工作機會。
"""
from linebot import LineBotApi, WebhookHandler

from config import TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN, TAOYUAN_DISPATCH_LINE_CHANNEL_SECRET

line_bot_api = LineBotApi(TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN) if TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(TAOYUAN_DISPATCH_LINE_CHANNEL_SECRET) if TAOYUAN_DISPATCH_LINE_CHANNEL_SECRET else None


def is_configured() -> bool:
    return bool(TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN)


def push_message(target_id: str, text: str) -> bool:
    """推播一則文字訊息給指定的 LINE 使用者（審核核准/駁回時用）。回傳
    是否有實際送出——帳號沒設定好 token，或沒有目標 ID（例如這個人員從
    來沒在這個帳號綁定過、line_user_id 是空字串）時直接回傳 False，不會
    拋例外中斷呼叫端（審核狀態還是要照樣寫入，不能因為推播失敗就整個
    請求失敗）。"""
    if not line_bot_api or not target_id:
        return False
    try:
        from linebot.models import TextSendMessage

        line_bot_api.push_message(target_id, TextSendMessage(text=text))
        return True
    except Exception as e:
        print(f"[桃園所專區] LINE 推播失敗：{e}")
        return False
