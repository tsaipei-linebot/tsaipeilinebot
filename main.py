from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage

from config import (
    LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET,
    TEST_LINE_CHANNEL_ACCESS_TOKEN, TEST_LINE_CHANNEL_SECRET
)
from handlers.message_handler import process_user_message

app = FastAPI(
    title="Tsaipei AI Recruitment Consultant - Legal & Formatted Detail Engine - V12 (Modular)",
    version="12.0.0"
)

# LINE 官方帳號客戶端實例化[cite: 2]
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

test_line_bot_api = LineBotApi(TEST_LINE_CHANNEL_ACCESS_TOKEN) if TEST_LINE_CHANNEL_ACCESS_TOKEN else None
test_handler = WebhookHandler(TEST_LINE_CHANNEL_SECRET) if TEST_LINE_CHANNEL_SECRET else None

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "Tsaipei AI Recruitment Consultant (PeiPei V12 Modular Engine) is running."
    }

# ==========================================
# 測試環境 Webhook 路由[cite: 2]
# ==========================================
@app.post("/test-callback")
async def test_callback(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")
    body = await request.body()
    try:
        test_handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@test_handler.add(MessageEvent, message=TextMessage)
def handle_test_message(event):
    process_user_message(event, test_line_bot_api)

# ==========================================
# 正式環境 Webhook 路由[cite: 2]
# ==========================================
@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    process_user_message(event, line_bot_api)