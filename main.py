import time
import uuid
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage

from config import (
    LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET,
    TEST_LINE_CHANNEL_ACCESS_TOKEN, TEST_LINE_CHANNEL_SECRET,
    LOAD_TEST_SECRET
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

# ==========================================
# 內部壓力測試端點（預設關閉，僅供壓力測試腳本使用）
#
# 目的：驗證 Notion / Firestore / Vertex AI 這幾個真實系統在高並發下撐不撐得住，
# 但不需要、也不應該真的把回覆送給真實求職者。設計上完全繞過 LINE 的
# reply_message API（用 _StubLineBotApi 頂替），所以：
# 1. 不需要真實的 LINE reply_token（那個只有真人傳訊息時 LINE 才會核發，
#    腳本無法偽造），可以無限次重複呼叫
# 2. 不會有任何真實使用者收到測試訊息
# 3. 但 process_user_message() 裡其餘的邏輯（Notion 讀取、Firestore
#    session 讀寫、Gemini 決策呼叫）完全是真的，跟正式流量走一樣的路徑，
#    量測出來的延遲/錯誤率才有參考價值
#
# 安全機制：必須帶對 X-Load-Test-Secret header，值要跟 Cloud Run 環境變數
# LOAD_TEST_SECRET 完全一致才會受理；沒有設定 LOAD_TEST_SECRET（預設情況）
# 時一律回傳 403，等同這個端點不存在。
# ==========================================
class LoadTestMessageRequest(BaseModel):
    user_id: str
    text: str


class _StubLineBotApi:
    """頂替真正的 LineBotApi：process_user_message() 只會呼叫到 reply_message()
    這一個方法，這裡直接記錄下來、不對外發送任何請求。"""

    def __init__(self):
        self.last_call = None

    def reply_message(self, reply_token, messages):
        self.last_call = {"reply_token": reply_token, "messages": messages}


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text


class _FakeSource:
    def __init__(self, user_id: str):
        self.user_id = user_id


class _FakeEvent:
    """模擬 line-bot-sdk 的 MessageEvent，只需要 process_user_message() 實際
    會讀取的三個屬性：reply_token、source.user_id、message.text。"""

    def __init__(self, user_id: str, text: str):
        # 隨便一組不重複的字串即可，不會真的拿去呼叫 LINE API，
        # 只要不是 process_user_message 特別排除的兩組驗證用假 token 即可。
        self.reply_token = f"loadtest-{uuid.uuid4()}"
        self.source = _FakeSource(user_id)
        self.message = _FakeMessage(text)


def _summarize_reply(messages) -> list:
    """把 _StubLineBotApi 攔下來的回覆內容整理成方便閱讀的摘要，
    讓壓力測試腳本除了量測時間，也能順便檢查 AI 回覆是否合理。"""
    if not messages:
        return []
    if not isinstance(messages, list):
        messages = [messages]
    summary = []
    for m in messages:
        entry = {"type": type(m).__name__}
        text = getattr(m, "text", None)
        if text:
            entry["text"] = text
        summary.append(entry)
    return summary


@app.post("/internal/load-test-message")
async def load_test_message(payload: LoadTestMessageRequest, x_load_test_secret: str = Header(None)):
    if not LOAD_TEST_SECRET or x_load_test_secret != LOAD_TEST_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    fake_event = _FakeEvent(payload.user_id, payload.text)
    stub_api = _StubLineBotApi()

    start = time.monotonic()
    await run_in_threadpool(process_user_message, fake_event, stub_api)
    elapsed = time.monotonic() - start

    return {
        "elapsed_seconds": round(elapsed, 3),
        "reply": _summarize_reply(stub_api.last_call["messages"] if stub_api.last_call else None),
    }
