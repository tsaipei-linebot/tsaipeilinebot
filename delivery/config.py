import os

# ==========================================
# 配送部系統設定
# 獨立於 LINE 招募機器人的子系統，共用同一個 GCP 專案（Firestore / Cloud Run），
# 但資料表（collection）與 GCS 檔案路徑皆加上 delivery_ / delivery/ 前綴，避免互相汙染。
# ==========================================

# 網頁登入 session 的簽章密鑰（cookie 簽章用，不是密碼雜湊用的鹽）。
# 正式環境務必透過環境變數設定成隨機字串，否則預設值僅供本機開發使用。
SESSION_SECRET_KEY = os.getenv("DELIVERY_SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")

# 上傳檔案（身分證、駕照、強制險、良民證、病假收據）存放的 GCS bucket 名稱。
# 未設定時，檔案上傳功能會回傳明確錯誤，不會嘗試寫入任何地方。
GCS_BUCKET_NAME = os.getenv("DELIVERY_GCS_BUCKET", "")

# Google 表單送出時，Apps Script 呼叫 /delivery/api/form-submission 這支 webhook
# 要帶的共用密鑰（見 X-Delivery-Form-Secret header）。未設定時該端點一律回傳 403，
# 等同這個 webhook 不存在（跟 main.py 的 LOAD_TEST_SECRET 是一樣的作法）。
FORM_WEBHOOK_SECRET = os.getenv("DELIVERY_FORM_WEBHOOK_SECRET", "")

# 廠商清單（選擇廠商 / 人員所屬廠商）
VENDORS = [
    {"code": "shopee", "name": "蝦皮"},
    {"code": "ud", "name": "UD"},
    {"code": "uc", "name": "UC"},
    {"code": "sf", "name": "順豐"},
]
VENDOR_MAP = {v["code"]: v["name"] for v in VENDORS}

# 批次匯入 CSV 時，「廠商」欄位允許填代號或中文名稱，一律轉成小寫比對。
VENDOR_LOOKUP = {}
for _v in VENDORS:
    VENDOR_LOOKUP[_v["code"].lower()] = _v["code"]
    VENDOR_LOOKUP[_v["name"].lower()] = _v["code"]

# 報到前應備文件（人員缺件狀況即依此清單逐項檢查）
DOC_TYPES = [
    {"code": "id_card", "name": "身分證", "has_expiry": False},
    {"code": "driver_license", "name": "駕照", "has_expiry": False},
    {"code": "insurance", "name": "強制險", "has_expiry": True},
    {"code": "police_clearance", "name": "良民證", "has_expiry": True},
]
DOC_TYPE_MAP = {d["code"]: d for d in DOC_TYPES}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB，單一檔案上傳上限
ALLOWED_UPLOAD_CONTENT_TYPES = {"image/jpeg", "image/png", "image/heic", "application/pdf"}
