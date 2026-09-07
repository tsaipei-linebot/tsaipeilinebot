import os

# ==========================================
# 人資專區設定
# 跟配送部/管理部系統一樣是獨立掛載的子系統，共用同一個 GCP 專案
# （Firestore / Cloud Storage）跟同一批帳號（見根目錄 platform_accounts.py），
# 資料表前綴改成 hr_，避免互相汙染。
# ==========================================

# 網頁登入 session 的簽章密鑰／cookie 名稱都跟配送部/管理部共用（見
# hr/app.py），同仁登入一次就能在有權限的部門之間切換，不用重複登入。
SESSION_SECRET_KEY = os.getenv("DELIVERY_SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")

# 檔案上傳沿用跟配送部/管理部同一個 GCS bucket（同一份環境變數），blob 路徑
# 前綴改成 hr/，不用另外申請一個 bucket。
GCS_BUCKET_NAME = os.getenv("DELIVERY_GCS_BUCKET", "")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，跟管理部文件庫一致
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

# ==========================================
# 意外通報（新群組）
# 沿用管理部現有的 LINE 官方帳號（見 management/line_bot.py），webhook 本來
# 就是直接打進 tsaipeilinebot（不像配送部那組要先經過 delivery-gas-project
# 轉發），所以在 management/routes/line_webhook_routes.py 裡多判斷「訊息
# 來自這個群組時，改用意外通報格式解析」即可，不需要另外申請 LINE 帳號、
# 也不需要動 delivery-gas-project。
#
# 表單 11 個欄位跟配送部「意外事件回報」相同，差異是「廠商名稱」這裡改成
# 自由文字（不限定蝦皮/UD/UC/順豐），見 hr/incident_report.py。
# 只回覆同一個群組的登記確認訊息，不轉發到第二個群組（比配送部那套單純）。
# ==========================================
IDENTITY_TYPES = ["雇傭", "承攬"]
DUTY_STATUSES = ["執行勤務中", "上下班途中"]
YES_NO_VALUES = ["有", "無"]

RISK_LEVELS = ["低", "中", "高"]

INCIDENT_STATUSES = [
    {"code": "open", "name": "未結案"},
    {"code": "closed", "name": "已結案"},
]
INCIDENT_STATUS_MAP = {s["code"]: s["name"] for s in INCIDENT_STATUSES}
DEFAULT_INCIDENT_STATUS = "open"

# 意外通報回報要用的 LINE 群組 ID（拿法：把管理部那組官方帳號拉進這個新
# 群組，群組裡打「群組ID」，機器人會回覆 Group ID）。
HR_INCIDENT_GROUP_ID = os.getenv("HR_INCIDENT_GROUP_ID", "")

# 未結案意外通報的每週提醒：Cloud Scheduler 呼叫，用共用密鑰驗證，直接用
# management 那組 LINE 帳號推播回 HR_INCIDENT_GROUP_ID（不需要像配送部那樣
# 另外在 GAS 那邊設時間驅動觸發器，因為 Python 這邊本來就有這組帳號的
# Token，可以直接推播）。
HR_INCIDENT_REMINDER_SECRET = os.getenv("HR_INCIDENT_REMINDER_SECRET", "")

# ==========================================
# 公司證照到期提醒
# 推播對象沿用管理部「門號繳費提醒」現有的群組設定
# （management.config.LINE_NOTIFY_GROUP_ID），不用另外設定推播對象。
# ==========================================
HR_LICENSE_REMINDER_SECRET = os.getenv("HR_LICENSE_REMINDER_SECRET", "")
LICENSE_REMINDER_DAYS_AHEAD = 30
LICENSE_REMINDER_RESEND_INTERVAL_DAYS = 7
