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
        "triggers": ["工作", "職缺", "缺額", "上班", "應徵", "台北", "臺北", "新北", "桃園", "台中", "臺中", "台南", "臺南", "高雄", "週休", "周休", "日班", "夜班", "早班", "中班", "晚班", "大夜", "兼職", "全職", "時薪", "月薪", "週領", "周領", "日領", "月領"],
        "synonym_groups": [
            ["晚班", "夜班", "大夜", "小夜", "夜間", "大夜班"],
            ["早班", "日班", "晨班", "日間"],
            ["中班", "午後班"],
            ["週休", "周休", "排休", "見紅休", "月休八天"],
            ["週領", "周領", "日領", "預支", "借支"],
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
        print(f"[系統提示] 成功自 job_keywords 載入 {len(synonym_groups)} 組同義關鍵字庫！")
        return result
    except Exception as e:
        print(f"[系統提示] 讀取 job_keywords 分頁失敗，使用預設同義詞庫 (錯誤: {e})")
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
            tags_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": badge_styles["shift"]["bg"],
                "cornerRadius": "sm",
                "paddingAll": "xs",
                "paddingStart": "sm",
                "paddingEnd": "sm",
                "contents": [{"type": "text", "text": shift, "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]
            })
            
        if leave:
            tags_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": badge_styles["leave"]["bg"],
                "cornerRadius": "sm",
                "paddingAll": "xs",
                "paddingStart": "sm",
                "paddingEnd": "sm",
                "contents": [{"type": "text", "text": leave, "size": "xxs", "color": badge_styles["leave"]["text"], "weight": "bold"}]
            })

        if job_type:
            tags_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": badge_styles["type"]["bg"],
                "cornerRadius": "sm",
                "paddingAll": "xs",
                "paddingStart": "sm",
                "paddingEnd": "sm",
                "contents": [{"type": "text", "text": job_type, "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]
            })

        if pay_method:
            tags_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": badge_styles["pay"]["bg"],
                "cornerRadius": "sm",
                "paddingAll": "xs",
                "paddingStart": "sm",
                "paddingEnd": "sm",
                "contents": [{"type": "text", "text": pay_method, "size": "xxs", "color": badge_styles["pay"]["text"], "weight": "bold"}]
            })

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
            body_contents.append({
                "type": "box",
                "layout": "horizontal",
                "spacing": "xs",
                "margin": "sm",
                "contents": tags_contents
            })
            
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

# ----------------- 呼叫 Gemini AI 函式 -----------------
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

    user_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')

    try:
        sheet = get_sheets_client()
    except Exception as e:
        print(f"Google Sheet 連線失敗: {e}")
        return

    active_jobs = get_cached_jobs(sheet)
    job_kw_dict = fetch_job_keywords(sheet)
    general_job_queries = job_kw_dict.get("general_queries", [])
    job_search_triggers = job_kw_dict.get("triggers", [])
    synonym_groups = job_kw_dict.get("synonym_groups", [])

    # ================= 1. 職缺查詢處理 =================
    try:
        msg_norm = user_msg.replace("台", "臺").lower()

        # 情況 A：純泛稱查詢（例如：找工作、看職缺）
        if user_msg in general_job_queries or (len(user_msg) <= 4 and user_msg in ["工作", "職缺", "缺額"]):
            if active_jobs:
                flex_msg = create_job_flex_card(active_jobs, user_id)
                target_line_bot_api.reply_message(reply_token, flex_msg)
                return

        # 情況 B：條件、地區或關鍵字篩選
        is_job_intent = any(trig.lower() in msg_norm for trig in job_search_triggers)
        
        if is_job_intent and active_jobs:
            # 1. 優先檢查地區（縣市與行政區）
            loc_matched_jobs = []
            detected_locations = []
            
            for job in active_jobs:
                county = str(job.get("縣市", "")).strip().replace("台", "臺")
                district = str(job.get("行政區", "")).strip().replace("台", "臺")
                
                loc_tokens = []
                for val in [county, district]:
                    if val:
                        loc_tokens.append(val)
                        short_val = val.replace("市", "").replace("縣", "").replace("區", "").replace("鄉", "").replace("鎮", "")
                        if len(short_val) >= 2:
                            loc_tokens.append(short_val)
                
                for token in loc_tokens:
                    if token.lower() in msg_norm:
                        if token not in detected_locations:
                            detected_locations.append(token)
                        loc_matched_jobs.append(job)
                        break

            # 若使用者有提到明確地區（例如「新莊工作」）
            if detected_locations:
                unique_loc_jobs = []
                seen_ids = set()
                for j in loc_matched_jobs:
                    jid = j.get("職缺編號", j.get("職缺代碼", j.get("職缺名稱", j.get("職缺名稱(對外)"))))
                    if jid not in seen_ids:
                        seen_ids.add(jid)
                        unique_loc_jobs.append(j)

                if unique_loc_jobs:
                    flex_msg = create_job_flex_card(unique_loc_jobs, user_id)
                    target_line_bot_api.reply_message(reply_token, flex_msg)
                    return
                else:
                    reply_no_loc = (
                        f"您好！目前在「{'、'.join(detected_locations)}」地區暫時沒有開放中的職缺。\n\n"
                        "建議您可以輸入「找工作」瀏覽全區職缺，或告訴專員您的需求為您留意！"
                    )
                    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_no_loc))
                    return

            # 2. 同義詞與特徵優先命中 (例如：晚班 自動關聯 夜班/大夜/中班)
            matched_synonym_jobs = []
            expanded_search_tokens = set()

            for group in synonym_groups:
                # 檢查使用者的輸入是否命中該同義詞群組中任一詞彙
                if any(kw.lower() in msg_norm for kw in group if kw):
                    # 將整組同義詞全部加入搜尋擴展詞庫
                    for kw in group:
                        if kw:
                            expanded_search_tokens.add(kw.lower())

            if expanded_search_tokens:
                for job in active_jobs:
                    full_info = (
                        str(job.get("職缺名稱", job.get("職缺名稱(對外)", ""))) + " " +
                        str(job.get("班別", "")) + " " +
                        str(job.get("休假方式", job.get("休假制度", ""))) + " " +
                        str(job.get("全/兼職", job.get("全職/兼職", ""))) + " " +
                        str(job.get("領薪方式", "")) + " " +
                        str(job.get("行業別", "")) + " " +
                        str(job.get("工作內容與條件", job.get("工作內容(對外)", "")))
                    ).lower()
                    
                    if any(token in full_info for token in expanded_search_tokens):
                        matched_synonym_jobs.append(job)

                if matched_synonym_jobs:
                    unique_syn_jobs = []
                    seen_ids = set()
                    for j in matched_synonym_jobs:
                        jid = j.get("職缺編號", j.get("職缺代碼", j.get("職缺名稱", j.get("職缺名稱(對外)"))))
                        if jid not in seen_ids:
                            seen_ids.add(jid)
                            unique_syn_jobs.append(j)

                    flex_msg = create_job_flex_card(unique_syn_jobs, user_id)
                    target_line_bot_api.reply_message(reply_token, flex_msg)
                    print(f"[同義關鍵字命中] 擴展詞庫: {expanded_search_tokens}，推播 {len(unique_syn_jobs)} 筆職缺")
                    return

            # 3. Gemini 智慧語意比對
            if ai_client:
                job_context = ""
                for idx, j in enumerate(active_jobs):
                    t = j.get("職缺名稱", j.get("職缺名稱(對外)", ""))
                    l = f"{j.get('縣市', '')} {j.get('行政區', '')}".strip()
                    s = j.get("薪資待遇", j.get("薪資", ""))
                    b = f"班別:{j.get('班別', '')} | 休假:{j.get('休假方式', j.get('休假制度', ''))} | 類型:{j.get('全/兼職', j.get('全職/兼職', ''))} | 領薪:{j.get('領薪方式', '')}"
                    job_context += f"【編號_{idx}】名稱：{t} | 地點：{l} | 待遇：{s} | 屬性：{b}\n"

                job_filter_prompt = f"""你是一位招募客服。以下是開放職缺：
{job_context}

求職者提問：「{user_msg}」

任務：
1. 判斷是否有任何一筆職缺符合求職者的需求（特別是班別、休假、領薪或工作性質）。
2. 若有，請只輸出符合的【編號數字】（例如：0 或 0,1）。
3. 若無符合，請只回覆：NO_MATCH"""

                ai_res = query_gemini(job_filter_prompt).strip()
                if ai_res and "NO_MATCH" not in ai_res:
                    indices = [int(n) for n in re.findall(r'\d+', ai_res) if int(n) < len(active_jobs)]
                    if indices:
                        filtered_jobs = [active_jobs[i] for i in indices]
                        flex_msg = create_job_flex_card(filtered_jobs, user_id)
                        target_line_bot_api.reply_message(reply_token, flex_msg)
                        return

            # 4. 確實無符合職缺
            reply_no_job = (
                f"您好！目前開放的職缺中，暫時沒有完全符合「{user_msg}」條件的工作。\n\n"
                "已為您記錄需求，若後續有最新符合的職缺開放，專員將第一時間主動聯繫您！"
            )
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_no_job))
            return

    except Exception as e:
        print(f"職缺查詢出錯: {e}")

    # ================= 2. FAQ 客服問答 =================
    try:
        active_faqs = get_cached_faqs(sheet)

        # 第一道：精確比對常見問法
        for faq in active_faqs:
            q_keywords = str(faq.get("問題與常見問法", faq.get("問題", ""))).replace("、", ",").replace("，", ",").replace("/", ",").split(",")
            answer = faq.get("標準回覆內容", faq.get("回答", ""))
            for kw in q_keywords:
                kw_clean = kw.strip()
                if kw_clean and (kw_clean in user_msg or user_msg in kw_clean):
                    reply_text = f"{answer}\n\n💡 材霈小提醒：本回覆由系統自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                    return

        # 第二道：Gemini 語意理解
        if active_faqs and ai_client:
            faq_context = ""
            for idx, faq in enumerate(active_faqs, 1):
                cat = faq.get("類別", faq.get("category", ""))
                q = faq.get("問題與常見問法", faq.get("問題", faq.get("questions", "")))
                a = faq.get("標準回覆內容", faq.get("回答", faq.get("answer", "")))
                faq_context += f"【項目{idx}】類別：{cat} | 問法包含：{q} | 標準答案：{a}\n"

            prompt = f"""你是一位專業的招募客服助理。請判斷【求職者提問】是否在詢問【官方規範清單】中的任何主題。

【官方規範清單】：
{faq_context}

【求職者提問】：「{user_msg}」

【判斷規則】：
1. 只要求職者的提問「意圖或主題」與清單中任何項目相關，請直接輸出該項目的【標準答案】。
2. 輸出時請原汁原味輸出標準答案，不要加上多餘的開場白或問候。
3. 若提問與清單完全無關，才回覆：NO_MATCH

請輸出回覆："""

            ai_reply = query_gemini(prompt)
            if ai_reply and "NO_MATCH" not in ai_reply:
                reply_text = f"{ai_reply}\n\n💡 材霈小提醒：本回覆由系統自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                return

        # 第三道：關鍵字兜底
        money_words = ["錢", "薪", "薪資", "待遇", "所得", "款"]
        action_words = ["領", "給", "發", "哪天", "何時", "幾號", "匯", "入帳", "拿", "算", "領到"]
        if (any(mw in user_msg for mw in money_words) and any(aw in user_msg for aw in action_words)) or user_msg in ["給錢", "領錢", "發薪"]:
            for faq in active_faqs:
                q_text = str(faq.get("問題與常見問法", faq.get("問題", ""))) + str(faq.get("類別", ""))
                if any(s in q_text for s in ["領錢", "薪資", "發薪", "薪水"]):
                    answer = faq.get("標準回覆內容", faq.get("回答", ""))
                    reply_text = f"{answer}\n\n💡 材霈小提醒：本回覆由材霈AI智能助理自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                    return

    except Exception as e:
        print(f"FAQ 比對發生錯誤: {e}")

    # ================= 3. 兜底罐頭回覆 =================
    fallback_text = (
        "您好！材霈AI智能助理在知識庫中暫時找不到與您提問完全相符的規範。\n\n"
        "已為您記錄問題，專員將於上班時間（週一至週五 09:00 - 18:00）盡快回覆您！"
    )
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=fallback_text))

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