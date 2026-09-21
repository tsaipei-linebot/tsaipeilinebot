"""桃園所派遣專屬 LINE 官方帳號的 Webhook（2026-09-21 新增，桃園所專區
Phase 2）。收到文字訊息事件時交給 `taoyuan_dispatch_bot.handle_message()`
解析指令、讀寫資料庫，再把回傳的文字用 `reply_message()` 回覆——跟
`management/routes/line_webhook_routes.py` 是同一套拆法（webhook 路由
只管簽章驗證跟轉交，訊息處理邏輯都在另一個檔案）。

完全沒有自動對話/AI 邏輯，理由見 `taoyuan_dispatch_line.py` 開頭說明。
"""
from fastapi import APIRouter, Header, HTTPException, Request
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from starlette.concurrency import run_in_threadpool

import taoyuan_dispatch_bot
from taoyuan_dispatch_line import handler, line_bot_api

router = APIRouter()


@router.post("/taoyuan-dispatch/line/callback")
async def taoyuan_dispatch_line_callback(request: Request, x_line_signature: str = Header(None)):
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


if handler:

    @handler.add(MessageEvent, message=TextMessage)
    def _reply_to_taoyuan_dispatch_message(event):
        reply = taoyuan_dispatch_bot.handle_message(event.source.user_id, event.message.text or "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
