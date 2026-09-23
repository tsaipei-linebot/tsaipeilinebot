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

# 意外事件回報：跟車輛回報同一個 LINE 群組、同一個 GAS 專案轉發過來，共用
# 一樣的「另一個獨立 LINE 官方帳號」架構，但走獨立的 webhook 端點/密鑰，
# 避免車輛回報跟意外事件回報這兩個不相干的功能共用同一支端點。
INCIDENT_REPORT_WEBHOOK_SECRET = os.getenv("DELIVERY_INCIDENT_REPORT_SECRET", "")

# 2026-09-15 新增：同仁如果不是在 LINE 群組回報，而是直接在配送部系統網站
# 填寫車輛領還車／新增意外事件，也要讓「配送組作業群組」即時收到通知，
# 跟 LINE 群組回報的體驗一致。這裡是反過來的方向——換成配送部系統
# （Cloud Run）主動呼叫 delivery-gas-project 的 doGet(?type=DELIVERY_NOTIFY)
# 橋接，請它用自己手上的 CHANNEL1 Token 推播到群組；Python 這邊完全不需要、
# 也不會拿到那個 Token，只負責把訊息內容送過去（見 delivery/group_notify.py）。
DELIVERY_NOTIFY_WEBHOOK_URL = os.getenv("DELIVERY_NOTIFY_WEBHOOK_URL", "")
DELIVERY_NOTIFY_WEBHOOK_SECRET = os.getenv("DELIVERY_NOTIFY_WEBHOOK_SECRET", "")

# 廠商清單（選擇廠商 / 人員所屬廠商）。
# 2026-09-13：原本單一的「蝦皮」拆成 4 個更細的廠商，代碼 "shopee" 保留
# 給改名後的「蝦皮三輪」（沿用同一個代碼，既有人員/車輛/應徵者資料不用
# 改任何欄位值，畫面上顯示的名稱自動變成新名稱），另外三個是全新代碼。
# 拆分後這三個新代碼底下的人員，應備文件/保險規則直接綁代碼本身
# （見下面 DOC_TYPES 的 shopee_contract_*／shopee_employed_own_car_*
# 那幾項），不再像「蝦皮三輪」那樣要另外選「合作方式」才能決定——這三個
# 新代碼當初刻意沒有被加進合作方式適用的廠商清單（那份清單 2026-09-18
# 起已經改成動態清單，見下面合作方式那節的說明；這裡只是保留原始決策的
# 脈絡），純粹是為了不去動既有「蝦皮」人員資料尚未被同仁手動改分類前的
# 既有行為，見 HANDOFF.md 的說明。
#
# 已知限制（不是這裡的程式碼能處理的）：應徵名單目前是由外部 Google
# 表單自己的 Apps Script 觸發器寫死帶 vendor="shopee" 過來（見
# routes/webhook_routes.py 開頭說明），這支腳本不在這個 repo 裡，如果
# 想讓新進的應徵者一開始就分類到新的三個代碼，需要同仁自己去改那支
# 外部 Apps Script，材霈平台這邊改不到。
#
# 2026-09-14：新增「蝦皮三輪速配倉」（代碼 shopee_speed_warehouse），使用者
# 確認這批人員的應備文件/保險規則要跟「蝦皮三輪」（shopee）完全一樣——
# 不是像上面三個新代碼那樣直接綁代碼本身，而是比照 "shopee" 也放進合作
# 方式適用的廠商清單、以及 DOC_TYPES 裡 police_clearance 的
# exclude_vendors，靠「合作方式」欄位決定保險規則。純粹是為了讓這批人員
# 在系統裡（人員清單、車輛、意外事件）用獨立的廠商代碼分開追蹤，不是要
# 另外訂一套不一樣的文件規則。
VENDORS = [
    {"code": "shopee", "name": "蝦皮三輪"},
    {"code": "shopee_company_car", "name": "蝦皮二輪公司車"},
    {"code": "shopee_employed_own_car", "name": "蝦皮二輪雇傭自備車"},
    {"code": "shopee_contract", "name": "蝦皮承攬"},
    {"code": "shopee_speed_warehouse", "name": "蝦皮三輪速配倉"},
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

# 合作方式的「屬於承攬還是雇傭」分類（2026-09-21 新增）：外送員接單媒合
# （見 delivery/rider_repository.py）的即時接單／報班媒合這兩個功能，資格
# 是照騎士在人員名冊裡目前的合作方式屬於承攬還是雇傭來判斷（承攬只能用
# 即時接單、雇傭只能用報班媒合），而不是像 EQUIPMENT_ELIGIBLE_PERSONNEL_
# STATUS 那樣寫死比對特定的合作方式文件 ID 字串——主管在「合作方式管理」
# 頁面新增的每一個選項，不管取什麼中文名稱，都要明確勾選屬於這兩類的哪一
# 類，資格判斷邏輯才不用跟著每次新增的選項一直修改。
COOPERATION_CATEGORY_CONTRACT = "contract"
COOPERATION_CATEGORY_EMPLOYED = "employed"
COOPERATION_CATEGORIES = [
    {"code": COOPERATION_CATEGORY_CONTRACT, "name": "承攬"},
    {"code": COOPERATION_CATEGORY_EMPLOYED, "name": "雇傭"},
]
COOPERATION_CATEGORY_MAP = {c["code"]: c["name"] for c in COOPERATION_CATEGORIES}

# 外送員接單媒合的「工號搬移」只認這幾個蝦皮系列的廠商代碼（2026-09-21
# 新增，見 scripts 目錄外、webhook_routes.py 的 /api/personnel-employee-no-
# sync）——使用者確認目前只有蝦皮相關的人員名冊資料才有工號可以比對。
SHOPEE_VENDOR_CODES = [
    "shopee",
    "shopee_company_car",
    "shopee_employed_own_car",
    "shopee_contract",
    "shopee_speed_warehouse",
]

# 合作方式：決定這個人除了基本項目之外還要備哪些保險/證明文件。2026-09-18
# 起改成主管可自行在「合作方式管理」頁面維護的動態清單（存 Firestore，見
# repository.py「合作方式管理」那節），不再是這裡的固定清單——使用者要求
# 除了蝦皮三輪/速配倉，UD/UC/順豐等其他廠商也要能設定自己的合作方式選項，
# 而且同一個選項可以勾選套用到多個廠商（例如蝦皮三輪跟蝦皮三輪速配倉繼續
# 共用同一份，其他廠商各自獨立）。
#
# **既有的 3 個選項（二輪承攬/二輪雇傭/三輪雇傭）刻意保留跟以前完全相同的
# 文件 ID**（"two_wheel_contract"／"two_wheel_employed"／
# "three_wheel_employed"，見 scripts/seed_cooperation_types.py 遷移腳本）：
# 下面 DOC_TYPES 的 `cooperation_types` 篩選欄位、
# TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES 都還在直接比對這幾個固定
# 字串，只要新建的 Firestore 文件 ID 跟舊代碼一模一樣，這些既有的保險/
# 試駕判斷邏輯完全不用改、既有人員資料也不用搬移。之後主管在「合作方式
# 管理」頁面新增的其他選項，會拿到 Firestore 自動產生的新 ID，不會跟這
# 三個固定判斷邏輯衝突（因為那些邏輯只認得這三個舊代碼字串本身）。

# 負責客戶：目前只有 UD 的人員會用到（決定要不要多備 MOMO 測驗），但欄位本身
# 不綁死在特定廠商上，之後其他廠商如果也分客戶，不用改架構。
CLIENTS = [
    {"code": "pchome", "name": "PCHOME"},
    {"code": "momo", "name": "MOMO"},
]
CLIENT_MAP = {c["code"]: c["name"] for c in CLIENTS}

# 哪些廠商的人員詳細頁要顯示「負責客戶」選單。這個欄位是全域欄位（值不因
# 廠商而異），但畫面上只有真的會用到的廠商才顯示，避免同仁在用不到的廠商
# 頁面上看到無意義的選單。「合作方式」選單改成動態清單後，哪些廠商看得到
# 選單改成看 repository.list_cooperation_types(vendor=...) 有沒有資料
# 決定，不再需要另外一份固定的廠商清單（見上面的說明）。
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
    {
        "code": "police_clearance",
        "name": "良民證",
        "kind": "file_expiry",
        "exclude_vendors": [
            "shopee",
            "shopee_company_car",
            "shopee_employed_own_car",
            "shopee_contract",
            "shopee_speed_warehouse",
        ],
    },
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
    # 蝦皮承攬／蝦皮二輪雇傭自備車專屬（2026-09-13 蝦皮廠商拆分後新增）：
    # 這兩個代碼本身已經講清楚雇用/承攬關係，保險規則直接綁廠商代碼本身
    # （不看合作方式），跟下面順豐的 sf_insurance／sf_guild_insurance 是
    # 同一種寫法——即使 2026-09-18 起合作方式選單開放給全部廠商設定，這
    # 兩個代碼的保險判斷邏輯還是不看合作方式欄位，維持原本的行為。「蝦皮
    # 二輪公司車」依使用者確認，強制險等保險文件由公司統一投保，不需要
    # 同仁個人上傳，所以沒有對應的項目。
    {
        "code": "shopee_contract_insurance",
        "name": "強制險",
        "kind": "file_expiry",
        "include_vendors": ["shopee_contract"],
    },
    {
        "code": "shopee_contract_guild_insurance",
        "name": "公會加保證明",
        "kind": "file_expiry",
        "include_vendors": ["shopee_contract"],
        "required": False,
    },
    {
        "code": "shopee_employed_own_car_insurance",
        "name": "強制險",
        "kind": "file_expiry",
        "include_vendors": ["shopee_employed_own_car"],
    },
    {
        "code": "shopee_employed_own_car_liability_insurance",
        "name": "營業用第三責任險",
        "kind": "file_expiry",
        "include_vendors": ["shopee_employed_own_car"],
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

# ==========================================
# 假別登記
# 2026-09-11 依勞基法擴充：原本只有病假/事假/特休/其他四種，且只記錄
# 起訖日期、沒有時數／額度概念。這次改成「一天一筆、記時數」，並且加上
# 法定額度自動試算＋90% 提醒，詳見 repository.py「假別額度計算」那一節
# 的說明。
#
# 每一項的欄位：
#   - quota_basis：額度怎麼算週期。
#       "calendar"：曆年制（每年 1/1~12/31 重新歸零）。
#       "anniversary"：到職週年制（只有特休用，週期是「到職日~隔年到職日
#         前一天」，依到職週年往後推）。
#       None：沒有固定額度（公假、其他、育嬰留職停薪），不算累積、不會
#         被 90% 提醒掃到。
#   - quota_days：法定年度上限天數。特休是 None（依年資查
#     ANNUAL_LEAVE_TIERS 表，不是固定數字），quota_basis 是 None 的假別
#     也是 None（代表沒有上限）。
#   - shares_quota_with：這個假別的用量要「額外」算進另一個假別的額度
#     消耗裡（目前只有家庭照顧假——本身上限 7 天，同時法規規定這 7 天要
#     算在事假 14 天的額度裡）。見 repository._quota_pool_codes()。
LEAVE_TYPES = [
    {"code": "annual", "name": "特休", "quota_basis": "anniversary", "quota_days": None},
    {"code": "personal", "name": "事假", "quota_basis": "calendar", "quota_days": 14},
    {"code": "sick", "name": "病假", "quota_basis": "calendar", "quota_days": 30},
    {"code": "marriage", "name": "婚假", "quota_basis": "calendar", "quota_days": 8},
    {"code": "funeral", "name": "喪假", "quota_basis": "calendar", "quota_days": 8},
    {"code": "menstrual", "name": "生理假", "quota_basis": "calendar", "quota_days": 3},
    {"code": "family_care", "name": "家庭照顧假", "quota_basis": "calendar", "quota_days": 7, "shares_quota_with": "personal"},
    {"code": "official", "name": "公假", "quota_basis": None, "quota_days": None},
    {"code": "maternity", "name": "產假", "quota_basis": "calendar", "quota_days": 56},
    {"code": "prenatal_checkup", "name": "產檢假", "quota_basis": "calendar", "quota_days": 7},
    {"code": "paternity", "name": "陪產（檢）假", "quota_basis": "calendar", "quota_days": 7},
    {"code": "parental_unpaid", "name": "育嬰留職停薪", "quota_basis": None, "quota_days": None},
    {"code": "other", "name": "其他", "quota_basis": None, "quota_days": None},
]
LEAVE_TYPE_MAP = {t["code"]: t["name"] for t in LEAVE_TYPES}
LEAVE_TYPE_LOOKUP = {t["code"]: t for t in LEAVE_TYPES}

# 特休依到職年資的級距（勞基法第38條）：
#   未滿半年 0 天／半年以上未滿1年 3天／1年以上未滿2年 7天／
#   2年以上未滿3年 10天／3年以上未滿5年每年14天／5年以上未滿10年每年15天／
#   10年以上每滿1年加1天，最高30天。
# 實際的分級判斷寫在 repository.compute_annual_leave_days()（純函式，方便
# 單元測試），這裡只記錄法條依據，不是真的查表用的資料結構。
ANNUAL_LEAVE_MAX_DAYS = 30

# 一天正常工時（時數／天數互相換算用，例如查詢頁面顯示「已用 24 小時
# ≈ 3 天」）。
WORKDAY_HOURS = 8

# 年度假別額度用到 90% 時觸發 LINE 提醒的門檻。使用者要求達到門檻後
# 「每次都要提醒」，不像文件到期提醒有「幾天內提醒過就不重複」的機制。
LEAVE_QUOTA_ALERT_RATIO = 0.9

# 應徵名單的廠商/合作方式：跟人員的 vendor/cooperation_type 是同一套代碼，
# 合作方式一樣是 repository.list_cooperation_types() 查來的動態清單（跟
# 人員詳細頁共用同一份資料）。應徵階段沒表單欄位可以填廠商，是由送出
# webhook 的 Apps Script 各自帶固定的廠商代碼過來（見
# routes/webhook_routes.py），畫面上保留讓同仁手動修改的權限。

# 試駕狀態：未試駕（預設）／通過／未通過。
TEST_DRIVE_STATUSES = [
    {"code": "not_tested", "name": "未試駕"},
    {"code": "passed", "name": "通過"},
    {"code": "failed", "name": "未通過"},
]
TEST_DRIVE_STATUS_MAP = {s["code"]: s["name"] for s in TEST_DRIVE_STATUSES}
DEFAULT_TEST_DRIVE_STATUS = "not_tested"

# 哪些應徵者需要試駕：UD、UC 一律需要；合作方式選了「三輪雇傭」（不分
# 哪個廠商）也需要——目前只有蝦皮三輪/速配倉會用到「三輪雇傭」這個選項，
# 但判斷邏輯本身（repository.applicant_needs_test_drive()）已經不再看
# 廠商代碼，只看合作方式的值是不是「three_wheel_employed」這個固定 ID，
# 這個 ID 是遷移腳本刻意保留下來的（見上面合作方式那節的說明），不會因為
# 合作方式改成動態清單而失效。
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

# 輪別（三輪／二輪）：2026-09-14 新增。這是車輛本身的固定屬性，跟廠商一樣
# 不會因為領還車事件改變，新增車輛時預設三輪（目前車隊以三輪車為主），
# 既有車輛（Firestore 裡還沒有這個欄位的舊資料）在讀取時一律當成三輪
# （見 repository.get_vehicle() / list_vehicles()），不用另外寫遷移腳本
# 補資料；管理員可以在車輛詳細頁個別修正成二輪。
WHEEL_TYPES = [
    {"code": "three_wheel", "name": "三輪"},
    {"code": "two_wheel", "name": "二輪"},
]
WHEEL_TYPE_MAP = {w["code"]: w["name"] for w in WHEEL_TYPES}
DEFAULT_WHEEL_TYPE = "three_wheel"

# ==========================================
# UD 廠商車輛領車/還車自動同步到材霈自己維護的「三輪車」Google Sheet
# （2026-09-22 新增，跟使用者討論確認的設計，見
# delivery/ud_vehicle_sheet_sync.py 的完整說明）。
# ==========================================

# 這份試算表需要分享「編輯者」權限給 Cloud Run 服務帳戶才寫得進去（跟
# salesdev/sheet_import.py 是同一個服務帳戶，權限分開分享）。
UD_VEHICLE_SHEET_ID = os.getenv("UD_VEHICLE_SHEET_ID", "11bN718SeTpOmomDkNO6ht_DiAM9Nj2y_54irASY5Wmw")

# 目標分頁的 gid（不是分頁名稱），從試算表網址 `...#gid=1971778587` 這段
# 抓到的。用 gid 定位分頁比用分頁名稱字串比對穩定——分頁改名字、或名稱裡
# 全形/半形括號打法不一致（這個系統之前在部門名稱比對就踩過一樣的雷，見
# platform_accounts.normalize_department() 的說明）都不會影響。**這個值
# 只有在這個分頁被整個刪掉重建（不是改名，是真的刪掉）時才會變，需要
# 工程師改這裡重新部署，一般改名稱/搬動欄位順序都不用動這裡。**
UD_VEHICLE_SHEET_GID = 1971778587

# 服務區域（車輛實際派駐/服務的縣市）：給「一鍵整理車輛狀況」報告（見
# delivery/vehicle_status_report.py）分區統計用。2026-09-14 新增時是寫死
# 在這裡的固定清單，2026-09-18 改成主管可以自行在網頁上新增/停用的動態
# 清單（存 Firestore，見 repository.py「車輛服務區域管理」那節），跟裝備
# 借還管理的品項/放置點是同一套「動態清單」設計——不再需要公司拓點到新
# 縣市時特地找 Claude 加代碼。既有車輛的 service_area 欄位存的是舊代碼
# （"taipei"／"new_taipei"…），改版時用 scripts/seed_vehicle_service_areas.py
# 把這些舊代碼原封不動建成 Firestore 文件的「文件 ID」，確保既有車輛資料
# 不需要搬移，讀取時一樣能對應到正確的服務區域名稱。

# ==========================================
# 意外事件回報
# 跟車輛回報同一個 LINE 群組回報格式（見 delivery/incident_report.py），
# 網頁上這幾個固定選項的欄位直接存中文字串本身當值（不像廠商/合作方式那樣
# 另外配一組英文代碼），因為這幾個欄位純粹是紀錄用途，沒有跟其他業務邏輯
# 掛勾，不需要多一層代碼轉換。
# ==========================================
IDENTITY_TYPES = ["雇傭", "承攬"]
DUTY_STATUSES = ["執行勤務中", "上下班途中"]
YES_NO_VALUES = ["有", "無"]

# 2026-09-15：使用者確認「是否報警」這一項單獨改用「是」「否」（跟「是否
# 聯繫家屬」「是否牽扯他人」維持原本的「有」「無」不同），網站表單跟 LINE
# 群組回報範本（見 delivery/incident_report.py）都要求填這一組新值——這是
# 特意的破壞性改動，舊範本（回報時還打「有」／「無」）會被系統判定格式
# 有誤，需要同時公告新範本給配送組同仁。既有已經存進資料庫的舊紀錄（值是
# 「有」／「無」）不會被回溯修改，詳細頁維持原樣顯示，編輯表單開啟舊紀錄
# 時會把「有」視同「是」、「無」視同「否」預選（見 incident_routes.py）。
POLICE_CALLED_VALUES = ["是", "否"]

RISK_LEVELS = ["低", "中", "高"]

INCIDENT_STATUSES = [
    {"code": "open", "name": "未結案"},
    {"code": "closed", "name": "已結案"},
]
INCIDENT_STATUS_MAP = {s["code"]: s["name"] for s in INCIDENT_STATUSES}
DEFAULT_INCIDENT_STATUS = "open"

# ==========================================
# 裝備借還管理
# 2026-09-17 新增。品項（籃子、橘衣...）跟放置點（新北所、桃園所...）刻意
# 不像廠商/假別那樣寫死在這裡——這兩份清單預期會比車輛廠商還常變動，改成
# 主管可以自己在網頁上新增/停用的動態清單，存在 Firestore
# （delivery_equipment_items / delivery_equipment_locations），這裡只放
# 「異動類型」這種真的不會讓使用者自己增加種類的固定清單。
# ==========================================
EQUIPMENT_TRANSACTION_TYPES = [
    {"code": "borrow", "name": "借用"},
    {"code": "return", "name": "歸還"},
    {"code": "transfer", "name": "轉倉（轉出／轉入）"},
    {"code": "purchase", "name": "採購新增"},
    {"code": "buyout", "name": "買斷"},
    {"code": "writeoff", "name": "核銷"},
]
EQUIPMENT_TRANSACTION_TYPE_MAP = {t["code"]: t["name"] for t in EQUIPMENT_TRANSACTION_TYPES}

# 「轉倉」原始需求文件把「轉出」「轉入」列成兩種異動類型，但這裡刻意合併
# 成一種「轉倉」動作、一次選「從哪個放置點→到哪個放置點」，用同一筆紀錄
# 同時扣掉來源庫存、加回目的庫存——這樣「轉入一定對應轉出」是資料結構
# 保證的（同一筆紀錄），不需要另外做「登記轉出後，等對方確認收到才算
# 轉入」這種跨兩個步驟、中途會有「在途中」狀態的流程。如果之後發現運送
# 中途真的需要有「已出貨、對方還沒收到」這種待確認狀態，才需要拆成兩步。
EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL = {"borrow", "return", "buyout"}
EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS = {"transfer"}

# 核銷（公司認賠、尚欠直接歸零）風險最高、直接影響帳務，限主管操作。
# 買斷雖然也涉及金錢，但性質是「同仁登記騎士已經付錢了結」，同仁本來就是
# 第一線在處理離職人員的裝備結算，開放一般同仁登記；使用者未來如果覺得
# 買斷也該限主管，這個集合直接加 "buyout" 即可。
EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES = {"writeoff"}

# 只有「在職」的人員才能借裝備——跟人員缺件清單預設隱藏離職/放棄報到的人
# 是同一個道理，不應該讓已經離職的人還掛在借用名單裡。
EQUIPMENT_ELIGIBLE_PERSONNEL_STATUS = "employed"

# ==========================================
# 外送員接單媒合（即時接單／報班媒合，2026-09-19 新增）
# 跟車輛/意外事件回報同一個 delivery-gas-project 專案、同一個 CHANNEL1 LINE
# 官方帳號，但轉發到這裡的是騎士的 1 對 1 私訊（Postback／文字／位置訊息），
# 不是群組訊息。沿用同一種「共用密鑰驗證」做法：GAS 那邊呼叫
# /delivery/api/rider-events（LINE 事件轉發）跟
# /delivery/api/rider-binding-sync（工號/姓名綁定表同步）這兩支 webhook 都
# 帶同一把密鑰（X-Delivery-Rider-Secret），未設定時兩支端點一律回傳 403。
#
# 刻意不像規格書原本設想的那樣，另外讓這裡持有一份 CHANNEL1 的 Channel
# Access Token 自己呼叫 LINE Reply API——沿用車輛/意外事件回報現有的做法：
# GAS 同步呼叫這裡、等回應、GAS 自己用它手上已經有的 Token 回覆
# LINE（見 delivery-gas-project 的 replyLineRawMessages_()），這裡只需要
# 回傳一份 LINE 訊息物件的 JSON 陣列（{"messages": [...]}），不需要另外管理
# 一把新的 LINE 憑證，也不用擔心 replyToken 時效——跟現有兩個功能是同一條
# 已經穩定運作的路徑。
RIDER_WEBHOOK_SECRET = os.getenv("DELIVERY_RIDER_WEBHOOK_SECRET", "")

# 騎士私訊「查詢附近單」／「瀏覽報班」之後，系統會先請他分享位置，這時候
# 他還沒真的分享——這裡用騎士綁定資料上的 awaiting_location／
# awaiting_shift_location 欄位暫存「正在等他分享位置」。避免騎士問完卻放著
# 不理，隔了很久才隨手分享一個不相干的位置也被當成在查詢，暫存狀態超過這個
# 秒數就視為過期、不再採用。
#
# **2026-09-22 起承接不再需要輸入件數**（點「承接」就直接完成，見
# rider_repository.py 的 DEFAULT_RIDER_CAPACITY 說明），原本同一個秒數也用在
# 「等騎士回覆件數」的 pending_claim 暫存上，那套機制已經整個移除，這個常數
# 現在只剩分享位置這一種用途，名稱維持不變避免無謂的改動。
RIDER_PENDING_CLAIM_TTL_SECONDS = 600

# 即時接單／報班媒合的「幾公里內才看得到」預設值（2026-09-21 新增）。
# 同仁在後台開門市當日量/報班時段時，可以各自調整這一筆要開放給幾公里內
# 的騎士，這裡只是表單沒填、或舊資料沒有這個欄位時的預設值——不是全域
# 唯一的設定，每一筆需求可以各自不一樣。
RIDER_DEFAULT_SEARCH_RADIUS_KM = 10

