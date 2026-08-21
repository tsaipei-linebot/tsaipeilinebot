import os
import re
import csv
import io
import time
import datetime
import urllib.request
import json
import threading
import pytz
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google import genai

# 載入 .env 環境變數
load_dotenv()

# ==========================================
# 版本定義：tsaipeilinebotmark2
# ==========================================
app = FastAPI(title="tsaipeilinebotmark2", version="2.0.0")

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
# 2. 初步開場與預設引導詞定義
# ==========================================
HUMAN_GUIDE_TEXT = (
    "您好！我是材霈的人資招募專員小霈 😊\n\n"
    "很高興為您服務！為了幫您精準媒合最合適的工作，想先了解一下：\n\n"
    "1. 您希望在【哪個地區】上班？（例如：桃園、新莊、台中、台南、高雄等）\n"
    "2. 有偏好的【工作類型】或【班別】嗎？（例如：理貨、作業員、早班、夜班）\n\n"
    "💡 您可以直接點擊下方快捷按鈕，或直接打字告訴我您的需求喔！"
)

DEFAULT_QUICK_BUTTONS = [
    "📍 桃園工作", "📍 新莊工作", "📍 台中工作", 
    "📍 南部工作", "☀️ 固定早班", "🌙 夜班/大夜"
]

# ==========================================
# 3. 對話記憶與使用者求職輪廓累積快取
# ==========================================
user_sessions = {}
SESSION_TTL = 1800  # 30 分鐘

def get_user_session(user_id: str) -> dict:
    now = time.time()
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if now - session["last_time"] < SESSION_TTL:
            session["last_time"] = now
            return session
    
    user_sessions[user_id] = {
        "last_time": now,
        "messages": [],
        "profile": {
            "area": "",      # 地區 (如: 新莊)
            "shift": "",     # 班別 (如: 早班)
            "job_type": "",  # 工種/內容 (如: 理貨、作業員)
            "salary": "",    # 薪資/領薪方式
            "name": "",
            "phone": ""
        }
    }
    return user_sessions[user_id]

def append_user_history(user_id: str, role: str, text: str):
    session = get_user_session(user_id)
    session["messages"].append({"role": role, "text": text})
    if len(session["messages"]) > 12:
        session["messages"].pop(0)

# ==========================================
# 4. 職缺內容智慧去噪與重點摘要模組
# ==========================================
def extract_smart_summary(raw_desc: str, title: str) -> str:
    if not raw_desc:
        return f"歡迎應徵【{title}】，點擊下方按鈕瞭解完整工作說明！"

    text = str(raw_desc)
    text = re.sub(r'[\w\s]*(?:股份有限公司|有限公司|企業社|商行)', '', text)
    text = re.sub(r'[台臺\w]{2,3}[市縣][\w]{2,3}[區鄉鎮市][\w\d號路街巷弄段\-]+', '', text)
    text = re.sub(r'(?:工期|預計工期|需求人數|人數|工作地點|工作時間|上班時間|班別|薪資|待遇|休假制度|休假|領薪方式)\s*[:：][^\n\r,，、;；]*', '', text)
    text = re.sub(r'[*•▶►◆◇■□▲▼\r\n\t]+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    text = re.sub(r'^[,\.，。、:：;\s]+', '', text)
    text = re.sub(r'[,\.，。、:：;\s]+$', '', text)
    
    if len(text) >= 8:
        if len(text) > 42:
            return text[:42] + "..."
        return text
        
    if any(k in title for k in ["理貨", "揀貨", "倉", "物流"]):
        return "負責商品分揀、理貨貼標與包裝出貨，免經驗環境佳！"
    elif any(k in title for k in ["作業員", "包裝", "組裝", "產線", "技術員"]):
        return "負責機台操作、產品組裝檢驗與成品包裝，免經驗可！"
    elif any(k in title for k in ["司機", "外送", "物流士"]):
        return "負責貨物配送與點交作業，出勤穩定，享優渥津貼！"
        
    return f"開放應徵【{title}】，工作環境單純，歡迎點擊下方履歷應徵！"

# ==========================================
# 5. Google Sheets 資料庫直連與紀錄模組
# ==========================================
CACHE_TTL = 30
_cached_jobs, _last_jobs_fetch = None, 0
_cached_faqs, _last_faqs_fetch = None, 0
_gspread_client = None

def get_sheets_client():
    global _gspread_client
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
        raise FileNotFoundError("找不到 service_account.json 金鑰檔案！請檢查金鑰路徑或 Render Secret Files 設定。")
        
    try:
        if _gspread_client is None:
            creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
            _gspread_client = gspread.authorize(creds)
        return _gspread_client.open(SPREADSHEET_NAME)
    except Exception as e:
        print(f"[Google Sheets 重新認證中...]: {e}")
        creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
        _gspread_client = gspread.authorize(creds)
        return _gspread_client.open(SPREADSHEET_NAME)

def log_user_interaction_to_sheet(user_id: str, user_msg: str, bot_reply: str, user_profile: dict = None):
    try:
        sheet = get_sheets_client()
        ws_name = "求職諮詢紀錄"
        try:
            ws = sheet.worksheet(ws_name)
        except Exception:
            ws = sheet.add_worksheet(title=ws_name, rows="1000", cols="8")
            ws.append_row(["時間戳記", "LINE_User_ID", "求職者訊息", "AI顧問回覆", "希望地區", "偏好班別", "偏好工種", "薪資期待/備註"])
        
        tw_tz = pytz.timezone("Asia/Taipei")
        now_str = datetime.datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")
        
        prof = user_profile or {}
        area = prof.get("area", "")
        shift = prof.get("shift", "")
        job_type = prof.get("job_type", "")
        salary = prof.get("salary", "")
        
        ws.append_row([now_str, user_id, user_msg, bot_reply, area, shift, job_type, salary])
        print(f"[成功寫入求職者紀錄] User: {user_id}, 累計條件 -> 地區:{area} 班別:{shift} 工種:{job_type}")
    except Exception as e:
        print(f"[寫入求職者紀錄失敗]: {e}")

def async_log_user_interaction(user_id: str, user_msg: str, bot_reply: str, user_profile: dict = None):
    """使用非同步背景執行緒寫入試算表，徹底杜絕 LINE Webhook 逾時與等待延遲"""
    thread = threading.Thread(
        target=log_user_interaction_to_sheet,
        args=(user_id, user_msg, bot_reply, user_profile),
        daemon=True
    )
    thread.start()

def fetch_jobs_data() -> list:
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    active_jobs = []
    try:
        sheet = get_sheets_client()
        ws = None
        for name in ["Jobs_職缺資料庫", "職缺清單", "職缺列表", "Jobs", "工作表1"]:
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

            for row in all_values[1:]:
                if not any(str(cell).strip() for cell in row):
                    continue

                row_dict = {}
                for i, val in enumerate(row):
                    h_name = headers[i] if i < len(headers) and headers[i] else f"COL_{i}"
                    row_dict[h_name] = str(val).strip()

                title = ""
                for k, v in row_dict.items():
                    if any(t_kw in k for t_kw in ["職缺名稱", "職稱", "職務名稱", "工作名稱", "title"]):
                        title = v
                        break
                if not title and len(row) >= 2:
                    title = row.strip()

                status = ""
                for k, v in row_dict.items():
                    if any(s_kw in k for s_kw in ["狀態", "職缺狀態", "招募狀態", "status"]):
                        status = v
                        break

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
# 6. 智慧職缺匹配演算法 (相關性計分)
# ==========================================
def find_best_matching_jobs(jobs: list, profile: dict, matched_ids: list = None) -> list:
    if matched_ids:
        res = [jobs[i] for i in matched_ids if i < len(jobs)]
        if res:
            return res

    area_raw = profile.get("area", "").replace("台", "臺")
    area_tokens = [t for t in re.split(r'[市縣區\s]+', area_raw) if len(t) >= 2]
    
    shift_raw = profile.get("shift", "")
    shift_tokens = []
    for s in ["早班", "日班", "中班", "夜班", "晚班", "大夜", "輪班", "週休", "排休"]:
        if s in shift_raw or (s == "日班" and "早班" in shift_raw) or (s == "早班" and "日班" in shift_raw):
            shift_tokens.append(s)

    job_type_raw = profile.get("job_type", "")
    type_tokens = []
    for t in ["理貨", "揀貨", "倉儲", "作業員", "包裝", "組裝", "門市", "司機", "配送", "餐飲", "客服", "生技", "電子"]:
        if t in job_type_raw:
            type_tokens.append(t)

    scored_jobs = []
    for j in jobs:
        row_txt = j.get("_raw_row_text", "").replace("台", "臺")
        score = 0
        
        # 1. 地區比對 (最關鍵)
        area_matched = False
        if area_tokens:
            for at in area_tokens:
                if at in row_txt:
                    score += 50
                    area_matched = True
                    break
        else:
            score += 10

        # 2. 班別比對
        if shift_tokens:
            for st in shift_tokens:
                if st in row_txt:
                    score += 25
                    break

        # 3. 工種比對
        if type_tokens:
            for jt in type_tokens:
                if jt in row_txt:
                    score += 25
                    break

        # 若使用者指定了特定地區，嚴格過濾該地區以外的工作
        if area_tokens and not area_matched:
            continue

        if score > 0:
            scored_jobs.append((score, j))

    scored_jobs.sort(key=lambda x: x[0], reverse=True)
    return [j for score, j in scored_jobs]

# ==========================================
# 7. 雙按鈕 + 4 標籤 Flex 卡片
# ==========================================
def create_job_flex_card(jobs: list, user_id: str):
    if not jobs:
        return None

    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "leave": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "pay": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:6]:
        job_id = str(job.get("職缺代碼") or job.get("職缺編號") or "JOB").strip()
        job_title = str(job.get("_parsed_title") or job.get("職缺名稱(對外)") or job.get("職缺名稱") or "優質職缺").strip()
        
        county = str(job.get("縣市") or "").strip()
        district = str(job.get("行政區") or "").strip()
        location = f"{county} {district}".strip() or "全台各廠區"
        
        salary = str(job.get("薪資") or job.get("薪資待遇") or "依公司規定").strip()
        shift = str(job.get("班別") or "").strip()
        leave = str(job.get("休假制度") or job.get("休假方式") or "").strip()
        job_type = str(job.get("全職/兼職") or job.get("全/兼職") or "").strip()
        pay_method = str(job.get("領薪方式") or "").strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift[:8], "size": "xxs", "color": badge_styles["shift"]["text"], "weight