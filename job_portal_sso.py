"""職缺維護系統（獨立在 Netlify + Google Apps Script 的專案，不在這個 repo
裡、也刻意不去動它的程式碼）免登入銜接。

背景：那個系統的身分驗證是「姓名 + 4 碼 PIN」，資料存在一份 Google Sheet
（同仁在 LINE 群組用「綁定+姓名+PIN碼」自動寫入）。這個平台的帳號密碼是
完全獨立的另一套系統（platform_accounts.py），但每個帳號的「姓名」欄位本身
就是本名，跟職缺系統 Sheet 裡的姓名是同一個人、同一種寫法，所以可以直接
拿本名互相比對，不需要額外維護一份帳號對照表。

整體運作分兩段：
1. 排程把 Google Sheet 的姓名/PIN 定期同步進 Firestore（見
   sync_identities_from_sheet()），我們自己的請求路徑只讀 Firestore，
   不會即時去打 Google Sheets API。Sheet 裡的 PIN 欄位存的其實是無鹽
   SHA-256 雜湊值（不是明文），同步時會先換算回明文 PIN 再存進 Firestore
   （見 _resolve_plaintext_pin()），因為 VERIFY_LOGIN 端點要收明文 PIN。
   順便把 Sheet 裡的「員工 LINE ID」也一起存進 Firestore，目前沒有任何
   功能會用到，只是幫「以後可能的個人提醒功能」預先鋪路。
2. 同仁在 /portal 點「職缺維護系統」卡片時（見 portal_routes.py），拿他
   帳號的姓名去 Firestore 查對應的 PIN，查得到就簽發一組幾十秒後失效、
   一次性用途的代碼放在網址上帶過去；查不到就直接導去原本的網址，同仁
   照舊手動輸入姓名+PIN，不受影響。代碼本身不是真的 PIN，只有簽章合法、
   沒過期才能透過 /api/job-system-sso/exchange 換回真正的姓名+PIN——
   而且這個交換動作是那個系統的網頁自己在背景呼叫，不會出現在網址上，
   比直接把 PIN 放進網址安全很多。
"""
import hashlib
import os
import re
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from delivery.config import SESSION_SECRET_KEY
from platform_db import get_db

# Cloud Scheduler 呼叫 /internal/sync-job-system-identities 觸發同步時要帶的
# 共用密鑰（見 X-Job-Sheet-Sync-Secret header）。未設定時該端點一律回傳 403，
# 等同這個端點不存在——跟 main.py 的 LOAD_TEST_SECRET 是一樣的作法。
SYNC_TRIGGER_SECRET = os.getenv("JOB_SHEET_SYNC_SECRET", "")

# 職缺系統前端呼叫 /api/job-system-sso/exchange 這個跨網域請求，只允許從
# 它自己的網域發起（CORS 白名單只放這一個來源，不開放給任何其他網站）。
ALLOWED_EXCHANGE_ORIGIN = "https://ubiquitous-choux-38eefb.netlify.app"

# 那份「員工主管組織表」Google Sheet：網址裡 /d/ 跟 /edit 之間那一段就是
# SPREADSHEET_ID，SHEET_TAB_NAME 是分頁籤上顯示的名稱（不是 gid）。
SPREADSHEET_ID = "1rys_WkW2qZmqm9NFovlDWb_PXL_80seDTxelFTd9xSk"
SHEET_TAB_NAME = "員工主管組織表"
# A欄=員工姓名、I欄=PIN碼（其餘欄位跟這個銜接無關，不讀取）。
SHEET_RANGE = f"'{SHEET_TAB_NAME}'!A2:I"

JOB_LISTING_BASE_URL = "https://ubiquitous-choux-38eefb.netlify.app/"

IDENTITIES_COLLECTION = "job_system_identities"

# 一次性代碼的有效時間：只要夠同仁點擊卡片到頁面完成兌換這幾秒鐘就好，
# 故意抓短一點，即使代碼不小心外流（例如出現在瀏覽器紀錄），也幾乎沒有
# 被冒用的時間窗口。跟 session 的簽章金鑰共用同一組 secret，但用不同的
# salt 隔開，兩種用途的簽章不能互相冒充。
SSO_TOKEN_MAX_AGE_SECONDS = 45
_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="job-portal-sso")

# 職缺系統的組織表 PIN 欄位存的不是明文 PIN，而是 sha256Hash(pin)（見那個
# 系統主程式的 EmployeeRegistrationService.processRegistration()：對純
# 4 碼數字字串做「無鹽」SHA-256，沒有加任何鹽或姓名混入）。但 VERIFY_LOGIN
# 端點收到的 pin 參數必須是明文——它自己會再雜湊一次去跟 Sheet 裡的值比對
# （OrgService.verifyEmployeePin()），把雜湊值原封不動送過去等於雜湊了
# 兩次，一定對不上。
#
# 因為 PIN 只有 4 位數字、只有 10000 種可能組合，這裡預先算好這 10000 種
# 雜湊值對照回明文 PIN 的表，同步時直接反查——這不是在破解對方系統，這份
# 資料本來就是我們已經被授權讀取的同一份 Sheet，只是換成 VERIFY_LOGIN 看
# 得懂的形式（明文）而已。舊資料如果還是明文 4 碼（那支程式碼相容舊資料、
# 一旦驗證通過會自動升級成雜湊值），直接原樣使用即可，不需要查表。
_PIN_HASH_TO_PLAINTEXT = {
    hashlib.sha256(f"{i:04d}".encode("utf-8")).hexdigest(): f"{i:04d}" for i in range(10000)
}


def _resolve_plaintext_pin(raw_value: str):
    """把 Sheet 裡 PIN 欄位的原始值（明文 4 碼或 SHA-256 雜湊值）換算回
    明文 PIN；查不到對應明文（例如欄位值格式不明）回傳 None。"""
    value = (raw_value or "").strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    return _PIN_HASH_TO_PLAINTEXT.get(value.lower())


def _identities_ref():
    return get_db().collection(IDENTITIES_COLLECTION)


def sync_identities_from_sheet() -> int:
    """讀取 Google Sheet 目前的姓名/PIN，鏡射寫進 Firestore（存的是換算回
    明文後的 PIN，見 _resolve_plaintext_pin()）。回傳同步的筆數。姓名是
    空白、PIN 欄位是空白、或 PIN 欄位換算不出明文的列一律跳過（分別對應
    「還沒完成 LINE 綁定」「還沒設定 PIN」「格式不明的舊資料」）。

    用 google.auth.default() 走 Cloud Run 掛載的服務帳號身分（跟
    firestore.Client() 是同一套 Application Default Credentials 機制），
    這個服務帳號要先被加進那份 Sheet 的共用名單（檢視者權限）才讀得到。
    """
    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    service = build("sheets", "v4", credentials=credentials)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=SHEET_RANGE)
        .execute()
    )
    rows = result.get("values", [])

    synced_at = time.time()
    count = 0
    for row in rows:
        name = (row[0] if len(row) > 0 else "").strip()
        # B欄=員工LINE ID：這次不會拿來做任何事，純粹是幫「以後可能的
        # 個人提醒功能」預先鋪路——反正同步程式已經在讀這一列，順便多存
        # 一欄不多花成本，之後真的要用不用回頭改同步邏輯。
        line_id = (row[1] if len(row) > 1 else "").strip()
        raw_pin = (row[8] if len(row) > 8 else "").strip()
        if not name or not raw_pin:
            continue
        pin = _resolve_plaintext_pin(raw_pin)
        if not pin:
            continue
        _identities_ref().document(name).set(
            {"name": name, "pin": pin, "line_id": line_id, "synced_at": synced_at}
        )
        count += 1
    return count


def find_identity_by_name(name: str):
    """回傳 {"name":..., "pin":...} 或 None（Firestore 鏡射資料裡查不到
    這個本名，代表這個人還沒在職缺系統那邊完成 LINE 綁定，或者兩邊姓名
    寫法對不上）。"""
    name = (name or "").strip()
    if not name:
        return None
    snapshot = _identities_ref().document(name).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    if not data.get("pin"):
        return None
    return {"name": data.get("name", name), "pin": data["pin"]}


def mint_sso_token(name: str, pin: str) -> str:
    return _serializer.dumps({"name": name, "pin": pin})


def verify_sso_token(token: str):
    """驗證通過回傳 {"name":..., "pin":...}；簽章不對或超過
    SSO_TOKEN_MAX_AGE_SECONDS 一律回傳 None（過期跟偽造都當同一種處理，
    呼叫端不需要區分原因）。"""
    try:
        return _serializer.loads(token, max_age=SSO_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
