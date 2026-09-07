"""用公司現有的 LINE 官方帳號推播「文件即將到期」提醒。

沿用 main.py／config.py 已經在用的 LINE_CHANNEL_ACCESS_TOKEN（跟招募機器人
共用同一個官方帳號），delivery 子系統這邊只負責「推播訊息給指定的同仁/群組」，
不處理收訊息（那是 main.py 既有 webhook 的事）。LineBotApi 延遲到真正要推播
時才初始化，避免只是匯入這個模組就需要 LINE 的憑證。
"""
from config import LINE_CHANNEL_ACCESS_TOKEN
from delivery.config import LINE_REMINDER_TARGET_ID

_line_bot_api = None


def is_configured() -> bool:
    return bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_REMINDER_TARGET_ID)


def _get_client():
    global _line_bot_api
    if _line_bot_api is None:
        from linebot import LineBotApi

        _line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    return _line_bot_api


def push_reminder_message(text: str) -> bool:
    """推播一則文字訊息給設定好的同仁/群組。回傳是否有實際送出（未設定好
    目標或 token 時直接回傳 False，不會拋例外中斷呼叫端的其他處理）。"""
    if not is_configured():
        return False
    try:
        from linebot.models import TextSendMessage

        _get_client().push_message(LINE_REMINDER_TARGET_ID, TextSendMessage(text=text))
        return True
    except Exception as e:
        print(f"[配送部系統] LINE 到期提醒推播失敗：{e}")
        return False
