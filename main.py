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

TEST_LINE_CHANNEL_ACCESS_TOKEN = os.getenv(
    "TEST_LINE_CHANNEL_ACCESS_TOKEN", 
    LINE_CHANNEL_ACCESS_TOKEN
)
TEST_LINE_CHANNEL_SECRET = os.getenv(
    "TEST_LINE_CHANNEL_SECRET", 
    LINE_CHANNEL_SECRET
)
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