import os
import re
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from google import genai

load_dotenv()

app = FastAPI(title="Tsaipei AI Recruitment Consultant - Legal & Formatted Detail Engine", version="9.0.0")

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
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_JOBS_DB_ID = os.getenv("NOTION_JOBS_DB_ID")
NOTION_FAQ_DB_ID = os.getenv("NOTION_FAQ_DB_ID")
OFFICIAL_WEBSITE_BASE = os.getenv("OFFICIAL_WEBSITE_BASE", "https://tsaipei.netlify.app")

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("[系統提示] Gemini AI 客戶端初始化成功！")
    except Exception as e:
        print(f"[系統警告] Gemini AI 初始化失敗: {e}")

# ==========================================
# 2. 對話記憶體快取 (Session Context - 7 天)
# ==========================================
user_sessions = {}
SESSION_TTL = 7 * 24 * 3600

def _get_or_create_session(user_id: str) -> dict:
    """取得（或建立）使用者的對話 Session，內含歷史訊息與已收集到的需求條件 (slots)。"""
    now = time.time()
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if now - session["last_time"] < SESSION_TTL:
            session["last_time"] = now
            return session
    user_sessions[user_id] = {
        "last_time": now,
        "messages": [],
        # 漸進式需求收集 (Slot-Filling)：地區 / 工作類別(行業別) / 時段班別
        "slots": {"location": "", "category": "", "shift": ""}
    }
    return user_sessions[user_id]

def get_user_history(user_id: str) -> list:
    return _get_or_create_session(user_id)["messages"]

def get_user_slots(user_id: str) -> dict:
    """回傳使用者目前已被顧問掌握的需求條件（地區/類別/時段）。"""
    return _get_or_create_session(user_id)["slots"]

def update_user_slots(user_id: str, location: str = "", category: str = "", shift: str = "") -> dict:
    """更新使用者的已知需求條件，只有傳入非空值才會覆寫，避免把已掌握的條件洗掉。"""
    slots = get_user_slots(user_id)
    if location:
        slots["location"] = location
    if category:
        slots["category"] = category
    if shift:
        slots["shift"] = shift
    return slots

def append_user_history(user_id: str, role: str, text: str):
    history = get_user_history(user_id)
    history.append({"role": role, "text": text})
    if len(history) > 10:
        history.pop(0)

# ==========================================
# 3. AI 職缺內容美化與吸睛亮點提煉模組
# ==========================================
summary_cache = {}
detail_cache = {}

def polish_job_description_with_ai(raw_desc: str, title: str) -> str:
    """使用 AI 將生硬的工作內容提煉美化為卡片精華短句 (35-45字)"""
    if not raw_desc:
        return f"歡迎應徵【{title}】，環境單純、福利健全，點擊下方立即應徵！"

    cache_key = f"{title}_{raw_desc[:30]}"
    if cache_key in summary_cache:
        return summary_cache[cache_key]

    text = str(raw_desc)
    text = re.sub(r'[\w\s]*(?:股份有限公司|有限公司|企業社|商行)', '', text)
    text = re.sub(r'[台臺\w]{2,3}[市縣][\w]{2,3}[區鄉鎮市][\w\d號路街巷弄段\-]+', '', text)
    text = re.sub(r'(?:工期|預計工期|需求人數|人數|工作地點|工作時間|上班時間|班別|薪資|待遇|休假制度|休假|領薪方式)\s*[:：][^\n\r,，、;；]*', '', text)
    text = re.sub(r'[*•▶►◆◇■□▲▼\r\n\t]+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    fallback_text = text[:40] + "..." if len(text) > 40 else text
    if not fallback_text or len(fallback_text) < 6:
        fallback_text = f"開放應徵【{title}】，工作環境良好、無經驗可，歡迎應徵！"

    if not ai_client:
        summary_cache[cache_key] = fallback_text
        return fallback_text

    prompt = f"""請將以下職缺工作內容改寫為一句「極具吸引力、親切、吸引求職者應徵」的精華短句（字數控制在 30-42 字之間，繁體中文）：
職缺名稱：{title}
原始工作內容：{raw_desc[:300]}
請直接回覆改寫後的一句話："""

    try:
        models = ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
        for m in models:
            if hasattr(ai_client, 'models'):
                res = ai_client.models.generate_content(model=m, contents=prompt)
                if res and hasattr(res, 'text') and res.text:
                    polished = res.text.strip().replace("\n", " ")
                    summary_cache[cache_key] = polished
                    return polished
    except Exception as e:
        print(f"[AI 文案美化異常]: {e}")

    summary_cache[cache_key] = fallback_text
    return fallback_text

def format_full_job_detail_with_ai(job: dict, location_display: str) -> str:
    """使用 AI 進行「詳細工作說明」排版與《就業服務法》合規審查"""
    job_id = job.get("_page_id", "JOB")
    if job_id in detail_cache:
        return detail_cache[job_id]

    title = job.get("職缺名稱(對外)") or job.get("職缺名稱") or "優質職缺"
    salary = job.get("薪資") or "依公司規定"
    shift = job.get("班別") or "依排班規定"
    raw_desc = job.get("工作內容(對外)") or "歡迎點擊線上履歷應徵。"

    fallback_layout = (
        f"📋【職缺名稱：{title}】\n\n"
        f"📍 上班地點：{location_display}\n"
        f"💰 薪資待遇：{salary}\n"
        f"⏰ 工作班別：{shift}\n\n"
        f"📝 工作內容詳細說明：\n{raw_desc}\n\n"
        f"💡 依法所有職缺無性別、年齡歧視限制，歡迎所有朋友應徵！"
    )

    if not ai_client:
        detail_cache[job_id] = fallback_layout
        return fallback_layout

    prompt = f"""你是一位專業的人資顧問，請將以下職缺資料進行【優雅美化排版】並進行【就業服務法合規審查】：

職缺名稱：{title}
上班地點：{location_display}
薪資待遇：{salary}
工作班別：{shift}
原始工作內容：
{raw_desc}

【處理與合規原則】：
1. 《就業服務法》第5條合規審查：若原文中有涉及年齡（如限幾歲）、性別（如限男女）、外貌等歧視性條件，請直接移除或轉化為客觀條件（如「需配合走動作業/具備基本體能」）。
2. 使用清晰條列式排版，搭配適當 Emoji，讓手機閱讀體感極佳。
3. 排版結構建議包含：
   📋【職缺名稱：{title}】（請務必放在最上方第一行，直接使用上方提供的職缺名稱，不要自行更改職缺名稱文字）
   📌【職缺亮點】（約 1-2 句吸引人的優勢）
   📍【工作地點與班別】
   💰【薪資與福利】
   📝【工作主要內容】（條列式 3-4 點）
   ✨【應徵條件】（強調免經驗、體能勝任等）
4. 結尾加上一句親切鼓勵應徵的招呼語。

請直接輸出排版後的繁體中文內容："""

    try:
        models = ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
        for m in models:
            if hasattr(ai_client, 'models'):
                res = ai_client.models.generate_content(model=m, contents=prompt)
                if res and hasattr(res, 'text') and res.text:
                    formatted = res.text.strip()
                    detail_cache[job_id] = formatted
                    return formatted
    except Exception as e:
        print(f"[AI 詳細內容排版異常]: {e}")

    detail_cache[job_id] = fallback_layout
    return fallback_layout

# ==========================================
# 4. Notion 原生 Direct HTTP Query
# ==========================================
CACHE_TTL = 30
_cached_jobs, _last_jobs_fetch = None, 0
_cached_faqs, _last_faqs_fetch = None, 0

ALLOWED_PROPERTIES = {
    "職缺名稱", "職缺名稱(對外)", "職務類別", "縣市", "行政區", "行業別", 
    "全/兼職", "班別", "薪資", "工作內容(對外)", "狀態"
}

def clean_text_for_search(text: str) -> str:
    t = str(text or "").lower().replace("台", "臺")
    return re.sub(r'[\(\)（）\/\s\-_,，、\?!？！。🛵☀️🌙📦🏭🏬🍽️🔄]+', '', t)

def sanitize_uri(url: str) -> str:
    default_fallback = "https://tsaipei.netlify.app/#jobs"
    if not url or not isinstance(url, str):
        return default_fallback
    url = url.strip().replace("\r", "").replace("\n", "").replace(" ", "")
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("line://")):
        return default_fallback
    return url

def parse_notion_property(prop: dict) -> str:
    if not isinstance(prop, dict):
        return str(prop or "").strip()
    
    p_type = prop.get("type", "")
    
    if p_type == "title":
        return "".join([t.get("plain_text", "") for t in prop.get("title", [])]).strip()
    elif p_type == "multi_select":
        return ",".join([opt.get("name", "").strip() for opt in prop.get("multi_select", []) if opt.get("name")])
    elif p_type == "rich_text":
        return "".join([t.get("plain_text", "") for t in prop.get("rich_text", [])]).strip()
    elif p_type == "select":
        return prop.get("select", {}).get("name", "").strip() if prop.get("select") else ""
    elif p_type == "status":
        return prop.get("status", {}).get("name", "").strip() if prop.get("status") else ""
    elif p_type == "url":
        return prop.get("url", "") or ""
    elif p_type == "number":
        return str(prop.get("number", "")) if prop.get("number") is not None else ""
    elif p_type == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    elif p_type == "rollup":
        r_data = prop.get("rollup", {})
        r_type = r_data.get("type", "")
        if r_type == "array":
            extracted = [parse_notion_property(item) for item in r_data.get("array", []) if parse_notion_property(item)]
            return ",".join(extracted)
        elif r_type in ["string", "text"]:
            return str(r_data.get("string", "") or "").strip()
    elif p_type == "formula":
        f_data = prop.get("formula", {})
        f_type = f_data.get("type", "")
        if f_type in ["string", "text"]:
            return str(f_data.get("string", "") or "").strip()
            
    return ""

def query_notion_database_direct(database_id: str) -> list:
    if not NOTION_API_KEY or not database_id:
        return []

    clean_db_id = database_id.replace("-", "").strip()
    url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY.strip()}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        body_data = {"page_size": 100}
        if start_cursor:
            body_data["start_cursor"] = start_cursor

        req = urllib.request.Request(url, data=json.dumps(body_data).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                res_json = json.loads(res.read().decode("utf-8"))
                all_results.extend(res_json.get("results", []))
                has_more = res_json.get("has_more", False)
                start_cursor = res_json.get("next_cursor", None)
        except Exception as e:
            print(f"[Notion Direct Query 異常]: {e}")
            break

    return all_results

def fetch_jobs_data() -> list:
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    active_jobs = []
    try:
        results = query_notion_database_direct(NOTION_JOBS_DB_ID)

        for page in results:
            props = page.get("properties", {})
            page_id = page.get("id", "")
            job_dict = {"_page_id": page_id}
            raw_text_parts = []

            # 1. Title
            title_val = ""
            for p_name, p_val in props.items():
                if isinstance(p_val, dict) and p_val.get("type") == "title":
                    title_val = parse_notion_property(p_val)
                    break
            job_dict["職缺名稱"] = title_val

            # 2. Multi-Select 職務類別
            category_val = ""
            for p_name, p_val in props.items():
                if "類別" in p_name or "職務" in p_name:
                    category_val = parse_notion_property(p_val)
                    break
            job_dict["職務類別"] = category_val

            # 3. 其餘欄位
            for field_name in ALLOWED_PROPERTIES:
                if field_name in props and field_name not in ["職缺名稱", "職務類別"]:
                    val_str = parse_notion_property(props[field_name])
                    job_dict[field_name] = val_str

            # 4. 嚴格過濾「停招」
            status = str(job_dict.get("狀態", "")).strip()
            if status == "停招":
                continue

            for k, v in job_dict.items():
                if isinstance(v, str) and v and k != "狀態" and not k.startswith("_"):
                    raw_text_parts.append(v)

            public_title = job_dict.get("職缺名稱(對外)") or ""
            internal_title = job_dict.get("職缺名稱") or ""
            job_category = job_dict.get("職務類別") or ""
            display_title = public_title or internal_title or job_category
            
            if display_title:
                job_dict["_parsed_title"] = display_title
                job_dict["_internal_title"] = internal_title
                job_dict["_internal_title_clean"] = clean_text_for_search(internal_title)
                job_dict["_job_category"] = job_category
                job_dict["_job_category_clean"] = clean_text_for_search(job_category)
                job_dict["_raw_row_text"] = " ".join(raw_text_parts)
                job_dict["_search_text"] = clean_text_for_search(" ".join(raw_text_parts))
                active_jobs.append(job_dict)

        print(f"[Notion 職缺載入成功] 共載入 {len(active_jobs)} 筆招募中職缺！")
        _cached_jobs = active_jobs
        _last_jobs_fetch = now
        return active_jobs
    except Exception as e:
        print(f"[Notion 職缺讀取失敗]: {e}")
        return _cached_jobs or []

def fetch_faqs_data() -> list:
    global _cached_faqs, _last_faqs_fetch
    now = time.time()
    if _cached_faqs is not None and (now - _last_faqs_fetch < CACHE_TTL):
        return _cached_faqs

    faqs = []
    try:
        results = query_notion_database_direct(NOTION_FAQ_DB_ID)
        for page in results:
            props = page.get("properties", {})
            q_text, a_text, status = "", "", "啟用"
            
            for k, v in props.items():
                val = parse_notion_property(v)
                k_lower = k.lower()
                if any(x in k_lower for x in ["問", "題目", "問題", "question", "title"]):
                    q_text = val
                elif any(x in k_lower for x in ["答", "回覆", "內容", "answer", "content"]):
                    a_text = val
                elif any(x in k_lower for x in ["狀態", "啟用", "status"]):
                    status = val

            if status not in ["停用", "關閉", "false"] and q_text and a_text:
                faqs.append({"question": q_text, "answer": a_text})

        print(f"[Notion FAQ 載入成功] 共載入 {len(faqs)} 筆常見問答！")
        _cached_faqs = faqs
        _last_faqs_fetch = now
        return faqs
    except Exception as e:
        print(f"[Notion FAQ 讀取失敗]: {e}")
        return _cached_faqs or []

# ==========================================
# 5. 精準履歷路由與 Flex 卡片 (地點智慧過濾 + AI 美化說明)
# ==========================================
DEFAULT_RESUME_URLS = {
    "Spx": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU3B4IiwiU3lzdGVtIjoiWWVzIn0=?openExternalBrowser=1",
    "Service": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU2VydmljZSIsIlN5c3RlbSI6IlllcyJ9?openExternalBrowser=1",
    "Manufacture": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiTWFudWZhY3R1cmUiLCJTeXN0ZW0iOiJZZXMifQ==?openExternalBrowser=1"
}

def resolve_apply_url_by_industry(job: dict) -> str:
    full_search_text = f"{job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')} {job.get('職務類別', '')} {job.get('行業別', '')} {job.get('工作內容(對外)', '')}".lower()

    if any(k in full_search_text for k in ["蝦皮", "智取店", "店到店", "spx", "外送"]):
        return DEFAULT_RESUME_URLS["Spx"]

    if any(k in full_search_text for k in ["服務", "餐飲", "服飾", "門市", "專櫃", "店員", "廚助"]):
        return DEFAULT_RESUME_URLS["Service"]

    return DEFAULT_RESUME_URLS["Manufacture"]

def format_clean_location(job: dict, target_location: str) -> str:
    county = str(job.get("縣市") or "").strip()
    district = str(job.get("行政區") or "").strip()

    if target_location:
        dist_list = [d.strip() for d in re.split(r'[,，、\s]+', district) if d.strip()]
        for d in dist_list:
            if target_location in d or d in target_location:
                return f"{county.split(',')[0] if county else ''} {d}（可自選門市/廠區）".strip()
        
        county_list = [c.strip() for c in re.split(r'[,，、\s]+', county) if c.strip()]
        for c in county_list:
            if target_location in c or c in target_location:
                return f"{c} 各區門市/廠區均有缺額".strip()

    main_districts = [d.strip() for d in re.split(r'[,，、\s]+', district) if d.strip()]
    if main_districts:
        short_dist = "、".join(main_districts[:3])
        if len(main_districts) > 3:
            short_dist += " 等多區可自選"
        return short_dist
        
    return f"{county} 各區".strip() or "全台各區均可安排"

def create_job_flex_card(jobs: list, user_id: str, target_location: str = "") -> FlexSendMessage:
    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "industry": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "category": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:10]:
        job_id = str(job.get("_page_id") or "JOB").replace("-", "")[:8]
        job_title = str(job.get("職缺名稱(對外)") or job.get("職缺名稱") or job.get("職務類別") or "優質職缺").strip()
        
        display_location = format_clean_location(job, target_location)
        salary = str(job.get("薪資") or "依公司規定").strip()
        shift = str(job.get("班別") or "").strip()
        industry = str(job.get("行業別") or "").strip()
        job_type = str(job.get("全/兼職") or "").strip()
        job_category = str(job.get("職務類別") or "").strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift[:8], "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]})
        if job_category:
            first_cat = job_category.split(",")[0].strip()
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["category"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": first_cat[:8], "size": "xxs", "color": badge_styles["category"]["text"], "weight": "bold"}]})
        elif industry:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["industry"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": industry[:8], "size": "xxs", "color": badge_styles["industry"]["text"], "weight": "bold"}]})
        if job_type:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["type"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": job_type[:8], "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]})

        raw_desc = str(job.get("工作內容(對外)") or "").strip()
        polished_desc = polish_job_description_with_ai(raw_desc, job_title)
            
        final_apply_link = sanitize_uri(resolve_apply_url_by_industry(job))

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
                    {"type": "text", "text": f"📍 地點：{display_location}", "size": "sm", "color": "#444444", "wrap": True},
                    {"type": "text", "text": f"💰 待遇：{salary}", "size": "sm", "color": "#D32F2F", "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"✨ 特色：{polished_desc}", "size": "xs", "color": "#555555", "wrap": True, "margin": "xs"}
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
                        "action": {
                            "type": "message",
                            "label": "📖 了解詳細內容",
                            "text": f"查看職缺詳情 {job_title}"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#00B900",
                        "height": "sm",
                        "action": {"type": "uri", "label": "📄 填寫線上履歷", "uri": final_apply_link}
                    }
                ]
            }
        }
        bubbles.append(bubble)
        
    return FlexSendMessage(alt_text=f"為您找到 {len(bubbles)} 筆熱門職缺！", contents={"type": "carousel", "contents": bubbles})

# ==========================================
# 6. Gemini 決策核心
# ==========================================
def _tokenize_search_terms(text: str) -> list:
    """將自然語言拆成可用於本地候選職缺/FAQ 篩選的詞彙。"""
    normalized = clean_text_for_search(text)
    candidates = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東",
        "早班", "早上", "白班", "日班", "晚班", "小夜", "大夜", "夜班", "假日", "彈性",
        "外送", "司機", "配送", "送貨", "門市", "店員", "店到店", "智取店", "蝦皮", "momo", "富邦", "富昇",
        "理貨", "揀貨", "倉管", "作業員", "包裝", "產線", "倉儲", "餐飲", "服飾", "服務",
    ]
    return [k for k in candidates if clean_text_for_search(k) in normalized]


def _score_job_for_ai(job: dict, query_text: str, current_location: str = "", slots: dict = None) -> int:
    """只做候選排序，不改變原本職缺資料或既有精準攔截邏輯。"""
    slots = slots or {}
    search_text = job.get("_search_text", "")
    score = 0
    query_clean = clean_text_for_search(query_text)

    if current_location:
        loc = clean_text_for_search(current_location)
        if loc and loc in search_text:
            score += 30

    category = slots.get("category", "")
    for keyword in category_search_keywords(category):
        if clean_text_for_search(keyword) in search_text:
            score += 15

    shift = slots.get("shift", "")
    if shift and shift != "不限":
        shift_keywords = {
            "早班": ["早班", "早上", "白班", "日班"],
            "晚班": ["晚班", "小夜"],
            "大夜班": ["大夜", "夜班"],
            "假日班": ["假日"],
            "彈性排班": ["彈性", "排班"],
        }
        for keyword in shift_keywords.get(shift, [shift]):
            if clean_text_for_search(keyword) in search_text:
                score += 12
                break

    for term in _tokenize_search_terms(query_text):
        term_clean = clean_text_for_search(term)
        if term_clean and term_clean in search_text:
            score += 8

    title_clean = clean_text_for_search(job.get("_parsed_title", ""))
    category_clean = clean_text_for_search(job.get("職務類別", ""))
    if title_clean and title_clean in query_clean:
        score += 25
    if category_clean and category_clean in query_clean:
        score += 20
    return score


def build_ai_job_candidates(active_jobs: list, query_text: str, current_location: str = "", slots: dict = None, limit: int = 40) -> list:
    """保留既有 Notion 職缺來源與白名單機制，只在送 Gemini 前縮小候選集合。"""
    if not active_jobs:
        return []
    scored = [(_score_job_for_ai(job, query_text, current_location, slots), idx, job) for idx, job in enumerate(active_jobs)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    positive = [item for item in scored if item[0] > 0]
    selected = positive[:limit] if positive else scored[:limit]
    return [item[2] for item in selected]


def _score_faq_for_ai(faq: dict, query_text: str) -> int:
    q = clean_text_for_search(faq.get("question", ""))
    query = clean_text_for_search(query_text)
    if not q or not query:
        return 0
    score = 0
    if q in query or query in q:
        score += 50
    for term in _tokenize_search_terms(query_text):
        t = clean_text_for_search(term)
        if t and t in q:
            score += 10
    for i in range(max(0, len(query) - 1)):
        piece = query[i:i + 2]
        if piece and piece in q:
            score += 2
    return score


def build_ai_faq_candidates(faq_list: list, query_text: str, limit: int = 20) -> list:
    if not faq_list:
        return []
    scored = [(_score_faq_for_ai(faq, query_text), idx, faq) for idx, faq in enumerate(faq_list)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    positive = [item for item in scored if item[0] > 0]
    selected = positive[:limit] if positive else scored[:limit]
    return [item[2] for item in selected]


def query_gemini_ai(prompt: str) -> str:
    if not ai_client:
        return ""
    models = ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    for m in models:
        try:
            if hasattr(ai_client, 'models'):
                res = ai_client.models.generate_content(model=m, contents=prompt)
                if res and hasattr(res, 'text') and res.text:
                    return res.text.strip()
            elif hasattr(ai_client, 'interactions'):
                interaction = ai_client.interactions.create(model=m, input=prompt)
                if hasattr(interaction, 'text') and interaction.text:
                    return interaction.text.strip()
        except Exception as e:
            print(f"[Gemini 呼叫異常 {m}]: {e}")
            continue
    return ""

def extract_current_target_location(history_and_msg: str) -> str:
    locs = [
        "板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "林口", "泰山", "五股", "三峽", "鶯歌",
        "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "平鎮", "楊梅", "龍潭",
        "台北", "臺北", "新北", "台中", "臺中", "台南", "臺南", "高雄", "新竹", "彰化", "嘉義", "苗栗", "宜蘭", "屏東"
    ]
    for loc in locs:
        if loc in history_and_msg:
            return loc.replace("臺", "台")
    return ""

def extract_shift_preference(text: str) -> str:
    """從文字中判斷求職者偏好的時段/班別，供漸進式需求收集使用。"""
    shift_map = {
        "早班": ["早班", "早上班", "白班", "日班"],
        "晚班": ["晚班", "小夜"],
        "大夜班": ["大夜", "夜班", "大夜班"],
        "假日班": ["假日班", "假日"],
        "彈性排班": ["彈性排班", "自由排班", "排班彈性", "時段彈性", "不限時段"]
    }
    for label, keys in shift_map.items():
        if any(k in text for k in keys):
            return label
    return ""

def detect_category_label(clean_input: str) -> str:
    """從文字中判斷求職者偏好的工作類別/行業別，供漸進式需求收集使用（不影響步驟1原有的精準攔截邏輯）。"""
    if any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作", "司機"]):
        return "外送"
    if any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]):
        return "門市"
    if any(k in clean_input for k in ["momo", "富邦", "富昇"]):
        return "momo"
    if any(k in clean_input for k in ["理貨", "揀貨", "倉管", "作業員", "包裝", "產線"]):
        return "理貨/倉儲"
    return ""

def category_search_keywords(category_label: str) -> list:
    """依已知的工作類別 slot，回傳對應的搜尋關鍵字。
    用於「都給我看看/不限時段」等泛意圖情境下，仍要保留已確認過的類別條件，避免跳出不相干的職缺卡片（問題修正）。"""
    mapping = {
        "外送": ["外送", "司機", "配送"],
        "門市": ["門市", "店到店", "智取店", "蝦皮"],
        "momo": ["momo", "富邦", "富昇"],
        "理貨/倉儲": ["理貨", "倉管", "作業員", "包裝", "產線"]
    }
    return mapping.get(category_label, [])

def build_progressive_question(user_id: str, current_location: str) -> tuple:
    """
    像真人顧問一樣「一步一步」詢問還缺少的條件（地區 → 工作類別 → 時段），
    並回傳 (提問文字, QuickReplyButton 清單)。若三項條件皆已齊全則回傳空字串。
    """
    slots = get_user_slots(user_id)
    known_location = current_location or slots.get("location", "")
    known_category = slots.get("category", "")
    known_shift = slots.get("shift", "")

    # 缺少「地區」→ 優先詢問地區
    if not known_location:
        prefix = f"想找【{known_category}】類型的工作對嗎？😊\n\n" if known_category else "您好呀！我是招募顧問沛沛 😊\n\n"
        text = prefix + "請問您方便在【哪個地區】上班呢？（例如板橋、新莊、桃園等）"
        buttons = [
            QuickReplyButton(action=MessageAction(label="📍 板橋/新莊", text=f"新莊{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="📍 桃園/中壢", text=f"桃園{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="📍 台北/新北", text=f"台北{known_category}".strip())),
            QuickReplyButton(action=MessageAction(label="👀 都可以，先看看", text="都給我看看"))
        ]
        return text, buttons

    # 已知地區、缺少「工作類別/行業別」→ 詢問想找哪種工作
    if not known_category:
        text = f"好的，鎖定在【{known_location}】附近幫您找工作 📍\n\n請問您比較想找哪一種工作類型呢？"
        buttons = [
            QuickReplyButton(action=MessageAction(label="🛵 外送/司機", text=f"{known_location}外送")),
            QuickReplyButton(action=MessageAction(label="🏬 門市/店到店", text=f"{known_location}門市")),
            QuickReplyButton(action=MessageAction(label="📦 理貨/倉儲作業員", text=f"{known_location}理貨")),
            QuickReplyButton(action=MessageAction(label="👀 都可以，先看看", text="都給我看看"))
        ]
        return text, buttons

    # 地區與類別皆已知、缺少「時段班別」→ 詢問時段（最後一步）
    if not known_shift:
        text = f"了解！【{known_location}】的【{known_category}】職缺為您安排 😊\n\n請問時段班別上您有偏好嗎？（沒有特別限制也沒關係喔）"
        buttons = [
            QuickReplyButton(action=MessageAction(label="☀️ 早班", text=f"{known_location}{known_category}早班")),
            QuickReplyButton(action=MessageAction(label="🌙 晚班/大夜", text=f"{known_location}{known_category}晚班")),
            QuickReplyButton(action=MessageAction(label="🔄 不限時段，先看看", text="都給我看看"))
        ]
        return text, buttons

    return "", []

def process_user_message(event, target_line_bot_api: LineBotApi):
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id})")

    # 1. 讀取 Notion 職缺與 FAQ
    active_jobs = fetch_jobs_data()
    faq_list = fetch_faqs_data()

    # ---------------- 步驟 0-1：處理「了解詳細內容」（AI 排版美化 + 就業服務法審查） ----------------
    if raw_msg.startswith("查看職缺詳情"):
        target_title = raw_msg.replace("查看職缺詳情", "").strip()
        matched_job = None
        for j in active_jobs:
            if target_title and (target_title in j.get("_parsed_title", "") or j.get("_parsed_title", "") in target_title):
                matched_job = j
                break
        
        if not matched_job and active_jobs:
            matched_job = active_jobs[0]

        if matched_job:
            loc_display = format_clean_location(matched_job, "")
            apply_url = sanitize_uri(resolve_apply_url_by_industry(matched_job))
            
            # 透過 AI 進行內容排版與就業服務法審查
            formatted_detail = format_full_job_detail_with_ai(matched_job, loc_display)
            final_reply_text = f"{formatted_detail}\n\n👉 立即填寫線上履歷：\n{apply_url}"

            append_user_history(user_id, "招募顧問沛沛", final_reply_text)
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📄 立即線上應徵", text="我要應徵")),
                QuickReplyButton(action=MessageAction(label="📍 看看其他工作", text="都給我看看")),
                QuickReplyButton(action=MessageAction(label="💬 詢問發薪與福利", text="發薪日是什麼時候？"))
            ])
            target_line_bot_api.reply_message(reply_token, TextSendMessage(text=final_reply_text, quick_reply=quick_reply))
            return

    # 2. 載入對話歷史紀錄 (7天)
    history = get_user_history(user_id)
    history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history])
    full_conversation_context = f"{history_text}\n求職者: {raw_msg}"
    current_location = extract_current_target_location(full_conversation_context)
    clean_input = clean_text_for_search(raw_msg)

    # 漸進式需求收集：即時更新目前已掌握的地區 / 工作類別 / 時段 條件（供後續各步驟判斷是否還需繼續詢問）
    detected_shift = extract_shift_preference(raw_msg)
    detected_category_from_text = detect_category_label(clean_input)
    update_user_slots(user_id, location=current_location, category=detected_category_from_text, shift=detected_shift)

    # ---------------- 步驟 0-2：就業服務法合規防呆攔截 ----------------
    age_gender_keywords = ["年齡限制", "幾歲", "年紀", "年齡", "限女性", "限男性", "性別限制", "幾歲以上", "幾歲以下", "高齡", "中高齡"]
    if any(k in raw_msg for k in age_gender_keywords) and ("有嗎" in raw_msg or "可以嗎" in raw_msg or "限制" in raw_msg or "能不能" in raw_msg or "可以做嗎" in raw_msg):
        legal_reply = (
            "您好呀！我是招募顧問沛沛 😊\n\n"
            "依《就業服務法》規定，材霈所有職缺皆【無性別與年齡限制】，歡迎所有求職朋友應徵！\n\n"
            "各廠區與工作主要評估實際工作內容的勝任度（例如：需配合走動作業、搬重或輪班需求）。只要體能與出勤狀況可配合，都非常歡迎線上填寫履歷應徵喔！\n\n"
            "👉 請問您目前希望在【哪個地區】找工作？偏好早班或夜班呢？"
        )
        append_user_history(user_id, "招募顧問沛沛", legal_reply)
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="📍 新莊/新北", text="新莊工作")),
            QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
            QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
            QuickReplyButton(action=MessageAction(label="🏬 蝦皮門市", text="蝦皮門市"))
        ])
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=legal_reply, quick_reply=quick_reply))
        return

    # ---------------- 步驟 1：【最高優先級】精準「職務類別」多工種嚴格直達攔截 ----------------
    is_delivery_intent = any(k in clean_input for k in ["外送", "外送員", "配送員", "巡貨司機", "送貨司機", "外送工作"])
    is_store_intent = any(k in clean_input for k in ["門市", "店員", "門市人員", "蝦皮門市", "智取店", "店到店"]) and not is_delivery_intent
    is_momo_intent = any(k in clean_input for k in ["momo", "富邦", "富昇"])

    # ---------------- 步驟 1-0：像真人顧問一樣「漸進式需求收集」(地區 → 工作類別 → 時段) ----------------
    # 求職者一提到具體工種關鍵字時，先確認地區/類別/時段是否都已掌握；
    # 若求職者已明確表示「都可以/隨便/全部/看全部」，則視為主動略過詢問，直接放行到下方原有比對邏輯。
    show_all_bypass_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "看全部", "都看"]
    has_explicit_category_intent = is_delivery_intent or is_store_intent or is_momo_intent
    if has_explicit_category_intent and not any(k in clean_input for k in show_all_bypass_keywords):
        _slots_now = get_user_slots(user_id)
        _location_ready = bool(current_location or _slots_now.get("location"))
        _category_ready = bool(_slots_now.get("category"))
        _shift_ready = bool(_slots_now.get("shift"))
        if not (_location_ready and _category_ready and _shift_ready):
            question_text, question_buttons = build_progressive_question(user_id, current_location)
            if question_text:
                append_user_history(user_id, "求職者", raw_msg)
                append_user_history(user_id, "招募顧問沛沛", question_text)
                target_line_bot_api.reply_message(
                    reply_token,
                    TextSendMessage(text=question_text, quick_reply=QuickReply(items=question_buttons))
                )
                print(f"[漸進式需求收集] 條件尚未齊全（地區:{_location_ready} 類別:{_category_ready} 時段:{_shift_ready}），先引導求職者補充")
                return

    direct_matches = []

    # 1-1. 外送員 / 司機
    if is_delivery_intent:
        for j in active_jobs:
            cat = str(j.get("_job_category", "")).lower()
            int_t = str(j.get("_internal_title", "")).lower()
            pub_t = str(j.get("職缺名稱(對外)", "")).lower()
            if any(k in cat for k in ["外送", "司機", "配送"]) or any(k in int_t for k in ["外送", "司機", "配送"]) or any(k in pub_t for k in ["外送", "司機", "配送"]):
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", ""):
                        direct_matches.append(j)
                else:
                    direct_matches.append(j)
        if not direct_matches:
            for j in active_jobs:
                cat = str(j.get("_job_category", "")).lower()
                int_t = str(j.get("_internal_title", "")).lower()
                if any(k in cat for k in ["外送", "司機", "配送"]) or any(k in int_t for k in ["外送", "司機", "配送"]):
                    direct_matches.append(j)

    # 1-2. 門市人員 / 店到店
    elif is_store_intent:
        for j in active_jobs:
            cat = str(j.get("_job_category", "")).lower()
            int_t = str(j.get("_internal_title", "")).lower()
            pub_t = str(j.get("職缺名稱(對外)", "")).lower()
            if (any(k in cat for k in ["門市", "服務", "店員"]) or any(k in int_t for k in ["門市", "店到店", "智取店"]) or any(k in pub_t for k in ["門市", "店到店"])) and not ("外送" in int_t or "外送" in pub_t):
                if current_location:
                    loc_clean = current_location.replace("台", "臺")
                    if current_location in j.get("_search_text", "") or loc_clean in j.get("_search_text", ""):
                        direct_matches.append(j)
                else:
                    direct_matches.append(j)
        if not direct_matches:
            for j in active_jobs:
                int_t = str(j.get("_internal_title", "")).lower()
                pub_t = str(j.get("職缺名稱(對外)", "")).lower()
                if any(k in int_t for k in ["門市", "店到店", "智取店", "蝦皮"]) or any(k in pub_t for k in ["門市", "店到店"]):
                    if not ("外送" in int_t or "外送" in pub_t):
                        direct_matches.append(j)

    # 1-3. momo / 富邦 / 富昇
    elif is_momo_intent:
        if current_location:
            loc_clean = current_location.replace("台", "臺")
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["momo", "富邦", "富昇"]) and (current_location in s_text or loc_clean in s_text):
                    direct_matches.append(j)
        if not direct_matches:
            for j in active_jobs:
                if any(k in j.get("_search_text", "") for k in ["momo", "富邦", "富昇"]):
                    direct_matches.append(j)

    if direct_matches:
        reply_text = f"有的！沛沛為您找到符合條件的推薦職缺囉，歡迎點擊下方「了解詳細內容」或填寫線上履歷應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(direct_matches[:3], user_id, current_location)])
        print(f"[最高優先級精準職務類別命中] 成功推播 {len(direct_matches)} 筆職缺！")
        return

    # ---------------- 步驟 2：【泛意圖與全部瀏覽攔截】（「都給我看看」、「都可以」） ----------------
    show_all_keywords = ["都給我看", "都要看", "都可以", "全部", "隨便", "推薦一下", "有什麼工作", "還有什麼", "看全部", "都看"]
    if any(k in clean_input for k in show_all_keywords):
        matched_show_all = []
        for j in active_jobs:
            s_text = j.get("_search_text", "")
            if current_location:
                loc_clean = current_location.replace("台", "臺")
                if current_location in s_text or loc_clean in s_text:
                    matched_show_all.append(j)
            else:
                matched_show_all.append(j)

        # 【問題修正 1】若對話中已掌握求職者指定的工作類別（例如先前已表示要找外送），
        # 「都給我看看/不限時段」等泛意圖詞語意上只代表「地區/時段不限」，不代表放棄原本的工作類別。
        # 這裡在既有地區篩選結果之上，再依已知類別關鍵字進一步收斂，避免跳出不相干的職缺卡片；
        # 若收斂後查無結果，才退回原本純地區範圍的結果，避免完全沒有卡片可看。
        _known_category_for_filter = get_user_slots(user_id).get("category", "")
        _category_kw = category_search_keywords(_known_category_for_filter)
        if _category_kw:
            _narrowed_by_category = [j for j in matched_show_all if any(k in j.get("_search_text", "") for k in _category_kw)]
            if _narrowed_by_category:
                matched_show_all = _narrowed_by_category

        if not matched_show_all:
            matched_show_all = active_jobs[:3]

        # 求職者已明確表示不限，順手補齊尚未回答的條件，避免之後又重複詢問同樣的問題
        _slots_to_complete = get_user_slots(user_id)
        if not _slots_to_complete.get("category"):
            _slots_to_complete["category"] = "不限"
        if not _slots_to_complete.get("shift"):
            _slots_to_complete["shift"] = "不限"

        reply_text = f"沒問題！沛沛馬上為您整理{current_location if current_location else ''}目前招募中的熱門職缺，歡迎點擊查看詳細說明或線上應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_show_all[:5], user_id, current_location)])
        print(f"[泛意圖攔截命中] 成功推播 {len(matched_show_all[:5])} 筆職缺！")
        return

    # ---------------- 步驟 3：組合「候選」Notion 職缺/FAQ 索引給 Gemini 進行多輪推理 ----------------
    # Notion 仍是唯一職缺/FAQ 資料來源；既有 Notion 權限與白名單設定不異動。
    # 這裡只在送 Gemini 前縮小候選集合，降低 Token 與延遲，不改變前面的精準攔截邏輯。
    _current_slots_for_candidates = get_user_slots(user_id)
    ai_job_candidates = build_ai_job_candidates(
        active_jobs,
        f"{history_text} {raw_msg}",
        current_location,
        _current_slots_for_candidates,
        limit=40,
    )
    ai_faq_candidates = build_ai_faq_candidates(faq_list, raw_msg, limit=20)

    job_index_text = ""
    for idx, j in enumerate(ai_job_candidates):
        public_t = j.get("職缺名稱(對外)", "")
        internal_t = j.get("職缺名稱", "")
        cat_t = j.get("職務類別", "")
        loc = f"{j.get('縣市', '')}{j.get('行政區', '')}"
        shift = j.get("班別", "")
        ind = j.get("行業別", "")
        salary = j.get("薪資", "")
        desc = j.get("工作內容(對外)", "")
        job_index_text += f"[ID:{idx}] 職缺唯一ID:{j.get('_page_id', '')} | 內部名稱:{internal_t} | 職務類別:{cat_t} | 對外名稱:{public_t} | 地點:{loc} | 行業:{ind} | 班別:{shift} | 待遇:{salary} | 說明:{desc}\n"

    faq_index_text = ""
    for f in ai_faq_candidates:
        faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

    _current_slots = get_user_slots(user_id)
    slot_location_text = current_location or _current_slots.get("location") or "尚未提供"
    slot_category_text = _current_slots.get("category") or "尚未提供"
    slot_shift_text = _current_slots.get("shift") or "尚未提供"

    ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的真人在線人資招募顧問（名字叫「沛沛」）。
你的目標是：結合過去 7 天的對話歷史，以真人顧問口吻引導求職者，並在資料庫中有符合職缺時推薦。

【極重要規則（絕對禁止幻覺）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【禁止擅自宣稱額滿或沒有職缺】：只要下方清單中存在該工種/職務類別（包含門市人員、外送員、司機、理貨、作業員等），一律視為開放招募中並直接推薦（ACTION:RECOMMEND）！
3. 【職務類別精確辨識】：
   - 求職者詢問「門市/店面」，推薦【職務類別:門市人員/店員】之職缺。
   - 求職者詢問「外送/司機」，推薦【職務類別:外送員/司機】之職缺。
4. 【求職者想看全部/隨便/都可以】：若求職者說「都給我看看」、「都可以」、「全部」，請直接推薦目前地區的所有職缺（ACTION:RECOMMEND），絕對不要繼續反問！
5. 【情境與按鈕規則】：
   - 目前對話鎖定的地區是：【{current_location if current_location else "未指定"}】。
   - 按鈕請一律圍繞該地區推薦，絕對不要跨縣市跳出不相干按鈕。
6. 【漸進式需求收集原則（像真人顧問一步一步了解需求）】：
   - 目前已掌握的條件 → 地區：【{slot_location_text}】、工作類別/行業別：【{slot_category_text}】、時段班別：【{slot_shift_text}】。
   - 除非求職者已明確表示「不限地區/都可以/隨便/全部/都給我看看」，否則請依序一次只確認一項缺少的條件：① 地區 → ② 工作類別/行業別 → ③ 時段班別，語氣自然親切，不要一次條列三個問題。
   - 只有在地區、工作類別/行業別、時段班別三項都已掌握（或求職者已表示不限/都可以），才可以輸出 ACTION:RECOMMEND；否則請輸出 ACTION:ASK，並在 REPLY 中只詢問「尚未提供」的那一項，絕對不要重複詢問已經掌握的條件。
   - 此原則不影響規則 2-4：只要條件確認齊全（或求職者表示都可以），只要清單中有符合的職缺，依然要直接推薦，不得宣稱額滿或無此職缺。

【公司官方常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（暫無額外 FAQ）"}

【目前公司招募中的職缺清單】：
{job_index_text if job_index_text else "（目前公司在全台灣北、中、南區均有開放各類優質職缺）"}

【過去對話歷史】：
{history_text if history_text else "（對話剛開始）"}

【求職者剛說的話】：
「{raw_msg}」

【決策指令】：
請分析上下文，輸出下列其中一種格式：

格式 A（求職者剛打招呼、詢問FAQ、詢問公司名稱、或條件仍需進一步引導）：
ACTION:ASK
REPLY:（以沛沛口吻親切回答，約 40-70 字）
BUTTONS:（緊扣目前地區與當前話題的 3-5 個按鈕，逗號分隔）

格式 B（求職者條件在清單中有符合的職缺，或求職者表示都可以/都給我看）：
ACTION:RECOMMEND
IDS:（符合的職缺數字，例如 0 或 0,1）
REPLY:（給求職者的溫暖推薦語）

格式 C（全台清單中確實完全沒有符合該條件的職缺）：
ACTION:NO_MATCH
REPLY:（以沛沛口吻說明目前暫無開放，並主動推薦同一地區的其他優質職缺）
BUTTONS:（提供目前所在地區的其他工種或班別選項）

請直接輸出："""

    ai_output = query_gemini_ai(ai_prompt)
    print(f"[Gemini 決策輸出]:\n{ai_output}\n")

    append_user_history(user_id, "求職者", raw_msg)

    # 4. 解析 AI 輸出
    if "ACTION:RECOMMEND" in ai_output:
        ids_match = re.search(r'IDS:\s*([0-9,\s]+)', ai_output)
        reply_match = re.search(r'REPLY:\s*(.+)', ai_output, re.DOTALL)
        reply_text = reply_match.group(1).strip() if reply_match else "太棒了！沛沛為您推薦以下符合需求的職缺："
        append_user_history(user_id, "招募顧問沛沛", reply_text)

        matched_jobs = []
        if ids_match:
            indices = [int(n.strip()) for n in ids_match.group(1).split(",") if n.strip().isdigit() and int(n.strip()) < len(ai_job_candidates)]
            matched_jobs = [ai_job_candidates[i] for i in indices]

        if not matched_jobs:
            # AI 輸出格式異常時，優先使用本次候選集合；完全沒有候選才沿用原本全庫前三筆保底。
            matched_jobs = ai_job_candidates[:3] if ai_job_candidates else active_jobs[:3]

        flex_card = create_job_flex_card(matched_jobs, user_id, current_location)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), flex_card])
        return

    elif "ACTION:ASK" in ai_output or "ACTION:NO_MATCH" in ai_output:
        reply_match = re.search(r'REPLY:\s*(.+?)(?=\nBUTTONS:|$)', ai_output, re.DOTALL)
        buttons_match = re.search(r'BUTTONS:\s*(.+)', ai_output)

        reply_text = reply_match.group(1).strip() if reply_match else f"您好呀！沛沛隨時為您服務，想請問您偏好哪個班別或工作類型呢？"
        append_user_history(user_id, "招募顧問沛沛", reply_text)

        buttons = []
        if buttons_match:
            raw_buttons = [b.strip() for b in buttons_match.group(1).split(",") if b.strip()]
            for b_label in raw_buttons[:6]:
                clean_txt = re.sub(r'^[📍☀️🌙📦🏭🏬🍽️🔄🛵\s]+', '', b_label)
                buttons.append(QuickReplyButton(action=MessageAction(label=b_label[:20], text=clean_txt)))
        
        # 本地情境防呆按鈕生成
        if not buttons:
            if current_location:
                buttons = [
                    QuickReplyButton(action=MessageAction(label=f"☀️ {current_location}早班", text=f"{current_location}早班")),
                    QuickReplyButton(action=MessageAction(label=f"🌙 {current_location}夜班", text=f"{current_location}夜班")),
                    QuickReplyButton(action=MessageAction(label=f"📦 {current_location}理貨", text=f"{current_location}理貨")),
                    QuickReplyButton(action=MessageAction(label=f"🛵 {current_location}外送", text=f"{current_location}外送")),
                    QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
                ]
            else:
                buttons = [
                    QuickReplyButton(action=MessageAction(label="📍 台北/新北", text="台北工作")),
                    QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                    QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                    QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
                    QuickReplyButton(action=MessageAction(label="🏬 蝦皮門市", text="蝦皮門市"))
                ]

        quick_reply = QuickReply(items=buttons)
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
        return

    # 5. 智慧本地多輪上下文合流容錯檢索
    print("[執行智慧本地多輪容錯比對]")
    combined_query = clean_text_for_search(history_text + " " + raw_msg)
    matched_jobs = []

    for j in active_jobs:
        s_text = j.get("_search_text", "")
        if any(k in combined_query for k in ["外送", "司機", "配送", "送貨"]) and any(k in s_text for k in ["外送", "司機", "配送", "送貨"]):
            matched_jobs.append(j)
            continue
        if any(k in combined_query for k in ["momo", "富邦", "富昇"]) and any(k in s_text for k in ["momo", "富邦", "富昇"]):
            matched_jobs.append(j)
            continue
        if any(k in combined_query for k in ["蝦皮", "門市", "店到店"]) and any(k in s_text for k in ["蝦皮", "門市", "店到店"]):
            matched_jobs.append(j)
            continue
        tokens = [t for t in ["板橋", "新莊", "三重", "台北", "新北", "桃園", "中壢", "龜山", "早班", "夜班", "理貨", "作業員"] if t in combined_query]
        if tokens and all(t in s_text for t in tokens):
            matched_jobs.append(j)

    if matched_jobs:
        reply_text = "太棒了！沛沛為您找到以下符合條件的推薦職缺，歡迎點擊下方「了解詳細內容」或線上應徵喔 😊"
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_jobs[:3], user_id, current_location)])
        return

    # 預設引導：優先以漸進式提問（依目前已知的地區/類別/時段客製化問句），像真人顧問一步一步了解需求
    progressive_text, progressive_buttons = build_progressive_question(user_id, current_location)
    if progressive_text:
        append_user_history(user_id, "招募顧問沛沛", progressive_text)
        target_line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=progressive_text, quick_reply=QuickReply(items=progressive_buttons))
        )
        return

    # 條件皆已齊全但仍未匹配到任何職缺時的保底引導語（原有邏輯，維持不變）
    default_text = "您好呀！我是招募顧問沛沛 😊\n\n很高興為您服務！想了解您偏好在哪個地區上班？或是哪種工作類型與班別呢？"
    append_user_history(user_id, "招募顧問沛沛", default_text)
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📍 台北/新北", text="台北工作")),
        QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
        QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
        QuickReplyButton(action=MessageAction(label="📦 momo理貨", text="momo理貨")),
        QuickReplyButton(action=MessageAction(label="👀 都給我看看", text="都給我看看"))
    ])
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=default_text, quick_reply=quick_reply))

# ==========================================
# 7. Webhook 路由端點
# ==========================================
@app.get("/")
def health_check():
    return {"status": "ok", "service": "Tsaipei AI Recruitment Consultant (PeiPei Legal & Formatted Detail Engine) is running."}

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