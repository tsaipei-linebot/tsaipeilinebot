import os

# ==========================================
# 管理部系統設定
# 跟配送部系統一樣是獨立掛載的子系統，共用同一個 GCP 專案（Firestore /
# Cloud Storage）跟同一批帳號（見根目錄 platform_accounts.py），但資料表
# 前綴改成 management_，避免互相汙染。
# ==========================================

# 網頁登入 session 的簽章密鑰。刻意跟配送部系統共用同一個環境變數/同一組
# secret key，而且 SessionMiddleware 的 cookie 名稱也要跟配送部一致
# （見 management/app.py），這樣同仁登入一次，兩個部門的 session 才會共用
# 同一顆瀏覽器 cookie，不用分別登入。
SESSION_SECRET_KEY = os.getenv("DELIVERY_SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")

# 公告/會議記錄/文件庫上傳的附件檔案，沿用跟配送部系統同一個 GCS bucket
# （同一份環境變數），只是 blob 路徑前綴改成 management/，不用另外申請一個
# bucket。
GCS_BUCKET_NAME = os.getenv("DELIVERY_GCS_BUCKET", "")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，規章/SOP 文件通常比配送部的證件影本大一些
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# 文件庫分類（規章制度/SOP/教育訓練……），純粹是畫面上篩選跟顯示用的自由
# 分類，不影響任何存取權限邏輯。
DOCUMENT_CATEGORIES = [
    {"code": "regulation", "name": "規章制度"},
    {"code": "sop", "name": "SOP流程"},
    {"code": "training", "name": "教育訓練"},
    {"code": "template", "name": "範本文件"},
    {"code": "other", "name": "其他"},
]
DOCUMENT_CATEGORY_MAP = {c["code"]: c["name"] for c in DOCUMENT_CATEGORIES}

# 資產/設備分類：公務車（跟配送部的車輛管理是兩回事，那個是配送人員在騎的
# 營業用車，這裡是公司內部行政用途的財產）、公務手機、門號、電腦。
ASSET_CATEGORIES = [
    {"code": "vehicle", "name": "公務車"},
    {"code": "phone", "name": "公務手機"},
    {"code": "sim", "name": "門號"},
    {"code": "computer", "name": "電腦"},
]
ASSET_CATEGORY_MAP = {c["code"]: c["name"] for c in ASSET_CATEGORIES}

ASSET_STATUSES = [
    {"code": "in_use", "name": "使用中"},
    {"code": "idle", "name": "閒置"},
    {"code": "maintenance", "name": "維修中"},
    {"code": "retired", "name": "報廢"},
]
ASSET_STATUS_MAP = {s["code"]: s["name"] for s in ASSET_STATUSES}
DEFAULT_ASSET_STATUS = "in_use"

# ==========================================
# 管理部專屬 LINE 官方帳號
# 跟招募機器人（沛沛）、配送部完全獨立的另一個 LINE Messaging API Channel，
# 刻意只用來做「推播通知」＋ Webhook 回覆聊天室 ID（見 line_bot.py／
# routes/line_webhook_routes.py），不接任何自動對話/AI 邏輯，避免跟沛沛的
# 求職者對話混在一起。
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("MANAGEMENT_LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("MANAGEMENT_LINE_CHANNEL_SECRET", "")
# 要推播通知的目標群組 ID：把這個新機器人拉進管理部群組後，機器人會直接在
# 群組裡回覆這個群組的 Group ID，複製貼到這個環境變數即可，不用像配送部
# 那樣去 Cloud Logging 撈。
LINE_NOTIFY_GROUP_ID = os.getenv("MANAGEMENT_LINE_GROUP_ID", "")

# ==========================================
# 門號繳費提醒（見 routes/reminder_routes.py）
# Cloud Scheduler 每週一上午 9 點呼叫一次，比照配送部文件到期提醒的密鑰
# 驗證作法（X-Management-Asset-Reminder-Secret header）。未設定時該端點
# 一律回傳 403，等同不存在。固定每週觸發一次，天然不會重複提醒同一顆
# 門號，不需要像配送部那樣另外記錄「提醒過了沒有」。
# ==========================================
ASSET_REMINDER_SECRET = os.getenv("MANAGEMENT_ASSET_REMINDER_SECRET", "")
SIM_PAYMENT_REMINDER_DAYS_AHEAD = 7
