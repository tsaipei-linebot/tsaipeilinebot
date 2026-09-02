import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. LINE 官方帳號設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# 測試環境 LINE 設定
TEST_LINE_CHANNEL_ACCESS_TOKEN = os.getenv("TEST_LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN)
TEST_LINE_CHANNEL_SECRET = os.getenv("TEST_LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET)

# ==========================================
# 2. 金鑰與 Notion 資料庫 ID
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_JOBS_DB_ID = os.getenv("NOTION_JOBS_DB_ID")
NOTION_FAQ_DB_ID = os.getenv("NOTION_FAQ_DB_ID")
OFFICIAL_WEBSITE_BASE = os.getenv("OFFICIAL_WEBSITE_BASE", "https://tsaipei.netlify.app")

# ==========================================
# 3. 快取與 Session 設定
# ==========================================
SESSION_TTL = 7 * 24 * 3600  # 7 天對話記憶 (秒)
CACHE_TTL = 30               # Notion 快取 30 秒

# ==========================================
# 4. Notion 讀取白名單 (完整納入 休假方式、系統廠商名稱 與 職缺名稱)
# ==========================================
ALLOWED_PROPERTIES = {
    "職缺名稱", "職缺名稱(對外)", "職務類別", "縣市", "行政區", "行業別",
    "全/兼職", "班別", "薪資", "休假方式", "領薪方式", "工作內容(對外)", "狀態",
    "精華亮點", "排版工作說明", "系統廠商名稱"
}

# ==========================================
# 5. 精準履歷路由網址（可用環境變數覆蓋，未設定時沿用原本的預設值）
# ==========================================
DEFAULT_RESUME_URLS = {
    "Spx": os.getenv(
        "RESUME_URL_SPX",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU3B4IiwiU3lzdGVtIjoiWWVzIn0=?openExternalBrowser=1"
    ),
    "Service": os.getenv(
        "RESUME_URL_SERVICE",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU2VydmljZSIsIlN5c3RlbSI6IlllcyJ9?openExternalBrowser=1"
    ),
    "Manufacture": os.getenv(
        "RESUME_URL_MANUFACTURE",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiTWFudWZhY3R1cmUiLCJTeXN0ZW0iOiJZZXMifQ==?openExternalBrowser=1"
    )
}

# ==========================================
# 6. Google Cloud / Vertex AI 設定
# ==========================================
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tsaipei-505807")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")

# ==========================================
# 7. 每週新工廠登記監控設定
# 資料源：政府資料開放平台《登記工廠名錄》(經濟部產業發展署，dataset id 6569)
# ==========================================
FACTORY_OPENDATA_DATASET_ID = os.getenv("FACTORY_OPENDATA_DATASET_ID", "6569")
FACTORY_WATCH_LOOKBACK_DAYS = int(os.getenv("FACTORY_WATCH_LOOKBACK_DAYS", "10"))
FACTORY_WATCH_SHEET_ID = os.getenv("FACTORY_WATCH_SHEET_ID", "")
FACTORY_WATCH_SHEET_NAME = os.getenv("FACTORY_WATCH_SHEET_NAME", "新登記工廠")
# 目前尚未決定要推播給哪個 LINE 帳號/群組，先留空；設定後即可自動開始推播
FACTORY_WATCH_LINE_TARGET_ID = os.getenv("FACTORY_WATCH_LINE_TARGET_ID", "")
# Cloud Scheduler 呼叫 /internal/factory-watch/run 時要帶的共用密鑰，避免端點被任意觸發
FACTORY_WATCH_TRIGGER_SECRET = os.getenv("FACTORY_WATCH_TRIGGER_SECRET", "")
