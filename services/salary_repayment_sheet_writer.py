"""薪資補款搬離 GAS 階段 2 的第 3 步（2026-09-25）：平台把補款單寫回「薪資補款紀錄」試算表。

**為什麼要寫回**：財務看的是另一份「材霈會計對帳表」，裡面用
`QUERY(IMPORTRANGE(…"薪資補款紀錄!A:U"…))` 直接帶「薪資補款紀錄」分頁的 A～U 欄（排除 D 欄 LINE ID，
見 job-portal-gas-project `程式碼.js` 的 `setupAccountingSyncSpreadsheet()`）。所以只要平台照 GAS 一模
一樣的方式寫進「薪資補款紀錄」，會計對帳表就會自動更新，財務那邊什麼都不用改。

照 GAS（`Project_Salary.js`）原本的三種寫法：
- 新的補款單 → `append_record()`：在最後加一列（GAS 的 `appendRow`）。
- 核准 → `update_review()`：只改「審核狀態」「核准主管」「核准時間」三格（GAS 的
  `updateSalaryReviewStatus`）。
- 退回 → `update_review(status="已退回")`：**整列刪除**（GAS 也是刪掉，退回的不給會計看）。

欄位一律**用表頭文字對位置**（每次先讀第 1 列），不寫死欄位字母；傳進來的欄位表頭上沒有的就不寫、
回報出來。寫入用 `USER_ENTERED`，跟 GAS `appendRow` 一樣，日期時間文字會被試算表當成日期。

**這一步只有程式，還不會被觸發**：第 4 步（送出改由平台處理）、第 5 步（核准改由平台處理）上線時才會
呼叫。現在唯一會用到的是 `check_write_access()`：`/finance/migration` 的「檢查寫入權限」按鈕，把第 1 列
表頭原封不動寫回去，試算表內容完全不變，只用來確認服務帳戶真的有「編輯者」權限。

所有函式都回傳 `(ok, 訊息)`，不拋例外。
"""
from config import SALARY_REPAYMENT_RECORDS_SHEET_NAME, SALARY_REPAYMENT_SHEET_ID

ID_COLUMN = "補款單號"
STATUS_COLUMN = "審核狀態"
APPROVER_COLUMN = "核准主管"
APPROVED_AT_COLUMN = "核准時間"
STATUS_REJECTED = "已退回"

_NO_PERMISSION = (
    "平台沒有權限寫入這份試算表。請到「薪資補款」試算表按「共用」，把 Cloud Run 服務帳戶的權限改成「編輯者」。"
)


def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _tab() -> str:
    return f"'{SALARY_REPAYMENT_RECORDS_SHEET_NAME}'"


def _col_letter(index: int) -> str:
    """0 起算的欄位編號 → 欄位字母（0→A、25→Z、26→AA）。"""
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _error_message(e: Exception) -> str:
    status = getattr(getattr(e, "resp", None), "status", None)
    if status == 403:
        return _NO_PERMISSION
    if status == 404:
        return "找不到「薪資補款」試算表或「薪資補款紀錄」分頁，請確認設定。"
    return f"寫入試算表時發生錯誤：{e}"


def _headers(service) -> list:
    result = service.spreadsheets().values().get(
        spreadsheetId=SALARY_REPAYMENT_SHEET_ID, range=f"{_tab()}!1:1"
    ).execute()
    rows = result.get("values", [])
    return [str(h) for h in rows[0]] if rows else []


def _find_row(service, salary_id: str):
    """回傳補款單號所在的試算表列號（1 起算），找不到回傳 None。重複的單號取第一筆（跟 GAS 一樣）。"""
    result = service.spreadsheets().values().get(
        spreadsheetId=SALARY_REPAYMENT_SHEET_ID, range=f"{_tab()}!A:A"
    ).execute()
    for index, row in enumerate(result.get("values", []), start=1):
        if index > 1 and row and str(row[0]).strip() == salary_id:
            return index
    return None


def _sheet_gid(service):
    meta = service.spreadsheets().get(
        spreadsheetId=SALARY_REPAYMENT_SHEET_ID, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == SALARY_REPAYMENT_RECORDS_SHEET_NAME:
            return props.get("sheetId")
    return None


def check_write_access() -> tuple:
    """把第 1 列表頭原樣寫回去，確認有編輯權限。試算表內容不會有任何改變。"""
    if not SALARY_REPAYMENT_SHEET_ID:
        return False, "尚未設定 SALARY_REPAYMENT_SHEET_ID。"
    try:
        service = _get_sheets_service()
        headers = _headers(service)
        if not headers:
            return False, "「薪資補款紀錄」分頁第 1 列是空的，找不到表頭。"
        service.spreadsheets().values().update(
            spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
            range=f"{_tab()}!A1:{_col_letter(len(headers) - 1)}1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
    except Exception as e:
        return False, _error_message(e)
    return True, f"平台可以寫入「薪資補款紀錄」分頁（表頭共 {len(headers)} 欄，內容沒有任何改變）。"


def append_record(fields: dict) -> tuple:
    """新增一列。fields 的 key 是表頭文字（跟 `salary_repayment_store` 存的 `fields` 一樣）。"""
    try:
        service = _get_sheets_service()
        headers = _headers(service)
        if not headers:
            return False, "「薪資補款紀錄」分頁第 1 列是空的，找不到表頭。"
        missing = [key for key in fields if key not in headers]
        row = ["" if fields.get(h) is None else fields.get(h, "") for h in headers]
        service.spreadsheets().values().append(
            spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
            range=f"{_tab()}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    except Exception as e:
        return False, _error_message(e)
    if missing:
        return True, f"已寫入，但試算表沒有這些欄位、所以沒寫：{'、'.join(missing)}"
    return True, "已寫入試算表。"


def update_review(salary_id: str, status: str, approver: str = "", approved_at: str = "") -> tuple:
    """核准：改審核狀態／核准主管／核准時間三格；退回：整列刪除（跟 GAS 一樣）。"""
    try:
        service = _get_sheets_service()
        row_number = _find_row(service, salary_id)
        if row_number is None:
            return False, f"試算表裡找不到補款單號「{salary_id}」。"
        if status == STATUS_REJECTED:
            gid = _sheet_gid(service)
            if gid is None:
                return False, "找不到「薪資補款紀錄」分頁。"
            service.spreadsheets().batchUpdate(
                spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
                body={"requests": [{"deleteDimension": {"range": {
                    "sheetId": gid, "dimension": "ROWS", "startIndex": row_number - 1, "endIndex": row_number,
                }}}]},
            ).execute()
            return True, "已從試算表刪除這筆退回的補款單。"
        headers = _headers(service)
        updates = {STATUS_COLUMN: status, APPROVER_COLUMN: approver, APPROVED_AT_COLUMN: approved_at}
        data = []
        for column, value in updates.items():
            if column not in headers:
                return False, f"試算表找不到「{column}」欄。"
            data.append({"range": f"{_tab()}!{_col_letter(headers.index(column))}{row_number}", "values": [[value]]})
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
    except Exception as e:
        return False, _error_message(e)
    return True, "已更新試算表的審核狀態。"
