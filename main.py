import os
import re
import csv
import io
import time
import datetime
import urllib.request
import json
import pytz
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException
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

app = FastAPI(title="Tsaipei AI Recruitment Consultant", version="6.6.0")

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
# 2. 對話記憶體快取 (Session Context)
# ==========================================
user_sessions = {}
SESSION_TTL = 900

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
    if len(history) > 8:
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
        
    if any(k in title for k in ["理貨", "揀貨", "倉", "物流"]):
        return "負責商品分揀、理貨貼標與包裝出貨，免經驗環境佳！"
    elif any(k in title for k in ["作業員", "包裝", "組裝", "產線", "技術員"]):
        return "負責機台操作、產品組裝檢驗與成品包裝，免經驗可！"
    elif any(k in title for k in ["司機", "外送", "物流士"]):
        return "負責貨物配送與點交作業，出勤穩定，享優渥津貼！"
        
    return f"開放應徵【{title}】，工作環境單純，歡迎點擊下方履歷應徵！"

# ==========================================
# 4. Google Sheets 資料庫直連模組
# ==========================================
CACHE_TTL = 30
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
                    title = row[1].strip()

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
# 5. 雙按鈕 + 4 標籤 Flex 卡片
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
        location = f"{county} {district}".strip() or "全台各廠區"
        
        salary = str(job.get("薪資") or job.get("薪資待遇") or "依公司規定").strip()
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
        clean_desc = extract_smart_summary(raw_desc, job_title)
            
        website_job_url = "https://tsaipei.netlify.app/#jobs"[cite: 1]
        raw_resume_url = str(job.get("線上履歷網址") or job.get("線上履歷連結") or "").strip()
        if raw_resume_url.startswith("http://") or raw_resume_url.startswith("https://"):
            separator = "&" if "?" in raw_resume_url else "?"
            apply_link = f"{raw_resume_url}{separator}job_id={job_id}&line_id={user_id}"
        else:
            apply_link = "https://tsaipei.netlify.app/#jobs"[cite: 1]

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
                        "action": {"type": "uri", "label": "📄 填寫線上履歷", "uri": apply_link}
                    }
                ]
            }
        }
        bubbles.append(bubble)
        
    return FlexSendMessage(alt_text=f"為您找到 {len(bubbles)} 筆熱門職缺！", contents={"type": "carousel", "contents": bubbles})

# ==========================================
# 6. Gemini 真人顧問決策核心 (支援 3.6 / 3.5 模型)
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

def process_user_message(event, target_line_bot_api: LineBotApi):
    reply_token = event.reply_token
    if reply_token in ["00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"]:
        return

    raw_msg = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', 'USER')
    print(f"\n[收到使用者訊息]: 「{raw_msg}」 (User: {user_id})")

    HUMAN_GUIDE_TEXT = (
        "您好！我是材霈的人資招募專員 😊\n\n"
        "很高興為您服務！為了幫您精準媒合最合適的工作，想先了解一下：\n\n"
        "1. 您希望在【哪個地區】上班？（例如：桃園、新莊、台中、台南、高雄等）\n"
        "2. 有偏好的【工作類型】或【班別】嗎？（例如：理貨、作業員、早班、夜班）\n\n"
        "💡 您可以直接點擊下方快捷按鈕，或直接打字告訴我您的需求喔！"
    )

    active_jobs = fetch_jobs_data()
    active_faqs = fetch_faqs_data()

    # 1. 快速檢索 FAQ
    for faq in active_faqs:
        q_keywords = str(faq.get("問題與常見問法") or faq.get("問題") or "").replace("、", ",").replace("，", ",").replace("/", ",").split(",")
        answer = faq.get("標準回覆內容") or faq.get("回答") or ""
        for kw in q_keywords:
            kw_clean = kw.strip()
            if kw_clean and (kw_clean in raw_msg or raw_msg in kw_clean):
                reply_text = f"{answer}\n\n💡 材霈小提醒：本回覆由材霈智能AI助理自動提供。若有更細節的問題，歡迎上班時間由專員為您服務！"
                target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
                return

    # 2. 載入對話歷史紀錄
    history = get_user_history(user_id)
    history_text = "\n".join([f"{item['role']}: {item['text']}" for item in history])

    job_index_text = ""
    for idx, j in enumerate(active_jobs):
        t = j.get("_parsed_title", "")
        loc = f"{j.get('縣市', '')}{j.get('行政區', '')}"
        shift = j.get('班別', '')
        salary = j.get('薪資', '')
        job_index_text += f"[ID:{idx}] 名稱:{t} | 地點:{loc} | 班別:{shift} | 待遇:{salary}\n"

    # 3. Gemini 真人顧問提示詞
    ai_prompt = f"""你是一位「材霈有限公司」非常親切、專業、高情商的真人在線人資招募顧問（名字叫小霈）。
你的目標是：結合過去對話歷史，以真人專員的口吻引導求職者，理解其求職條件（地區、班別、工種），並在有符合職缺時推薦。我們在全台灣（北、中、南區）皆有職缺。

【目前公司招募中的職缺清單】：
{job_index_text if job_index_text else "（目前公司在全台灣北、中、南區均有開放各類優質職缺）"}

【過去對話歷史】：
{history_text if history_text else "（對話剛開始）"}

【求職者剛說的話】：
「{raw_msg}」

【決策指令】：
請分析求職者剛說的話以及上下文，選擇下列其中一種格式輸出：

格式 A（求職者剛打招呼、說想找工作，或條件還很模糊）：
ACTION:ASK
REPLY:{HUMAN_GUIDE_TEXT}
BUTTONS:📍 桃園工作,📍 新莊/新北,📍 台中工作,📍 南部工作,☀️ 固定早班,🌙 夜班/大夜,📦 理貨工作

格式 B（求職者提出的條件有符合的職缺，或指定地區/班別有缺）：
ACTION:RECOMMEND
IDS:（符合的職缺數字，例如 0 或 0,1）
REPLY:（給求職者的溫暖過場語，例如：為您找到以下符合您需求的優質職缺，歡迎點擊查看簡章或線上填寫履歷喔！）

格式 C（求職者提出的條件在清單中「完全沒有」符合）：
ACTION:NO_MATCH
REPLY:（以真人專員口吻說明目前該地區或條件暫無開放，並主動詢問是否考慮其他地區或班別，約 40-60 字）

請直接輸出："""

    ai_output = query_gemini_ai(ai_prompt)
    print(f"[Gemini 決策輸出]:\n{ai_output}\n")

    append_user_history(user_id, "求職者", raw_msg)

    # 4. 解析 AI 輸出
    if "ACTION:RECOMMEND" in ai_output:
        ids_match = re.search(r'IDS:\s*([0-9,\s]+)', ai_output)
        reply_match = re.search(r'REPLY:\s*(.+)', ai_output, re.DOTALL)
        reply_text = reply_match.group(1).strip() if reply_match else "太棒了！為您推薦以下最符合您需求的職缺："
        append_user_history(user_id, "招募顧問", reply_text)

        matched_jobs = []
        if ids_match:
            indices = [int(n.strip()) for n in ids_match.group(1).split(",") if n.strip().isdigit() and int(n.strip()) < len(active_jobs)]
            matched_jobs = [active_jobs[i] for i in indices]

        if not matched_jobs and active_jobs:
            matched_jobs = active_jobs[:3]

        flex_card = create_job_flex_card(matched_jobs, user_id)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), flex_card])
        return

    elif "ACTION:ASK" in ai_output:
        reply_match = re.search(r'REPLY:\s*(.+?)(?=\nBUTTONS:|$)', ai_output, re.DOTALL)
        buttons_match = re.search(r'BUTTONS:\s*(.+)', ai_output)

        reply_text = reply_match.group(1).strip() if reply_match else HUMAN_GUIDE_TEXT
        append_user_history(user_id, "招募顧問", reply_text)

        buttons = []
        if buttons_match:
            raw_buttons = [b.strip() for b in buttons_match.group(1).split(",") if b.strip()]
            for b_label in raw_buttons[:6]:
                clean_txt = re.sub(r'^[📍☀️🌙📦🏭\s]+', '', b_label)
                buttons.append(QuickReplyButton(action=MessageAction(label=b_label[:20], text=clean_txt)))
        
        if not buttons:
            buttons = [
                QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
                QuickReplyButton(action=MessageAction(label="📍 新莊工作", text="新莊工作")),
                QuickReplyButton(action=MessageAction(label="📍 台中工作", text="台中工作")),
                QuickReplyButton(action=MessageAction(label="📍 台南/高雄", text="台南工作")),
                QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
                QuickReplyButton(action=MessageAction(label="🌙 晚班/夜班", text="夜班工作"))
            ]

        quick_reply = QuickReply(items=buttons)
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
        return

    elif "ACTION:NO_MATCH" in ai_output:
        reply_match = re.search(r'REPLY:\s*(.+)', ai_output, re.DOTALL)
        reply_text = reply_match.group(1).strip() if reply_match else f"您好！目前在「{raw_msg}」暫時沒有開放中的職缺，已為您記錄需求！"
        append_user_history(user_id, "招募顧問", reply_text)
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📍 看看北部職缺", text="桃園工作")),
            QuickReplyButton(action=MessageAction(label="📍 看看中部職缺", text="台中工作")),
            QuickReplyButton(action=MessageAction(label="📍 看看南部職缺", text="台南工作")),
            QuickReplyButton(action=MessageAction(label="🌐 瀏覽全部工作", text="找工作"))
        ])
        target_line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
        return

    # 5. 智慧本地容錯（全台職缺直接動態比對）
    print("[執行智慧本地容錯比對]")
    matched_jobs = []
    clean_msg = re.sub(r'[？\?！!。，,\s]+', '', raw_msg).replace("台", "臺").lower()
    
    for j in active_jobs:
        row_text = str(j.get("_raw_row_text", "")).replace("台", "臺").lower()
        if clean_msg and (clean_msg in row_text or any(token in row_text for token in ["夜班", "早班", "理貨", "作業員", "司機", "包裝", "中班"] if token in clean_msg)):
            matched_jobs.append(j)

    if matched_jobs:
        reply_text = "為您找到以下符合條件的推薦職缺，歡迎點擊下方查看簡章或線上應徵喔："
        append_user_history(user_id, "招募顧問", reply_text)
        target_line_bot_api.reply_message(reply_token, [TextSendMessage(text=reply_text), create_job_flex_card(matched_jobs[:3], user_id)])
        return

    append_user_history(user_id, "招募顧問", HUMAN_GUIDE_TEXT)
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📍 桃園工作", text="桃園工作")),
        QuickReplyButton(action=MessageAction(label="📍 新莊/新北", text="新莊工作")),
        QuickReplyButton(action=MessageAction(label="📍 台中工作", text="台中工作")),
        QuickReplyButton(action=MessageAction(label="📍 台南/高雄", text="台南工作")),
        QuickReplyButton(action=MessageAction(label="☀️ 固定早班", text="早班工作")),
        QuickReplyButton(action=MessageAction(label="🌙 晚班/夜班", text="夜班工作"))
    ])
    target_line_bot_api.reply_message(reply_token, TextSendMessage(text=HUMAN_GUIDE_TEXT, quick_reply=quick_reply))

# ==========================================
# 7. Webhook 路由端點 (加入防呆簽章檢查)
# ==========================================
@app.get("/")
def health_check():
    return {"status": "ok", "service": "Tsaipei AI Recruitment Consultant is running."}

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