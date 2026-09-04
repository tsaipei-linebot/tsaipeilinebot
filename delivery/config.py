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

# 車輛領車/還車回報：另一個獨立 LINE 官方帳號（跟這支招募機器人是不同的
# LINE Channel）的 Google Apps Script 專案（delivery-gas-project）收到群組
# 訊息後，會呼叫 /delivery/api/vehicle-report 這支 webhook 轉發訊息內容，
# 要帶的共用密鑰（見 X-Delivery-Vehicle-Secret header）。未設定時該端點
# 一律回傳 403，等同這個 webhook 不存在。
VEHICLE_REPORT_WEBHOOK_SECRET = os.getenv("DELIVERY_VEHICLE_REPORT_SECRET", "")

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

# 合作方式：決定這個人除了基本項目之外還要備哪些保險/證明文件。
COOPERATION_TYPES = [
    {"code": "two_wheel_contract", "name": "二輪承攬"},
    {"code": "two_wheel_employed", "name": "二輪雇傭"},
    {"code": "three_wheel_employed", "name": "三輪雇傭"},
]
COOPERATION_TYPE_MAP = {c["code"]: c["name"] for c in COOPERATION_TYPES}

# 負責客戶：目前只有 UD 的人員會用到（決定要不要多備 MOMO 測驗），但欄位本身
# 不綁死在特定廠商上，之後其他廠商如果也分客戶，不用改架構。
CLIENTS = [
    {"code": "pchome", "name": "PCHOME"},
    {"code": "momo", "name": "MOMO"},
]
CLIENT_MAP = {c["code"]: c["name"] for c in CLIENTS}

# 哪些廠商的人員詳細頁要顯示「合作方式」「負責客戶」這兩個選單。這兩個欄位
# 本身是全域欄位（值不因廠商而異），但畫面上只有真的會用到的廠商才顯示，
# 避免同仁在用不到的廠商頁面上看到無意義的選單。
COOPERATION_TYPE_VENDORS = ["shopee"]
CLIENT_VENDORS = ["ud"]

# 報到前應備文件（人員缺件狀況即依此清單逐項檢查）。每一項的 kind 決定要怎麼
# 判斷「缺不缺」、頁面上要顯示什麼樣的輸入元件：
#   - "id_number"：不是文件，是檢查 personnel.id_number 這個欄位本身格式合不合法
#     （身分證字號檢查碼），同仁直接填字號、不用上傳檔案。
#   - "email"：不是文件，同仁直接填 email，簡單檢查格式。
#   - "checkbox"：同仁勾選「有」就算備齊，不用上傳檔案、沒有到期日。
#   - "file"：要上傳檔案，但不用記錄到期日（例如自拍照，純粹「有沒有交」）。
#   - "file_expiry"：要上傳檔案，並且（透過 OCR 或人工）記錄到期日，過期也算缺件。
#     多一個 required（預設 True）：False 代表這項不是必填，沒交不算缺件，但只要
#     有交、有到期日，一樣會被到期提醒掃到。
# 篩選條件（都不設代表不限）：
#   - exclude_vendors：這幾個廠商的人員不會被要求這一項。
#   - include_vendors：只有這幾個廠商的人員才會被要求這一項（白名單，跟
#     exclude_vendors 是相反方向，依項目本身比較像哪一種寫法決定用哪個）。
#   - cooperation_types：只有合作方式在清單裡的人才會被要求。
#   - clients：只有負責客戶在清單裡的人才會被要求。
DOC_TYPES = [
    {"code": "id_card", "name": "身分證", "kind": "id_number"},
    {"code": "driver_license", "name": "駕照", "kind": "checkbox"},
    {"code": "contract", "name": "合約簽定", "kind": "checkbox"},
    {"code": "police_clearance", "name": "良民證", "kind": "file_expiry", "exclude_vendors": ["shopee"]},
    {
        "code": "insurance",
        "name": "強制險",
        "kind": "file_expiry",
        "cooperation_types": ["two_wheel_contract", "two_wheel_employed"],
    },
    {
        "code": "guild_insurance",
        "name": "公會加保證明",
        "kind": "file_expiry",
        "cooperation_types": ["two_wheel_contract"],
        "required": False,
    },
    {
        "code": "liability_insurance",
        "name": "營業用第三責任險",
        "kind": "file_expiry",
        "cooperation_types": ["two_wheel_employed"],
    },
    # UD/UC 專屬（不用合作方式判斷，直接綁廠商）
    {"code": "uber_system", "name": "UBER系統", "kind": "checkbox", "include_vendors": ["ud", "uc"]},
    {
        "code": "momo_test",
        "name": "MOMO測驗",
        "kind": "checkbox",
        "include_vendors": ["ud"],
        "clients": ["momo"],
    },
    {"code": "selfie_photo", "name": "自拍照", "kind": "file", "include_vendors": ["ud"]},
    {"code": "uc_photo", "name": "拍照", "kind": "file", "include_vendors": ["uc"]},
    {"code": "email", "name": "EMAIL", "kind": "email", "include_vendors": ["ud", "uc"]},
    # 順豐專屬：強制險/公會加保證明不看合作方式，直接綁廠商、無條件要求
    # （公會加保證明比照蝦皮設為非必填，但一樣有到期提醒）。
    {"code": "sf_insurance", "name": "強制險", "kind": "file_expiry", "include_vendors": ["sf"]},
    {
        "code": "sf_guild_insurance",
        "name": "公會加保證明",
        "kind": "file_expiry",
        "include_vendors": ["sf"],
        "required": False,
    },
]
DOC_TYPE_MAP = {d["code"]: d for d in DOC_TYPES}

# 人員狀態（報到/在職狀態）。跟 create_personnel 內部寫死的 status="active" 是
# 兩回事——那個是判斷資料還存不存在的隱藏欄位，一律是 "active"、不開放編輯；
# 這裡才是同仁自己會維護、畫面上看得到、可以篩選的報到狀態。
PERSONNEL_STATUSES = [
    {"code": "pending_onboard", "name": "待報到"},
    {"code": "employed", "name": "在職"},
    {"code": "resigned", "name": "離職"},
    {"code": "onboard_withdrawn", "name": "放棄報到"},
]
PERSONNEL_STATUS_MAP = {s["code"]: s["name"] for s in PERSONNEL_STATUSES}
PERSONNEL_STATUS_BADGE_CLASS = {
    "pending_onboard": "badge-pending",
    "employed": "badge-employed",
    "resigned": "badge-resigned",
    "onboard_withdrawn": "badge-withdrawn",
}
# 新建人員（手動新增表單、CSV 批次匯入、應徵名單錄取）一律先預設這個，
# 之後同仁自己到人員詳細頁改成「在職」等其他狀態。
DEFAULT_PERSONNEL_STATUS = "pending_onboard"
# 這個功能上線前就已經存在的人員資料沒有 employment_status 欄位，讀取時當作
# 「在職」——這些人本來就已經在系統裡，不該被當成剛建立、還沒報到。
LEGACY_PERSONNEL_STATUS = "employed"
# 「離職」「放棄報到」預設不顯示在廠商人員清單，跟應徵名單「放棄」預設隱藏是
# 一樣的邏輯：同仁主動搜尋姓名、或直接篩選狀態為這兩項才會列出來。
HIDDEN_PERSONNEL_STATUSES = {"resigned", "onboard_withdrawn"}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB，單一檔案上傳上限
ALLOWED_UPLOAD_CONTENT_TYPES = {"image/jpeg", "image/png", "image/heic", "application/pdf"}

# ==========================================
# 文件到期提醒（強制險/公會加保證明/營業用第三責任險/良民證）
# 用公司現有的 LINE 官方帳號主動推播，Cloud Scheduler 每天呼叫
# /delivery/api/expiry-reminder-check 觸發檢查（見 routes/reminder_routes.py）。
# ==========================================
REMINDER_TRIGGER_SECRET = os.getenv("DELIVERY_REMINDER_SECRET", "")
LINE_REMINDER_TARGET_ID = os.getenv("DELIVERY_LINE_REMINDER_TARGET", "")
REMINDER_DAYS_AHEAD = int(os.getenv("DELIVERY_REMINDER_DAYS_AHEAD", "30"))
REMINDER_RESEND_INTERVAL_DAYS = 7  # 同一份文件最多幾天才重新提醒一次，避免每天洗版

# 應徵名單處理狀態。「已錄取」不開放在應徵名單頁面手動勾選，只能透過
# 「錄取並建立人員」那個流程設定（因為需要同時指派廠商、建立正式人員資料）。
APPLICANT_STATUSES = [
    {"code": "not_interviewed", "name": "未面試"},
    {"code": "interviewed", "name": "已面試"},
    {"code": "withdrawn", "name": "放棄"},
    {"code": "hired", "name": "已錄取"},
]
APPLICANT_STATUS_MAP = {s["code"]: s["name"] for s in APPLICANT_STATUSES}
SELECTABLE_APPLICANT_STATUSES = [s for s in APPLICANT_STATUSES if s["code"] != "hired"]

# 假別登記的假別種類。
LEAVE_TYPES = [
    {"code": "sick", "name": "病假"},
    {"code": "personal", "name": "事假"},
    {"code": "annual", "name": "特休"},
    {"code": "other", "name": "其他"},
]
LEAVE_TYPE_MAP = {t["code"]: t["name"] for t in LEAVE_TYPES}

# 應徵名單的廠商/合作方式：跟人員的 vendor/cooperation_type 是同一套代碼，
# 沿用 VENDOR_MAP / COOPERATION_TYPE_MAP。應徵階段沒表單欄位可以填廠商，
# 是由送出 webhook 的 Apps Script 各自帶固定的廠商代碼過來（見
# routes/webhook_routes.py），畫面上保留讓同仁手動修改的權限。
# 合作方式選單只在蝦皮的應徵者顯示，沿用 COOPERATION_TYPE_VENDORS
# （目前就是 ["shopee"]，跟人員詳細頁那個是同一份設定）。

# 試駕狀態：未試駕（預設）／通過／未通過。
TEST_DRIVE_STATUSES = [
    {"code": "not_tested", "name": "未試駕"},
    {"code": "passed", "name": "通過"},
    {"code": "failed", "name": "未通過"},
]
TEST_DRIVE_STATUS_MAP = {s["code"]: s["name"] for s in TEST_DRIVE_STATUSES}
DEFAULT_TEST_DRIVE_STATUS = "not_tested"

# 哪些應徵者需要試駕：UD、UC 一律需要；蝦皮只有合作方式是「三輪雇傭」才需要
# （二輪承攬/二輪雇傭不用）；順豐不需要。判斷邏輯見
# repository.applicant_needs_test_drive()，這裡只放組成判斷用的資料。
TEST_DRIVE_REQUIRED_VENDORS = ["ud", "uc"]
TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES = ["three_wheel_employed"]

# ==========================================
# 車輛管理
# 車號全公司唯一（車輛主檔用車號當文件 ID）；廠商是車輛本身固定的屬性，跟
# LINE 群組回報／網頁登記事件時填的廠商要一致，見 repository.vehicle_event_error()。
# ==========================================
VEHICLE_STATUSES = [
    {"code": "available", "name": "待領用"},
    {"code": "in_use", "name": "使用中"},
    {"code": "maintenance", "name": "待維修"},
]
VEHICLE_STATUS_MAP = {s["code"]: s["name"] for s in VEHICLE_STATUSES}
VEHICLE_STATUS_BADGE_CLASS = {
    "available": "badge-ok",
    "in_use": "badge-pending",
    "maintenance": "badge-missing",
}
DEFAULT_VEHICLE_STATUS = "available"
