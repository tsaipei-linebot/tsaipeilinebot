import os
import re
import time
import datetime
import pytz
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google import genai

# 載入 .env 環境變數
load_dotenv()

app = FastAPI(title="Tsaipei LineBot Production", version="4.2.0")

# ==========================================
# 1. 環境設定與金鑰
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

TEST_LINE_CHANNEL_ACCESS_TOKEN = os.getenv("TEST_LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN)
TEST_LINE_CHANNEL_SECRET = os.getenv("TEST_LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET)
test_line_bot_api = LineBotApi(TEST_LINE_CHANNEL_ACCESS_TOKEN) if TEST_LINE_CHANNEL_ACCESS_TOKEN else None
test_handler = WebhookHandler(TEST_LINE_CHANNEL_SECRET) if TEST_LINE_CHANNEL_SECRET else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "材霈_招募客服自動化資料庫")
OFFICIAL_WEBSITE_BASE = os.getenv("OFFICIAL_WEBSITE_BASE", "https://tsaipei.netlify.app")

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("[系統提示] Gemini AI 客戶端初始化成功！")
    except Exception as e:
        print(f"[系統警告] Gemini AI 初始化失敗: {e}")

# ==========================================
# 2. Google Sheets 資料庫直連模組 (自動相容欄位)
# ==========================================
CACHE_TTL = 30  # 30 秒快取，確保資料表修改後快速生效
_cached_jobs, _last_jobs_fetch = None, 0
_cached_faqs, _last_faqs_fetch = None, 0
_gspread_client = None

def get_sheets_client():
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client.open(SPREADSHEET_NAME)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    possible_paths = [
        "/etc/secrets/service_account.json",
        "service_account.json",
        os.path.join(os.path.dirname(__file__), "service_account.json")
    ]
    key_path = None
    for p in possible_paths:
        if os.path.exists(p):
            key_path = p
            break
            
    if not key_path:
        raise FileNotFoundError("找不到 service_account.json 金鑰檔案！請檢查 Render Secret Files 設定。")
        
    creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
    _gspread_client = gspread.authorize(creds)
    return _gspread_client.open(SPREADSHEET_NAME)

def fetch_jobs_data() -> list:
    """直接連線 Google 試算表本體讀取職缺，容錯所有欄位名稱"""
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    active_jobs = []
    try:
        sheet = get_sheets_client()
        # 尋找職缺分頁
        ws = None
        for name in ["職缺清單", "Jobs_職缺資料庫", "職缺列表", "Jobs", "工作表1"]:
            try:
                ws = sheet.worksheet(name)
                break
            except Exception:
                continue
        if not ws:
            ws = sheet.get_worksheet(0)

        all_values = ws.get_all_values()
        if len(all_values) >= 2:
            headers = [str(h).strip() for h in all_values[0]]
            
            stop_keywords = ["停招", "暫停", "額滿", "關閉", "下架", "結束", "否", "滿", "pause", "close"]

            for row_idx, row in enumerate(all_values[1:], start=2):
                if not any(str(cell).strip() for cell in row):
                    continue  # 跳過完全空白列

                # 建立欄位字典
                row_dict = {}
                for i, val in enumerate(row):
                    h_name = headers[i] if i < len(headers) and headers[i] else f"COL_{i}"
                    row_dict[h_name] = str(val).strip()

                # 自動探索欄位值
                title = ""
                for k, v in row_dict.items():
                    if any(t_kw in k for t_kw in ["職缺名稱", "職稱", "職務名稱", "工作名稱", "title"]):
                        title = v
                        break
                if not title and len(row) >= 2:
                    title = row[1].strip()  # 預設 B 欄為職稱

                # 探索狀態
                status = ""
                for k, v in row_dict.items():
                    if any(s_kw in k for s_kw in ["狀態", "職缺狀態", "招募狀態", "status"]):
                        status = v
                        break

                # 只要有職稱且未標註停招，全部載入
                if title and not any(stop_kw in status for stop_kw in stop_keywords):
                    row_dict["_parsed_title"] = title
                    row_dict["_raw_row_text"] = " ".join([str(c).strip() for c in row])
                    active_jobs.append(row_dict)

        print(f"[試算表載入成功] 工作表「{ws.title}」共載入 {len(active_jobs)} 筆招募中職缺！")
        _cached_jobs = active_jobs
        _last_jobs_fetch = now
        return active_jobs
    except Exception as e:
        print(f"[讀取試算表職缺失敗]: {e}")
        return _cached_jobs or []

def fetch_faqs_data() -> list:
    global _cached_faqs, _last_faqs_fetch
    now = time.time()
    if _cached_faqs is not None and (now - _last_faqs_fetch < CACHE_TTL):
        return _cached_faqs

    faqs = []
    try:
        sheet = get_sheets_client()
        ws = None
        for name in ["FAQ_客服問答", "FAQ知識庫", "FAQ", "問答清單"]:
            try:
                ws = sheet.worksheet(name)
                break
            except Exception:
                continue
        if ws:
            values = ws.get_all_values()
            if len(values) >= 2:
                headers = [str(h).strip() for h in values[0]]
                for r in values[1:]:
                    row_dict = {headers[i]: str(r[i]).strip() for i in range(min(len(headers), len(r)))}
                    faqs.append(row_dict)
        _cached_faqs = faqs
        _last_faqs_fetch = now
    except Exception as e:
        print(f"[FAQ 讀取錯誤]: {e}")
    return _cached_faqs or []

# ==========================================
# 3. 雙按鈕 + 4 標籤 Flex 卡片
# ==========================================
def create_job_flex_card(jobs: list, user_id: str) -> FlexSendMessage:
    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "leave": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "pay": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:10]:
        job_id = str(job.get("職缺代碼") or job.get("職缺編號") or "JOB").strip()
        job_title = str(job.get("_parsed_title") or job.get("職缺名稱(對外)") or job.get("職缺名稱") or "優質職缺").strip()
        
        county = str(job.get("縣市") or "").strip()
        district = str(job.get("行政區") or "").strip()
        location = f"{county} {district}".strip() or "桃園市/全區"
        
        salary = str(job.get("薪資") or job.get("薪資待遇") or "時薪 218~253元/月領/週領").strip()
        shift = str(job.get("班別") or "").strip()
        leave = str(job.get("休假制度") or job.get("休假方式") or "").strip()
        job_type = str(job.get("全職/兼職") or job.get("全/兼職") or "").strip()
        pay_method = str(job.get("領薪方式") or "").strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift[:8], "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]})
        if leave:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["leave"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": leave[:8], "size": "xxs", "color": badge_styles["leave"]["text"], "weight": "bold"}]})
        if job_type:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["type"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": job_type[:8], "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]})
        if pay_method:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["pay"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": pay_method[:8], "size": "xxs", "color": badge_styles["pay"]["text"], "weight": "bold"}]})

        raw_desc = str(job.get("工作內容(對外)") or job.get("工作內容與條件") or job.get("工作需求") or "").strip()
        clean_desc = re.sub(r'[\r\n\t]+', ' ', raw_desc).strip()
        if len(clean_desc) > 50:
            clean_desc = clean_desc[:50] + "..."
        if not clean_desc:
            clean_desc = "歡迎點擊下方按鈕瞭解詳細說明與應徵！"
            
        website_job_url = "https://tsaipei.netlify.app/#jobs"
        raw_resume_url = str(job.get("線上履歷網址") or job.get("線上履歷連結") or "").strip()
        if raw_resume_url.startswith("http://") or raw_resume_url.startswith("https://"):
            separator = "&" if "?" in raw_resume_url else "?"
            apply_link = f"{raw_resume_url}{separator}job_id={job_id}&line_id={user_id}"
        else:
            apply_link = "https://tsaipei.netlify.app/#jobs"

        body_contents = [
            {"type": "text", "text": "🎯 材霈推薦職缺", "weight": "bold", "color": "#1DB446", "size": "xs"},
            {"type": "text", "text": job_title, "weight": "bold", "size": "lg", "margin": "xs", "wrap": True}
        ]
        
        if tags_contents:
            body_contents.append({"type": "box", "layout": "horizontal", "spacing": "xs", "margin": "sm", "contents": tags_contents})
            
        body_contents.extend([
            {"type": "separator", "margin": "md"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": f"📍 地點：{location}", "size": "sm", "color": "#444444", "wrap": True},
                    {"type": "text", "text": f"💰 待遇：{salary}", "size": "sm", "color": "#D32F2F", "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"📝 說明：{clean_desc}", "size": "xs", "color": "#777777", "wrap": True, "margin": "xs"}
                ]
            }
        ])

        bubble = {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "secondary",
                        "color": "#F0F0F0",
                        "height": "sm",
                        "action": {"type": "uri", "label": "🌐 查看官網簡章", "uri": website_job_url}
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#00B900",
                        "height": "sm",
                        "action": {"type": "uri", "label": "📄 填寫線上履歷", "uri": apply_link}
                    }
                ]
            }
        }
        bubbles.append(bubble)
        
    return FlexSendMessage(alt_text=f"為您找到 {len(bubbles)} 筆熱門職缺！", contents={"type": "carousel", "contents": bubbles})

# ==========================================
# 4. 核心對話處理邏輯 (全文檢索 + 同義詞)
# ==========================================
SYNONYM_GROUPS = [
    ["理貨", "揀貨", "包裝", "倉管", "倉儲", "物流", "加工", "理貨員", "揀貨員", "物流士", "momo"],
    ["作業員", "技術員", "品檢", "組裝", "測試", "產線", "助理", "操作員", "工廠"],
    ["晚班", "夜班", "大夜", "小夜", "夜間", "大夜班"],
    ["早班", "日班", "晨班", "日間"],
    ["中班", "午後班"],
    ["司機", "送貨", "外送", "物流司機", "駕駛", "貨車"],
    ["週休", "周休", "排休", "見紅休", "月休八天", "休六日"],
    ["週領", "周領", "日領", "預支", "借支", "領錢"],
    ["兼職", "工讀", "打工", "pt", "PT", "短期"],
    ["全職", "正職", "常態"]
]

def process_user_message(event, target_line_bot_api: LineBotApi):
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」")

    clean_msg = re.sub(r'[？\?！!。，,\s]+', '', raw_msg).replace("台", "臺").lower()
    for filler in ["有嗎", "有沒有", "我想找", "想找", "我要找", "請問", "可以推薦", "推薦", "的工作", "工作", "職缺", "的"]:
        clean_msg = clean_msg.replace(filler, "")

    active_jobs = fetch_jobs_data()
    active_faqs = fetch_faqs_data()

    # 1. 泛稱查詢
    if raw_msg in ["找工作", "有工作嗎", "有哪些工作", "工作推薦", "職缺列表", "看職缺", "全部職缺", "推薦職缺", "工作", "職缺"] or clean_msg == "":
        if active_jobs:
            target_line_bot_api.reply_message(reply_token, create_job_flex_card(active_jobs, user_id))
            return

    # 2. FAQ 比對
    for faq in active_faqs:
        q_keywords = str(faq.get("問題與常見問法") or faq.get("問題") or "").replace("、", ",").replace("，", ",").replace("/", ",").split(",")
        answer = faq.get("標準回覆內容") or faq.get("回答") or ""
        for kw in q_keywords:
            kw_clean = kw.strip()
            if kw_clean and (kw_clean in raw_msg or raw_msg in kw_clean):
                reply_text = f"{answer}\n\n💡 材霈小提醒：本回覆由材霈AI智能助理自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                return

    # 3. 全文檢索 + 同義詞擴展比對
    msg_norm = raw_msg.replace("台", "臺").lower()
    search_tokens = set()
    if clean_msg:
        search_tokens.add(clean_msg)

    for group in SYNONYM_GROUPS:
        if any(kw.lower() in msg_norm or (clean_msg and kw.lower() in clean_msg) for kw in group if kw):
            for kw in group:
                if kw:
                    search_tokens.add(kw.lower())

    matched_jobs = []
    if active_jobs:
        for job in active_jobs:
            row_text = str(job.get("_raw_row_text", "")).replace("台", "臺").lower()
            if not row_text:
                row_text = " ".join([str(v) for v in job.values()]).replace("台", "臺").lower()

            # A. 命中搜尋詞（如：理貨、momo、桃園）
            if any(token in row_text for token in search_tokens):
                matched_jobs.append(job)
            # B. 雙字滑動比對
            elif len(clean_msg) >= 2:
                for i in range(len(clean_msg) - 1):
                    sub = clean_msg[i:i+2]
                    if sub in row_text:
                        matched_jobs.append(job)
                        break

    if matched_jobs:
        unique_jobs = []
        seen_ids = set()
        for j in matched_jobs:
            jid = j.get("職缺代碼") or j.get("_parsed_title") or str(j)
            if jid not in seen_ids:
                seen_ids.add(jid)
                unique_jobs.append(j)

        target_line_bot_api.reply_message(reply_token, create_job_flex_card(unique_jobs, user_id))
        print(f"[職缺命中] 關鍵字: {search_tokens}，成功推播 {len(unique_jobs)} 筆職缺！")
        return

    # 4. 兜底查無回覆
    reply_no_job = (
        f"您好！目前開放的職缺中，暫時沒有完全符合「{raw_msg}」條件的工作。\n\n"
        "已為您記錄需求，若後續有最新符合的職缺開放，專員將第一時間主動聯繫您！"
    )
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_no_job))

# ==========================================
# 5. Webhook 路由端點
# ==========================================
@app.get("/")
def health_check():
    return {"status": "ok", "service": "Tsaipei LineBot is running."}

@app.post("/test-callback")
async def test_callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try:
        test_handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@test_handler.add(MessageEvent, message=TextMessage)
def handle_test_message(event):
    process_user_message(event, test_line_bot_api)

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    process_user_message(event, line_bot_api)