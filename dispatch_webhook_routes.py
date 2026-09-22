"""多所派遣專屬 LINE 官方帳號的 Webhook（2026-09-22 重構自
`taoyuan_dispatch_webhook_routes.py`）。網址依所別代碼區分
（`/dispatch/{site}/line/callback`），收到文字訊息事件時交給
`dispatch_bot.handle_message(site, ...)` 解析指令、讀寫資料庫，再把
回傳的文字用 `reply_message()` 回覆——跟
`management/routes/line_webhook_routes.py` 是同一套拆法（webhook 路由
只管簽章驗證跟轉交，訊息處理邏輯都在另一個檔案）。

每個所各自獨立的 `WebhookHandler` 實例（見 `dispatch_line.py`），
`@handler.add(...)` 這個裝飾器是綁在特定實例上的，所以要對
`dispatch_sites.DISPATCH_SITES` 裡每一個有設定好環境變數的所，各自註冊
一次事件處理函式（`_make_reply_handler()`），不能只註冊一次共用。

完全沒有自動對話/AI 邏輯，理由見 `dispatch_line.py` 開頭說明。
"""
from fastapi import APIRouter, Header, HTTPException, Request
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from starlette.concurrency import run_in_threadpool

import dispatch_bot
from dispatch_line import get_handler, get_line_bot_api
from dispatch_sites import DISPATCH_SITES

router = APIRouter()


@router.post("/dispatch/{site}/line/callback")
async def dispatch_line_callback(site: str, request: Request, x_line_signature: str = Header(None)):
    handler = get_handler(site)
    if not handler:
        raise HTTPException(status_code=503, detail="LINE channel not configured")
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = await request.body()
    try:
        await run_in_threadpool(handler.handle, body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"


def _make_reply_handler(site: str):
    def _reply(event):
        reply = dispatch_bot.handle_message(site, event.source.user_id, event.message.text or "")
        # 空字串代表這則訊息沒有觸發任何派遣指令關鍵字（可能是求職者在問
        # 工作——這幾個所的 LINE 官方帳號跟求職者共用），這時候完全不回覆，
        # replyToken 自然過期、不會有任何副作用，讓 LINE 內建的自動回應
        # 訊息跟專員接手（見 dispatch_bot.py 開頭的說明）。
        if not reply:
            return
        get_line_bot_api(site).reply_message(event.reply_token, TextSendMessage(text=reply))

    return _reply


for _code in DISPATCH_SITES:
    _handler = get_handler(_code)
    if _handler:
        _handler.add(MessageEvent, message=TextMessage)(_make_reply_handler(_code))
