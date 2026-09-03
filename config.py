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
# 7. 壓力測試專用（僅供內部壓力測試腳本使用，預設關閉）
# main.py 的 /internal/load-test-message 端點需要這組密鑰才會受理請求；
# 沒有設定（空字串）時該端點一律回傳 403，等同完全關閉。
# ==========================================
LOAD_TEST_SECRET = os.getenv("LOAD_TEST_SECRET", "")

# ==========================================
# 8. 配送部車輛回報（LINE 群組專用）
# 只有這個群組 ID 傳來的文字訊息才會被 handlers/message_handler.py 攔截、
# 解析成車輛領車/還車回報寫進配送部系統（見 delivery/vehicle_report.py）；
# 其他來源（含私訊、其他群組）一律當作一般訊息，走原本的招募對話邏輯，不受影響。
# 沒有設定（空字串）時這個攔截機制完全關閉。
# ==========================================
DELIVERY_VEHICLE_REPORT_GROUP_ID = os.getenv("DELIVERY_VEHICLE_REPORT_GROUP_ID", "")
