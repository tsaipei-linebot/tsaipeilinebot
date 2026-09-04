import os
from datetime import time
import pytz
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
# 8. 白天／晚間回覆時段（日夜接力，見 HANDOFF.md）
# 同仁上班時間為每天 10:00–19:00（含週末，班表相同）。這裡刻意抓 10 分鐘
# 交接緩衝：機器人比同仁實際下班時間（19:00）提早 10 分鐘啟動、比同仁實際
# 上班時間（10:00）延後 10 分鐘才停止——目的是寧可偶爾跟同仁重複回覆，
# 也不要讓求職者在交接空檔完全沒有任何一邊回覆。
#
# STAFFED_HOURS_START～STAFFED_HOURS_END 這段時間內，沛沛完全不主動回覆，
# 交給真人專員在 LINE 聊天模式手動處理（見 handlers/message_handler.py
# 的 _is_staffed_hours()）。
#
# STAFFED_HOURS_GUARD_ENABLED：這個機制的總開關，預設關閉（不管幾點都照舊
# 回覆，等同這個功能還沒上線）。還在測試頻道、LINE 官方帳號後台的「回應時間
# 設定」排程還沒設好之前，開著這個守門邏輯會讓白天測試時機器人看起來像故障
# （完全不回覆），所以刻意讓程式碼合併進 main 後不會立刻生效。等正式要切換
# 到「白天真人、晚上沛沛」的運作模式時，才去 Cloud Run 設定環境變數
# STAFFED_HOURS_GUARD_ENABLED=true 打開，不需要再改程式碼、重新部署一次即可。
# ==========================================
STAFFED_HOURS_GUARD_ENABLED = os.getenv("STAFFED_HOURS_GUARD_ENABLED", "false").strip().lower() in ("1", "true", "yes")
STAFFED_HOURS_START = time(10, 10)
STAFFED_HOURS_END = time(18, 50)
TAIPEI_TZ = pytz.timezone("Asia/Taipei")
