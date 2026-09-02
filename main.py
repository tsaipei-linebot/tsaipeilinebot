from fastapi import FastAPI, Request, Header, HTTPException
from starlette.concurrency import run_in_threadpool
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage

from config import (
    LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET,
    TEST_LINE_CHANNEL_ACCESS_TOKEN, TEST_LINE_CHANNEL_SECRET
)
from handlers.message_handler import process_user_message, process_image_message

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
# 共用 Webhook 處理邏輯
# 讀取 body / 驗證 header 維持輕量的 async 寫法；
# 真正耗時的 webhook_handler.handle()（內部會觸發 Notion / Firestore / Gemini
# 等同步網路 I/O）改用 run_in_threadpool 丟進獨立執行緒執行，
# 避免卡住 FastAPI 的 event loop，讓多個使用者的請求可以平行處理。
# ==========================================
async def _handle_webhook(request: Request, x_line_signature: str, webhook_handler: WebhookHandler) -> str:
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = await request.body()

    try:
        await run_in_threadpool(webhook_handler.handle, body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

# ==========================================
# 測試環境 Webhook 路由[cite: 2]
# ==========================================
@app.post("/test-callback")
async def test_callback(request: Request, x_line_signature: str = Header(None)):
    return await _handle_webhook(request, x_line_signature, test_handler)

@test_handler.add(MessageEvent, message=TextMessage)
def handle_test_message(event):
    process_user_message(event, test_line_bot_api)

@test_handler.add(MessageEvent, message=ImageMessage)
def handle_test_image_message(event):
    process_image_message(event, test_line_bot_api)

# ==========================================
# 正式環境 Webhook 路由[cite: 2]
# ==========================================
@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    return await _handle_webhook(request, x_line_signature, handler)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    process_user_message(event, line_bot_api)

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    process_image_message(event, line_bot_api)
