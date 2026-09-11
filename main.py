import hmac
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage

import accounts_routes
import login_routes
import portal_routes
from config import (
    LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET,
    TEST_LINE_CHANNEL_ACCESS_TOKEN, TEST_LINE_CHANNEL_SECRET,
    LOAD_TEST_SECRET, FACTORY_WATCH_TRIGGER_SECRET, DAILY_REPORT_TRIGGER_SECRET, DAILY_REPORT_ENABLED,
    DEFAULT_RESUME_URLS
)
from delivery.config import SESSION_SECRET_KEY
from handlers.message_handler import process_user_message, process_image_message
from delivery.app import delivery_app
from management.app import management_app
from hr.app import hr_app
from services.factory_watch_service import run_weekly_scan
from services.daily_report_service import run_daily_report
from services.session_service import db as _firestore_db, SESSIONS_COLLECTION as _SESSIONS_COLLECTION
from services.notion_service import fetch_jobs_data, fetch_faqs_data, sanitize_uri, record_resume_click
from services.ai_service import query_gemini_ai

app = FastAPI(
    title="Tsaipei AI Recruitment Consultant - Legal & Formatted Detail Engine - V12 (Modular)",
    version="12.0.0"
)

# 根 app 自己也裝一份 SessionMiddleware（跟 delivery_app/management_app
# 用同一組 secret key + cookie 名稱），這樣掛在根 app 上的 /accounts
# （帳號權限管理）才讀得到跟 /delivery、/management 共用的同一顆登入
# session cookie，不用另外登入一次。
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="delivery_session",
    max_age=14 * 24 * 3600,
)
app.include_router(accounts_routes.router, prefix="/accounts")
# /login、/logout：全平台共用的登入頁（見 login_routes.py）。
app.include_router(login_routes.router)
# /portal：登入後才看得到的內部系統入口頁，加上職缺維護系統的免登入銜接
# （見 portal_routes.py）。
app.include_router(portal_routes.router)

# 配送部系統、管理部系統、人資專區：各自獨立子系統（自己的路由/資料表，
# 共用同一顆登入 session cookie），掛在 /delivery、/management、/hr 底下，
# 跟上面 LINE 招募機器人的 webhook 路由完全分開，互不影響。
app.mount("/delivery", delivery_app)
app.mount("/management", management_app)
app.mount("/hr", hr_app)

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
# 啟動時預熱招募機器人會用到的三個外部服務（Firestore／Notion／Vertex AI Gemini）
#
# 背景：這個 Cloud Run 服務是招募機器人跟配送部/管理部/人資等系統共用的，
# 任何一個團隊推送到 main 都會讓整個服務重新部署、產生一支全新的執行版本。
# 就算設定了 min-instances=1，新版本的容器裡，程式跟這三個外部服務之間的
# 連線都還沒真的建立過（連線是「第一次真的要送資料時」才會去握手），所以
# 「重新部署後、第一個真的傳訊息的求職者」會多負擔這段連線建立的時間，
# 曾經在測試頻道實測到因此觸發 15 秒同步等待逾時、改用背景補發的情況
# （詳見 HANDOFF.md「上線前流量/正確性盤點」章節）。
#
# 這裡在服務真正開始接受請求「之前」，就先把這三個連線都跑過一次：
# FastAPI 的 startup 事件會在應用程式開始處理任何請求前執行完畢，所以無論
# 是重新部署或是流量升載多開一台執行個體，第一個使用者都不會撞到冷連線。
# 任何一步失敗都只記 log、不讓服務因此啟動失敗——最壞情況只是退回「沒有
# 預熱」的舊行為，不會讓整個服務（包含其他子系統）掛掉。
#
# 三個連線彼此完全獨立，改用執行緒池同時跑，而不是依序一個接一個執行：
# 這樣總預熱時間只取決於「最慢的那一個」，而不是三個時間加總——既然這段
# 程式碼存在的目的就是要縮短容器啟動後、第一個真人使用者撞到冷連線的風險
# 窗口，讓三步同時跑而不是排隊執行，才能把這個窗口壓到最小。
# ==========================================
def _warmup_firestore():
    _firestore_db.collection(_SESSIONS_COLLECTION).document("__warmup__").get()
    print("[啟動預熱] Firestore 連線正常")


def _warmup_notion():
    fetch_jobs_data()
    fetch_faqs_data()
    print("[啟動預熱] Notion 連線正常，職缺／FAQ 快取已預先載入")


def _warmup_vertex_ai():
    query_gemini_ai("你好，這是服務啟動時的暖機測試，請直接回覆「收到」即可。")
    print("[啟動預熱] Vertex AI Gemini 連線正常")


@app.on_event("startup")
def _warmup_recruitment_bot_dependencies():
    warmup_steps = [
        ("Firestore", _warmup_firestore),
        ("Notion", _warmup_notion),
        ("Vertex AI Gemini", _warmup_vertex_ai),
    ]
    with ThreadPoolExecutor(max_workers=len(warmup_steps)) as executor:
        futures = {executor.submit(func): name for name, func in warmup_steps}
        for future in futures:
            name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"[啟動預熱] {name} 連線失敗（不影響服務啟動）: {e}")


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

def handle_test_message(event):
    process_user_message(event, test_line_bot_api)

def handle_test_image_message(event):
    process_image_message(event, test_line_bot_api)

# ==========================================
# 正式環境 Webhook 路由[cite: 2]
# ==========================================
@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    return await _handle_webhook(request, x_line_signature, handler)

def handle_message(event):
    process_user_message(event, line_bot_api)

def handle_image_message(event):
    process_image_message(event, line_bot_api)

# 用 .add(...) 手動註冊事件處理函式，而不是用 @handler.add(...) 裝飾器語法：
# handler/test_handler 在對應的 LINE_CHANNEL_SECRET／TEST_LINE_CHANNEL_SECRET
# 環境變數沒設定時會是 None（見上面的實例化），裝飾器語法在「模組匯入當下」
# 就會呼叫 None.add(...) 而丟出 AttributeError，導致整個 Cloud Run 服務（不只
# 招募機器人，這個 app 上還掛了 delivery/management/hr 等其他子系統）啟動失敗、
# 整個服務起不來。改成先定義好函式，匯入時才檢查 handler 是否存在再註冊，
# 就算漏設某個 LINE 密鑰，也只會讓對應的 webhook 路由收不到訊息，不會拖垮
# 整個服務。
if test_handler:
    test_handler.add(MessageEvent, message=TextMessage)(handle_test_message)
    test_handler.add(MessageEvent, message=ImageMessage)(handle_test_image_message)

if handler:
    handler.add(MessageEvent, message=TextMessage)(handle_message)
    handler.add(MessageEvent, message=ImageMessage)(handle_image_message)

# ==========================================
# 職缺卡片「填寫線上履歷」按鈕的轉址端點（記錄點擊後再轉去外部履歷網站）
#
# LINE 的 uri 類型按鈕點下去是直接開外部瀏覽器，完全不會觸發任何 webhook
# 事件，機器人原本沒辦法知道誰點了這顆按鈕。做法是讓按鈕先連到我們自己這支
# 服務（見 services/flex_service.py 的 SERVICE_BASE_URL 判斷），這裡記錄下
# 「誰、點了哪個職缺」之後，再用 302 轉址到真正的履歷網站，求職者感覺不出
# 差異（只多一次幾乎瞬間完成的伺服器轉址）。
#
# `type` 只接受 DEFAULT_RESUME_URLS 這份白名單裡的 key（Spx/Service/
# Manufacture），不接受外部傳來的完整網址當轉址目標，避免被拿去偽造成開放
# 重導向（open redirect）的釣魚連結。記錄失敗（例如 Notion 沒設定、逾時）
# 絕對不能擋住轉址——求職者永遠都要能順利到達履歷網站。
# ==========================================
@app.get("/apply-click")
def apply_click_redirect(uid: str = "", type: str = "", job: str = ""):
    dest = sanitize_uri(DEFAULT_RESUME_URLS.get(type, DEFAULT_RESUME_URLS["Manufacture"]))

    # 這整段記錄點擊的邏輯包在 try/except 裡，是刻意的最後一道防線：
    # record_resume_click() 自己內部雖然已經有 try/except（見
    # services/notion_service.py），但這裡多包一層可以確保就算未來那支
    # 函式的防護出現漏洞，也不會連帶讓求職者連不到履歷網站——轉址永遠是
    # 第一優先，記錄點擊只是附加價值。
    if uid:
        try:
            display_name = ""
            for api in (line_bot_api, test_line_bot_api):
                if api is None:
                    continue
                try:
                    display_name = api.get_profile(uid).display_name
                    break
                except Exception:
                    continue
            record_resume_click(uid, display_name, job, type)
        except Exception as e:
            print(f"[履歷點擊記錄失敗，不影響轉址]: {e}")

    return RedirectResponse(url=dest, status_code=302)

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
    """頂替真正的 LineBotApi：process_user_message() 同步路徑會呼叫 reply_message()，
    超過 AI_DECISION_SYNC_TIMEOUT_SECONDS 的長尾請求則會在背景執行緒算完後改呼叫
    push_message()（見 handlers/message_handler.py 的限時同步等待架構）。這兩個方法
    都只是記錄下來、不對外發送任何真實請求；push_message 一樣要頂替，否則長尾請求
    背景補發時會因為 stub 沒有這個方法而丟出 AttributeError（雖然會被上層的保底
    try/except 攔住不影響服務，但會在 Cloud Run log 裡持續噴出無意義的錯誤，混淆
    之後想從 log 判斷背景補發是否真的成功送達的判斷）。"""

    def __init__(self):
        self.last_call = None
        self.last_push_call = None

    def reply_message(self, reply_token, messages):
        self.last_call = {"reply_token": reply_token, "messages": messages}

    def push_message(self, user_id, messages):
        self.last_push_call = {"user_id": user_id, "messages": messages}


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
    # 比照 /internal/factory-watch/run、/internal/daily-report/run 改用
    # hmac.compare_digest 做固定時間比對，避免用 != 直接比較字串時，理論上
    # 能被拿來做時間旁道攻擊猜出密鑰（這支端點一旦密鑰外流，任何人都能拿去
    # 呼叫真正的 Vertex AI/Notion/Firestore，等於免費幫別人燒你的帳單）。
    if not LOAD_TEST_SECRET or not x_load_test_secret or not hmac.compare_digest(
        x_load_test_secret, LOAD_TEST_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    fake_event = _FakeEvent(payload.user_id, payload.text)
    stub_api = _StubLineBotApi()

    start = time.monotonic()
    # bypass_staffed_hours_guard=True：壓力測試本來就是要測 Notion/Firestore/
    # Gemini 那條路徑撐不撐得住，不該因為剛好在同仁上班時段執行就被日夜接力
    # 的守門邏輯擋掉（見 handlers/message_handler.py 的 _is_staffed_hours()）。
    await run_in_threadpool(process_user_message, fake_event, stub_api, True)
    elapsed = time.monotonic() - start

    return {
        "elapsed_seconds": round(elapsed, 3),
        "reply": _summarize_reply(stub_api.last_call["messages"] if stub_api.last_call else None),
    }


# ==========================================
# 每週新工廠登記監控：由 Cloud Scheduler 定期呼叫觸發，
# 不對外公開，用共用密鑰驗證避免被任意觸發。
# ==========================================
@app.post("/internal/factory-watch/run")
async def trigger_factory_watch(x_factory_watch_secret: str = Header(None)):
    if not FACTORY_WATCH_TRIGGER_SECRET or not x_factory_watch_secret or not hmac.compare_digest(
        x_factory_watch_secret, FACTORY_WATCH_TRIGGER_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    summary = await run_in_threadpool(run_weekly_scan, line_bot_api)
    return summary


# ==========================================
# 每日健康報告／FAQ 週報：由 Cloud Scheduler 每天呼叫一次觸發，不對外公開，
# 用共用密鑰驗證避免被任意觸發（見 HANDOFF.md「監控與告警機制」）。
# DAILY_REPORT_ENABLED 是總開關，預設關閉：就算 Cloud Scheduler 已經設定好、
# 每天照樣會打這支端點，只要沒開這個環境變數，就只回傳「功能尚未啟用」、
# 不會真的去讀 log／推播，等使用者確定要切換到正式頻道才手動打開。
# ==========================================
@app.post("/internal/daily-report/run")
async def trigger_daily_report(x_daily_report_secret: str = Header(None)):
    if not DAILY_REPORT_TRIGGER_SECRET or not x_daily_report_secret or not hmac.compare_digest(
        x_daily_report_secret, DAILY_REPORT_TRIGGER_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not DAILY_REPORT_ENABLED:
        return {"enabled": False, "message": "DAILY_REPORT_ENABLED 尚未開啟，本次不執行"}

    summary = await run_in_threadpool(run_daily_report, line_bot_api)
    return summary
