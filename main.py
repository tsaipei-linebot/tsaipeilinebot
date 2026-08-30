import os
import re
import time
import json
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from google import genai
from notion_client import Client

load_dotenv()

app = FastAPI(title="Tsaipei AI Recruitment Consultant - Precision Status Engine", version="8.2.0")

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

notion_client = None
if NOTION_API_KEY:
    try:
        notion_client = Client(auth=NOTION_API_KEY)
        print("[系統提示] Notion API 客戶端初始化成功！")
    except Exception as e:
        print(f"[系統警告] Notion 初始化失敗: {e}")

# ==========================================
# 2. 對話記憶體快取 (Session Context - 7 天)
# ==========================================
user_sessions = {}
SESSION_TTL = 7 * 24 * 3600

def get_user_history(user_id: str) -> list:
    now = time.time()
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if now - session["last_time"] < SESSION_TTL:
            session["last_time"] = now
            return session["messages"]
    user_sessions[user_id] = {"last_time": now, "messages": []}
    return user_sessions[user_id]["messages"]

def append_user_history(user_id: str, role: str, text: str):
    history = get_user_history(user_id)
    history.append({"role": role, "text": text})
    if len(history) > 10:
        history.pop(0)

# ==========================================
# 3. 職缺內容智慧去噪與重點摘要模組
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
        
    if any(k in title for k in ["外送", "司機", "配送"]):
        return "負責指定區域包裹快遞配送作業，趟次穩定，享高額趟次津貼！"
    elif any(k in title for k in ["momo", "富邦", "富昇"]):
        return "知名電商物流中心，負責商品揀貨、包裝與出貨作業！"
    elif "蝦皮" in title:
        return "負責蝦皮門市包裹點交、進出貨盤點與顧客接待，工作環境單純！"
    elif any(k in title for k in ["理貨", "揀貨", "倉", "物流"]):
        return "負責商品分揀、理貨貼標與包裝出貨，免經驗環境佳！"
    elif any(k in title for k in ["作業員", "包裝", "組裝", "產線", "技術員"]):
        return "負責機台操作、產品組裝檢驗與成品包裝，免經驗可！"
        
    return f"開放應徵【{title}】，工作環境單純，歡迎點擊下方履歷應徵！"

# ==========================================
# 4. Notion 資料庫讀取 (嚴格僅排除「停招」+ 完整翻頁讀取)
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

def parse_notion_property(prop: dict) -> str:
    """全面解析 Notion 所有屬性結構"""
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

def fetch_jobs_data() -> list:
    global _cached_jobs, _last_jobs_fetch
    now = time.time()
    if _cached_jobs is not None and (now - _last_jobs_fetch < CACHE_TTL):
        return _cached_jobs

    if not notion_client or not NOTION_JOBS_DB_ID:
        print("[Notion 警告] 未設定 NOTION_API_KEY 或 NOTION_JOBS_DB_ID")
        return _cached_jobs or []

    active_jobs = []
    try:
        has_more = True
        start_cursor = None

        while has_more:
            query_kwargs = {"database_id": NOTION_JOBS_DB_ID, "page_size": 100}
            if start_cursor:
                query_kwargs["start_cursor"] = start_cursor

            response = notion_client.databases.query(**query_kwargs)

            for page in response.get("results", []):
                props = page.get("properties", {})
                page_id = page.get("id", "")
                job_dict = {"_page_id": page_id}
                raw_text_parts = []

                # 1. 自動偵測 Notion Title 欄位（內部職缺名稱）
                title_val = ""
                for p_name, p_val in props.items():
                    if isinstance(p_val, dict) and p_val.get("type") == "title":
                        title_val = parse_notion_property(p_val)
                        break
                job_dict["職缺名稱"] = title_val

                # 2. 自動偵測 Notion Multi-Select 職務類別欄位
                category_val = ""
                for p_name, p_val in props.items():
                    if "類別" in p_name or "職務" in p_name:
                        category_val = parse_notion_property(p_val)
                        break
                job_dict["職務類別"] = category_val

                # 3. 讀取其他白名單欄位
                for field_name in ALLOWED_PROPERTIES:
                    if field_name in props and field_name not in ["職缺名稱", "職務類別"]:
                        val_str = parse_notion_property(props[field_name])
                        job_dict[field_name] = val_str

                # 4. 【核心規則】：只有狀態精準為「停招」才過濾，其餘一律視為有缺招募中！
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

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor", None)

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

    if not notion_client or not NOTION_FAQ_DB_ID:
        return _cached_faqs or []

    faqs = []
    try:
        response = notion_client.databases.query(database_id=NOTION_FAQ_DB_ID, page_size=100)
        for page in response.get("results", []):
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
# 5. 精準履歷路由與 Flex 卡片
# ==========================================
DEFAULT_RESUME_URLS = {
    "Spx": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU3B4IiwiU3lzdGVtIjoiWWVzIn0=?openExternalBrowser=1",
    "Service": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU2VydmljZSIsIlN5c3RlbSI6IlllcyJ9?openExternalBrowser=1",
    "Manufacture": "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiTWFudWZhY3R1cmUiLCJTeXN0ZW0iOiJZZXMifQ==?openExternalBrowser=1"
}

def resolve_apply_url_by_industry(job: dict, faq_list: list) -> str:
    full_search_text = f"{job.get('職缺名稱(對外)', '')} {job.get('職缺名稱', '')} {job.get('職務類別', '')} {job.get('行業別', '')} {job.get('工作內容(對外)', '')}".lower()

    if any(k in full_search_text for k in ["蝦皮", "智取店", "店到店", "spx", "外送"]):
        for f in faq_list:
            q = f.get("question", "")
            ans = f.get("answer", "").strip()
            if any(k in q for k in ["蝦皮", "spx", "智取店"]) and (ans.startswith("http://") or ans.startswith("https://")):
                return ans
        return DEFAULT_RESUME_URLS["Spx"]

    if any(k in full_search_text for k in ["服務", "餐飲", "服飾", "門市", "專櫃", "店員", "廚助"]):
        for f in faq_list:
            q = f.get("question", "")
            ans = f.get("answer", "").strip()
            if any(k in q for k in ["服務", "餐飲", "服飾"]) and (ans.startswith("http://") or ans.startswith("https://")):
                return ans
        return DEFAULT_RESUME_URLS["Service"]

    if any(k in full_search_text for k in ["製造", "科技", "物流", "電子", "工業", "作業員", "包裝", "組裝", "理貨", "技術員", "momo", "富邦"]):
        for f in faq_list:
            q = f.get("question", "")
            ans = f.get("answer", "").strip()
            if any(k in q for k in ["製造", "科技", "物流"]) and (ans.startswith("http://") or ans.startswith("https://")):
                return ans
        return DEFAULT_RESUME_URLS["Manufacture"]

    for f in faq_list:
        q = f.get("question", "")
        ans = f.get("answer", "").strip()
        if any(k in q for k in ["線上履歷", "履歷連結", "預設", "通用"]) and (ans.startswith("http://") or ans.startswith("https://")):
            return ans

    return DEFAULT_RESUME_URLS["Manufacture"]

def create_job_flex_card(jobs: list, user_id: str, faq_list: list) -> FlexSendMessage:
    bubbles = []
    badge_styles = {
        "shift": {"bg": "#E8F5E9", "text": "#2E7D32"},
        "industry": {"bg": "#E3F2FD", "text": "#1565C0"},
        "type": {"bg": "#FFF3E0", "text": "#E65100"},
        "pay": {"bg": "#F3E5F5", "text": "#7B1FA2"}
    }

    for job in jobs[:10]:
        job_id = str(job.get("_page_id") or "JOB").replace("-", "")[:8]
        job_title = str(job.get("職缺名稱(對外)") or job.get("職缺名稱") or job.get("職務類別") or "優質職缺").strip()
        
        county = str(job.get("縣市") or "").strip()
        district = str(job.get("行政區") or "").strip()
        location = f"{county} {district}".strip() or "全台各廠區"
        
        salary = str(job.get("薪資") or "依公司規定").strip()
        shift = str(job.get("班別") or "").strip()
        industry = str(job.get("行業別") or "").strip()
        job_type = str(job.get("全/兼職") or "").strip()
        
        tags_contents = []
        if shift:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["shift"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": shift[:8], "size": "xxs", "color": badge_styles["shift"]["text"], "weight": "bold"}]})
        if industry:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["industry"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": industry[:8], "size": "xxs", "color": badge_styles["industry"]["text"], "weight": "bold"}]})
        if job_type:
            tags_contents.append({"type": "box", "layout": "horizontal", "backgroundColor": badge_styles["type"]["bg"], "cornerRadius": "sm", "paddingAll": "xs", "paddingStart": "sm", "paddingEnd": "sm", "contents": [{"type": "text", "text": job_type[:8], "size": "xxs", "color": badge_styles["type"]["text"], "weight": "bold"}]})

        raw_desc = str(job.get("工作內容(對外)") or "").strip()
        clean_desc = extract_smart_summary(raw_desc, job_title)
            
        website_job_url = "https://tsaipei.netlify.app/#jobs"
        base_apply_url = resolve_apply_url_by_industry(job, faq_list)
        
        connector = "&" if "?" in base_apply_url else "?"
        final_apply_link = f"{base_apply_url}{connector}job_id={job_id}&line_id={user_id}"

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
                    {"type": "text", "text": f"📝 說明：{clean_desc}", "size": "xs", "color": "#555555", "wrap": True, "margin": "xs"}
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
def query_gemini_ai(prompt: str) -> str:
    if not ai_client:
        return ""
    models = ["gemini-3.6-flash", "gemini-3.5-flash"]
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
    locs = ["板橋", "新莊", "三重", "中和", "永和", "土城", "蘆洲", "樹林", "汐止", "台北", "臺北", "新北", "桃園", "中壢", "龜山", "蘆竹", "大園", "八德", "台中", "臺中", "台南", "臺南", "高雄"]
    for loc in locs:
        if loc in history_and_msg:
            return loc.replace("臺", "台")
    return ""

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

    # 2. 載入對話歷史紀錄 (7天)
    history = get_user_history(user_id)
    history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history])
    full_conversation_context = f"{history_text}\n求職者: {raw_msg}"
    current_location = extract_current_target_location(full_conversation_context)
    clean_input = clean_text_for_search(raw_msg)

    # ---------------- 步驟 0：就業服務法合規防呆攔截 ----------------
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

    # ---------------- 步驟 1：【最高優先級】全能精準工種直達攔截 ----------------
    direct_matches = []
    
    # 1-1. 蝦皮外送 / 外送員 / 司機 / 配送
    if any(k in clean_input for k in ["外送", "外送員", "配送", "司機", "送貨"]):
        # 優先搜尋特定區域
        if current_location:
            loc_clean = current_location.replace("台", "臺")
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["外送", "司機", "配送", "送貨"]) and (current_location in s_text or loc_clean in s_text):
                    direct_matches.append(j)
        # 若指定地區沒有，直接抓取全台的外送職缺推薦
        if not direct_matches:
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["外送", "司機", "配送", "送貨"]):
                    direct_matches.append(j)

    # 1-2. 蝦皮門市 / 蝦皮店到店 / 智取店
    elif any(k in clean_input for k in ["蝦皮", "店到店", "智取店", "蝦皮門市"]):
        if current_location:
            loc_clean = current_location.replace("台", "臺")
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["蝦皮", "店到店", "智取店"]) and (current_location in s_text or loc_clean in s_text):
                    direct_matches.append(j)
        if not direct_matches:
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["蝦皮", "店到店", "智取店"]):
                    direct_matches.append(j)

    # 1-3. momo / 富邦 / 富昇
    elif any(k in clean_input for k in ["momo", "富邦", "富昇"]):
        if current_location:
            loc_clean = current_location.replace("台", "臺")
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["momo", "富邦", "富昇"]) and (current_location in s_text or loc_clean in s_text):
                    direct_matches.append(j)
        if not direct_matches:
            for j in active_jobs:
                s_text = j.get("_search_text", "")
                if any(k in s_text for k in ["momo", "富邦", "富昇"]):
                    direct_matches.append(j)

    if direct_matches:
        reply_text = f"有的！沛沛為您找到符合條件的推薦職缺囉，歡迎點擊下方查看簡章或線上填寫履歷應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(direct_matches[:3], user_id, faq_list)])
        print(f"[最高優先級直達命中] 成功推播 {len(direct_matches)} 筆職缺！")
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
                
        if not matched_show_all:
            matched_show_all = active_jobs[:3]

        reply_text = f"沒問題！沛沛馬上為您整理{current_location if current_location else ''}目前招募中的熱門職缺，歡迎點擊查看簡章或線上應徵喔 😊"
        append_user_history(user_id, "求職者", raw_msg)
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_show_all[:5], user_id, faq_list)])
        print(f"[泛意圖攔截命中] 成功推播 {len(matched_show_all[:5])} 筆職缺！")
        return

    # ---------------- 步驟 3：組合 Notion 職缺索引給 Gemini 進行多輪推理 ----------------
    job_index_text = ""
    for idx, j in enumerate(active_jobs):
        public_t = j.get("職缺名稱(對外)", "")
        internal_t = j.get("職缺名稱", "")
        cat_t = j.get("職務類別", "")
        loc = f"{j.get('縣市', '')}{j.get('行政區', '')}"
        shift = j.get('班別', '')
        ind = j.get('行業別', '')
        salary = j.get('薪資', '')
        desc = j.get('工作內容(對外)', '')
        job_index_text += f"[ID:{idx}] 內部職缺名稱:{internal_t} | 職務類別:{cat_t} | 對外名稱:{public_t} | 地點:{loc} | 行業:{ind} | 班別:{shift} | 待遇:{salary} | 說明:{desc}\n"

    faq_index_text = ""
    for idx, f in enumerate(faq_list):
        faq_index_text += f"問：{f.get('question')} => 答：{f.get('answer')}\n"

    ai_prompt = f"""你是一位「材霈有限公司」非常親切、高情商的真人在線人資招募顧問（名字叫「沛沛」）。
你的目標是：結合過去 7 天的對話歷史，以真人顧問口吻引導求職者，並在資料庫中有符合職缺時推薦。

【極重要規則（絕對禁止幻覺）】：
1. 自稱一律為「沛沛」。遵守就業服務法（無年齡性別限制）。
2. 【禁止擅自宣稱額滿或沒有職缺】：只要下方清單中存在該工種/職務類別（包含外送員、司機、蝦皮門市、理貨、作業員等），一律視為開放招募中並直接推薦（ACTION:RECOMMEND）！
3. 【全欄位比對】：比對【內部職缺名稱】、【職務類別】、【對外名稱】與【說明】。
4. 【求職者想看全部/隨便/都可以】：若求職者說「都給我看看」、「都可以」、「全部」，請直接推薦目前地區的所有職缺（ACTION:RECOMMEND），絕對不要繼續反問！
5. 【情境與按鈕規則】：
   - 目前對話鎖定的地區是：【{current_location if current_location else "未指定"}】。
   - 按鈕請一律圍繞該地區推薦，絕對不要跨縣市跳出不相干按鈕。

【公司官方常見問題庫 (FAQ)】：
{faq_index_text if faq_index_text else "（暫無額外 FAQ）"}

【目前公司招募中的職缺清單 (已全部載入)】：
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
            indices = [int(n.strip()) for n in ids_match.group(1).split(",") if n.strip().isdigit() and int(n.strip()) < len(active_jobs)]
            matched_jobs = [active_jobs[i] for i in indices]

        if not matched_jobs and active_jobs:
            matched_jobs = active_jobs[:3]

        flex_card = create_job_flex_card(matched_jobs, user_id, faq_list)
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
        if any(k in combined_query for k in ["蝦皮", "店到店"]) and any(k in s_text for k in ["蝦皮", "店到店"]):
            matched_jobs.append(j)
            continue
        tokens = [t for t in ["板橋", "新莊", "三重", "台北", "新北", "桃園", "中壢", "龜山", "早班", "夜班", "理貨", "作業員"] if t in combined_query]
        if tokens and all(t in s_text for t in tokens):
            matched_jobs.append(j)

    if matched_jobs:
        reply_text = "太棒了！沛沛為您找到以下符合條件的推薦職缺，歡迎點擊下方查看簡章或線上應徵喔 😊"
        append_user_history(user_id, "招募顧問沛沛", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_jobs[:3], user_id, faq_list)])
        return

    # 預設引導
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
    return {"status": "ok", "service": "Tsaipei AI Recruitment Consultant (PeiPei Status Precision Engine) is running."}

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