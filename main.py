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

app = FastAPI()

# ----------------- 環境設定與金鑰 -----------------
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

# 初始化 Google GenAI 客戶端
ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("[系統提示] Gemini 客戶端初始化成功！")
    except Exception as e:
        print(f"[系統警告] Gemini 初始化失敗: {e}")

# ----------------- Google Sheets 連線與安全金鑰讀取 -----------------
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

# ----------------- 高速記憶體快取系統 (Cache TTL: 180 秒) -----------------
CACHE_TTL = 180

_cached_jobs = None
_last_jobs_fetch = 0

_cached_faqs = None
_last_faqs_fetch = 0

_cached_keywords = None
_last_keywords_fetch = 0

def get_cached_jobs(sheet) -> list:
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    try:
        try:
            ws = sheet.worksheet("Jobs_職缺資料庫")
        except Exception:
            ws = sheet.worksheet("職缺清單")
            
        jobs_data = ws.get_all_records()
        active_jobs = []
        stop_keywords = ["停招", "暫停", "額滿", "關閉", "下架", "結束", "否", "滿", "pause", "close"]
        
        for j in jobs_data:
            title = str(j.get("職缺名稱", j.get("職缺名稱(對外)", ""))).strip()
            status = str(j.get("職缺狀態", j.get("狀態", ""))).strip()
            if title and not any(stop_kw in status for stop_kw in stop_keywords):
                active_jobs.append(j)

        _cached_jobs = active_jobs
        _last_jobs_fetch = now
        print(f"[快取更新] 職缺清單載入完成，有效職缺共 {len(active_jobs)} 筆")
        return active_jobs
    except Exception as e:
        print(f"[快取讀取錯誤] 職缺載入失敗: {e}")
        return _cached_jobs or []

def get_cached_faqs(sheet) -> list:
    global _cached_faqs, _last_faqs_fetch
    now = time.time()
    if _cached_faqs is not None and (now - _last_faqs_fetch < CACHE_TTL):
        return _cached_faqs

    try:
        try:
            ws = sheet.worksheet("FAQ_客服問答")
        except Exception:
            ws = sheet.worksheet("FAQ知識庫")
            
        faq_data = ws.get_all_records()
        active_faqs = [
            f for f in faq_data 
            if str(f.get("狀態", f.get("status", ""))).strip() in ["是", "啟用", "active", "1", ""]
        ]
        _cached_faqs = active_faqs
        _last_faqs_fetch = now
        print(f"[快取更新] FAQ 載入完成，有效問答共 {len(active_faqs)} 筆")
        return active_faqs
    except Exception as e:
        print(f"[快取讀取錯誤] FAQ 載入失敗: {e}")
        return _cached_faqs or []

def fetch_job_keywords(sheet) -> dict:
    global _cached_keywords, _last_keywords_fetch
    now = time.time()
    if _cached_keywords is not None and (now - _last_keywords_fetch < CACHE_TTL):
        return _cached_keywords

    default_keywords = {
        "general_queries": ["找工作", "有工作嗎", "有哪些工作", "工作推薦", "職缺列表", "看職缺", "全部職缺", "推薦職缺", "我想找工作", "有缺嗎"],
        "triggers": ["工作", "職缺", "缺額", "上班", "應徵", "理貨", "揀貨", "倉管", "倉儲", "作業員", "包裝", "司機", "台北", "臺北", "新北", "桃園", "台中", "臺中", "台南", "臺南", "高雄", "週休", "周休", "日班", "夜班", "早班", "中班", "晚班", "大夜", "兼職", "全職", "時薪", "月薪", "週領", "周領", "日領", "月領"],
        "synonym_groups": [
            ["理貨", "揀貨", "包裝", "倉管", "倉儲", "物流", "加工", "理貨員", "揀貨員"],
            ["晚班", "夜班", "大夜", "小夜", "夜間", "大夜班"],
            ["早班", "日班", "晨班", "日間"],
            ["中班", "午後班"],
            ["作業員", "技術員", "品檢", "組裝", "測試", "產線"],
            ["司機", "送貨", "外送", "物流司機", "駕駛"],
            ["週休", "周休", "排休", "見紅休", "月休八天"],
            ["週領", "周領", "日領", "預支", "借支", "領錢"],
            ["兼職", "工讀", "打工", "pt", "PT", "短期", "部分工時"],
            ["全職", "正職", "常態"]
        ]
    }

    try:
        ws = sheet.worksheet("job_keywords")
        records = ws.get_all_records()
        
        fetched_triggers = set(default_keywords["triggers"])
        fetched_general = set(default_keywords["general_queries"])
        synonym_groups = []

        for row in records:
            kw = str(row.get("關鍵字", row.get("keyword", ""))).strip()
            category = str(row.get("分類", row.get("類別", row.get("category", "")))).strip()
            synonyms = str(row.get("同義詞", row.get("同義字", row.get("synonyms", "")))).strip()
            
            group = [kw] if kw else []
            if synonyms:
                group.extend([s.strip() for s in re.split(r'[\s,，、/]+', synonyms) if s.strip()])
            
            if group:
                synonym_groups.append(group)
                for item in group:
                    if "泛稱" in category or "通用" in category:
                        fetched_general.add(item)
                    else:
                        fetched_triggers.add(item)

        result = {
            "general_queries": list(fetched_general),
            "triggers": list(fetched_triggers),
            "synonym_groups": synonym_groups if synonym_groups else default_keywords["synonym_groups"]
        }
        _cached_keywords = result
        _last_keywords_fetch = now
        return result
    except Exception as e:
        return default_keywords

# ----------------- 雙按鈕 + 膠囊標籤 Flex 職缺卡片 -----------------
def create_job_flex_card(jobs: list, user_id: str) -> FlexSendMessage:
    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "leave": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "pay": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:10]:
        job_id = str(job.get("職缺編號", job.get("職缺代碼", ""))).strip() or "JOB"
        job_title = str(job.get("職缺名稱", job.get("職缺名稱(對外)", "優質職缺"))).strip()
        county = str(job.get("縣市", "")).strip()
        district = str(job.get("行政區", "")).strip()
        location = f"{county} {district}".strip() if (county or district) else "台灣各廠區"
        
        salary = str(job.get("薪資待遇", job.get("薪資", "依公司規定"))).strip()
        shift = str(job.get("班別", "")).strip()
        leave = str(job.get("休假方式", job.get("休假制度", ""))).strip()
        job_type = str(job.get("全/兼職", job.get("全職/兼職", ""))).strip()
        pay_method = str(job.get("領薪方式", "")).strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift, "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]})
        if leave:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["leave"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": leave, "size": "xxs", "color": badge_styles["leave"]["text"], "weight": "bold"}]})
        if job_type:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["type"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": job_type, "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]})
        if pay_method:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["pay"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": pay_method, "size": "xxs", "color": badge_styles["pay"]["text"], "weight": "bold"}]})

        raw_desc = str(job.get("工作內容與條件", job.get("工作內容(對外)", ""))).strip()
        clean_desc = re.sub(r'[\r\n\t]+', ' ', raw_desc).strip()
        if len(clean_desc) > 50:
            clean_desc = clean_desc[:50] + "..."
        if not clean_desc:
            clean_desc = "歡迎點擊下方按鈕瞭解詳細說明與應徵！"
            
        base_clean = OFFICIAL_WEBSITE_BASE.strip() if OFFICIAL_WEBSITE_BASE else "https://tsaipei.netlify.app"
        if not (base_clean.startswith("http://") or base_clean.startswith("https://")):
            base_clean = "https://tsaipei.netlify.app"
        website_job_url = f"{base_clean.rstrip('/')}/#jobs"[cite: 1]

        raw_resume_url = str(job.get("線上履歷連結", job.get("線上履歷網址", ""))).strip()
        if raw_resume_url.startswith("http://") or raw_resume_url.startswith("https://"):
            separator = "&" if "?" in raw_resume_url else "?"
            apply_link = f"{raw_resume_url}{separator}job_id={job_id}&line_id={user_id}"
        else:
            apply_link = f"{base_clean.rstrip('/')}/#jobs"[cite: 1]

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

# ----------------- Gemini AI 智慧分析 -----------------
def query_gemini(prompt: str) -> str:
    if not ai_client:
        return "NO_MATCH"
    
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for m in candidate_models:
        try:
            res = ai_client.models.generate_content(model=m, contents=prompt)
            if res and hasattr(res, 'text') and res.text:
                return res.text.strip()
        except Exception:
            continue
    return "NO_MATCH"

# ----------------- 核心對話處理邏輯 -----------------
def process_user_message(event, target_line_bot_api: LineBotApi):
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_user_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')

    # 文字淨化：去除標點符號與常見疑問贅字
    clean_msg = re.sub(r'[？\?！!。，,\s]+', '', raw_user_msg).replace("台", "臺").lower()
    for filler in ["有嗎", "有沒有", "我想找", "想找", "我要找", "請問", "可以推薦", "推薦", "的工作", "工作", "職缺"]:
        clean_msg = clean_msg.replace(filler, "")

    try:
        sheet = get_sheets_client()
    except Exception as e:
        print(f"Google Sheet 連線失敗: {e}")
        return

    active_jobs = get_cached_jobs(sheet)
    active_faqs = get_cached_faqs(sheet)
    job_kw_dict = fetch_job_keywords(sheet)
    general_job_queries = job_kw_dict.get("general_queries", [])
    job_search_triggers = job_kw_dict.get("triggers", [])
    synonym_groups = job_kw_dict.get("synonym_groups", [])

    # ================= 1. 純泛稱查詢（找工作、看職缺） =================
    if raw_user_msg in general_job_queries or clean_msg in ["工作", "職缺", "缺額", "找工作", "看工作", ""]:
        if active_jobs:
            target_line_bot_api.reply_message(reply_token, create_job_flex_card(active_jobs, user_id))
            return

    # ================= 2. FAQ 精確問答比對 =================
    for faq in active_faqs:
        q_keywords = str(faq.get("問題與常見問法", faq.get("問題", ""))).replace("、", ",").replace("，", ",").replace("/", ",").split(",")
        answer = faq.get("標準回覆內容", faq.get("回答", ""))
        for kw in q_keywords:
            kw_clean = kw.strip()
            if kw_clean and (kw_clean in raw_user_msg or raw_user_msg in kw_clean):
                reply_text = f"{answer}\n\n💡 材霈小提醒：本回覆由系統自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                return

    # ================= 3. 關鍵字與職缺同義詞直接全文搜尋（核心修復） =================
    msg_norm = raw_user_msg.replace("台", "臺").lower()
    matched_jobs = []
    
    # 建立搜尋比對詞庫（使用者輸入核心詞 + 關聯同義詞）
    search_tokens = set()
    if clean_msg:
        search_tokens.add(clean_msg)

    for group in synonym_groups:
        if any(kw.lower() in msg_norm or (clean_msg and kw.lower() in clean_msg) for kw in group if kw):
            for kw in group:
                if kw:
                    search_tokens.add(kw.lower())

    if search_tokens:
        for job in active_jobs:
            full_info = (
                str(job.get("職缺名稱", job.get("職缺名稱(對外)", ""))) + " " +
                str(job.get("縣市", "")) + " " +
                str(job.get("行政區", "")) + " " +
                str(job.get("班別", "")) + " " +
                str(job.get("休假方式", job.get("休假制度", ""))) + " " +
                str(job.get("全/兼職", job.get("全職/兼職", ""))) + " " +
                str(job.get("領薪方式", "")) + " " +
                str(job.get("行業別", "")) + " " +
                str(job.get("工作內容與條件", job.get("工作內容(對外)", "")))
            ).lower()

            if any(token in full_info for token in search_tokens):
                matched_jobs.append(job)

    if matched_jobs:
        unique_jobs = []
        seen_ids = set()
        for j in matched_jobs:
            jid = j.get("職缺編號", j.get("職缺代碼", j.get("職缺名稱", j.get("職缺名稱(對外)"))))
            if jid not in seen_ids:
                seen_ids.add(jid)
                unique_jobs.append(j)

        target_line_bot_api.reply_message(reply_token, create_job_flex_card(unique_jobs, user_id))
        print(f"[關鍵字直接命中] 比對詞: {search_tokens}，推播 {len(unique_jobs)} 筆職缺")
        return

    # ================= 4. Gemini 雙軌意圖裁決 (FAQ vs JOBS) =================
    if ai_client:
        faq_context = ""
        for idx, faq in enumerate(active_faqs, 1):
            faq_context += f"【FAQ_{idx}】問題:{faq.get('問題與常見問法', faq.get('問題',''))} | 答案:{faq.get('標準回覆內容', faq.get('回答',''))}\n"

        job_context = ""
        for idx, j in enumerate(active_jobs):
            job_context += f"【JOB_{idx}】名稱:{j.get('職缺名稱', j.get('職缺名稱(對外)',''))} | 地點:{j.get('縣市','')}{j.get('行政區','')} | 待遇:{j.get('薪資待遇', j.get('薪資',''))} | 內容:{j.get('工作內容與條件', j.get('工作內容(對外)',''))}\n"

        intent_prompt = f"""你是一位專業的招募客服。分析求職者訊息並給出合適回覆。

求職者訊息：「{raw_user_msg}」

FAQ 清單：
{faq_context}

職缺清單：
{job_context}

任務：
1. 若詢問制度/發薪/福利/請假等問題，請輸出：TYPE:FAQ|內容:[標準答案]
2. 若詢問任何工作職缺，請輸出：TYPE:JOBS|編號:[符合的JOB編號，例如 0 或 0,1]
3. 若皆非：輸出 NO_MATCH"""

        ai_res = query_gemini(intent_prompt).strip()

        if ai_res.startswith("TYPE:FAQ|內容:"):
            faq_answer = ai_res.replace("TYPE:FAQ|內容:", "").strip()
            reply_text = f"{faq_answer}\n\n💡 材霈小提醒：本回覆由系統自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
            return

        elif ai_res.startswith("TYPE:JOBS|編號:"):
            indices_str = ai_res.replace("TYPE:JOBS|編號:", "").strip()
            indices = [int(n) for n in re.findall(r'\d+', indices_str) if int(n) < len(active_jobs)]
            if indices:
                filtered_jobs = [active_jobs[i] for i in indices]
                target_line_bot_api.reply_message(reply_token, create_job_flex_card(filtered_jobs, user_id))
                return

    # ================= 5. 兜底回覆 =================
    reply_no_job = (
        f"您好！目前開放的職缺中，暫時沒有完全符合「{raw_user_msg}」條件的工作。\n\n"
        "已為您記錄需求，若後續有最新符合的職缺開放，專員將第一時間主動聯繫您！"
    )
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_no_job))

# ----------------- 測試頻道 Webhook 路由 -----------------
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

# ----------------- 正式頻道 Webhook 路由 -----------------
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