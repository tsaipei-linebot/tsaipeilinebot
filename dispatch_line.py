"""多所派遣媒合共用的 LINE 官方帳號客戶端層（2026-09-22 重構自
`taoyuan_dispatch_line.py`）：每個所有自己獨立的 LINE Messaging API
Channel（跟招募機器人、配送部、管理部完全獨立），各自的 Channel
Token/Secret 環境變數名稱記在 `dispatch_sites.py`，這裡依所別代碼建立
各自的 LineBotApi/WebhookHandler 實例並快取起來。哪個所還沒設定好
環境變數，`is_configured(site)` 回傳 False，這個所的 webhook 直接停用
（回 503），不會拖垮其他所或其他子系統。

這裡刻意只做「客戶端」這一件事：實例化 LineBotApi/WebhookHandler、提供
`push_message()`。webhook 路由本身跟訊息處理邏輯在
`dispatch_webhook_routes.py`／`dispatch_bot.py`，拆開的理由跟
`management/line_bot.py` 拆給
`management/routes/line_webhook_routes.py` 一樣：這支檔案不需要知道任何
指令解析邏輯，只單純是各所帳號的客戶端。

完全不接招募機器人那套 AI 對話邏輯——各所人員在這些帳號傳的每一句話都
是「綁定」「需求列表」「報名」這類固定指令，不需要、也不應該被誤判成
求職者在問工作機會。
"""
import os

from linebot import LineBotApi, WebhookHandler

from dispatch_sites import DISPATCH_SITES

_line_bot_apis = {}
_handlers = {}

for _code, _site in DISPATCH_SITES.items():
    _token = os.getenv(_site["line_token_env"])
    _secret = os.getenv(_site["line_secret_env"])
    _line_bot_apis[_code] = LineBotApi(_token) if _token else None
    _handlers[_code] = WebhookHandler(_secret) if _secret else None


def get_line_bot_api(site: str):
    return _line_bot_apis.get(site)


def get_handler(site: str):
    return _handlers.get(site)


def is_configured(site: str) -> bool:
    return bool(_line_bot_apis.get(site))


def push_message(site: str, target_id: str, text: str) -> bool:
    """推播一則文字訊息給指定 LINE 使用者（審核核准/駁回時用）。回傳
    是否有實際送出——這個所沒設定好 token，或沒有目標 ID（例如這個人員
    從來沒在這個所的帳號綁定過、line_user_id 是空字串）時直接回傳
    False，不會拋例外中斷呼叫端（審核狀態還是要照樣寫入，不能因為推播
    失敗就整個請求失敗）。"""
    api = _line_bot_apis.get(site)
    if not api or not target_id:
        return False
    try:
        from linebot.models import TextSendMessage

        api.push_message(target_id, TextSendMessage(text=text))
        return True
    except Exception as e:
        print(f"[派遣媒合/{site}] LINE 推播失敗：{e}")
        return False
