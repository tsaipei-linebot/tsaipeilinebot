"""我的專區（/me）目前唯一的小工具：薪資補款紀錄。

資料來源是「職缺維護表單」（Netlify + Apps Script，跟這個 repo 完全獨立的
系統，見 CLAUDE.md）背後的 Google Sheet，這裡只讀、不寫回，用跟
services/factory_watch_service.py／services/salesdev_sheet_service.py 一樣的
Cloud Run 服務帳戶 ADC 連線。

這份試算表有兩個相關分頁：
- 「員工主管組織表」：只用來把補款紀錄裡「核准主管」欄位存的 LINE ID
  換算回看得懂的姓名（見 `build_line_id_name_lookup`）。**誰是誰的主管
  這件事本身，不再讀這個分頁**——改成讀系統自己的帳號資料
  （`platform_accounts` 的 `manager_usernames` 欄位，在 `/accounts` 網頁上
  設定），比對用的是帳號本身，不是文字姓名，才不會有同名同姓或姓名打法
  不一致誤判權限的問題（第一版曾經是讀這個分頁的「主管姓名」文字欄位，
  改掉的原因見 `platform_accounts.py` 的說明；既有資料可以用
  `scripts/import_account_managers.py` 批次匯入一次）。
- 「薪資補款紀錄」：同仁在職缺維護表單送出的補款申請，「申請人姓名」是
  送出申請的同仁本人，「員工姓名」欄位反而是被補款的配送人員（不是同一個
  人），比對權限用的是「申請人姓名」。

「申請人姓名」跟系統帳號之間的比對，還是文字姓名完全相同（跟
job_portal_sso.py 的比對方式一致）——這一段沒辦法避免，因為這筆資料是
同仁在外部表單填的，不是這個系統產生的；姓名對不上的話，該筆資料就看不到，
不會噴錯，只是查不到而已。
"""
from config import (
    SALARY_REPAYMENT_ORG_SHEET_NAME,
    SALARY_REPAYMENT_RECORDS_SHEET_NAME,
    SALARY_REPAYMENT_SHEET_ID,
)
import platform_accounts

_SERVICE_ACCOUNT_HINT = (
    "沒有權限讀取這份 Google Sheet，請把這份試算表分享「檢視者」權限給 "
    "Cloud Run 服務帳戶（tsaipei-505807 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱）。"
)

# 顯示在畫面上的欄位，用表頭文字比對、不是欄位順序，避免之後試算表調整欄位
# 順序就抓錯資料。刻意不顯示「加項小計/扣項小計/補款金額(總計)」這幾欄——
# 目前資料大多是 0 或只在少數列有值，跟「實補總額」重複，先不顯示，之後
# 財務真的需要拆項目再加回來即可。
DISPLAY_COLUMNS = [
    "補款單號", "申請時間", "員工姓名", "身分證", "廠商/店家", "申請日", "付款日",
    "補請款月份", "是否可請款", "補款方式", "實補總額", "備註", "審核狀態", "核准主管",
]


def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def rows_to_dicts(values: list) -> list:
    if not values:
        return []
    headers = values[0]
    dicts = []
    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        dicts.append(dict(zip(headers, padded)))
    return dicts


def parse_name_list(raw: str) -> list:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def build_manager_lookup_from_accounts(accounts: list) -> dict:
    """回傳 {員工姓名: [主管姓名, ...]}，資料來源是系統帳號的
    `manager_usernames` 欄位（在 /accounts 網頁上設定，存的是帳號本身，不是
    文字姓名），這裡轉換成姓名清單只是為了跟 `filter_visible_records()` 既有
    的姓名比對邏輯相容。姓名重複（同名同姓）的帳號沒有特別處理，跟系統
    帳號本身「用姓名顯示、用帳號比對」的既有限制一致。"""
    name_by_username = {a["username"]: a["name"] for a in accounts}
    lookup = {}
    for a in accounts:
        lookup[a["name"]] = [
            name_by_username[u] for u in a.get("manager_usernames", []) if u in name_by_username
        ]
    return lookup


def build_line_id_name_lookup(org_rows: list) -> dict:
    """回傳 {員工 LINE ID: 員工姓名}，用來把補款紀錄裡「核准主管」的 LINE ID
    換成看得懂的姓名。"""
    lookup = {}
    for row in org_rows:
        line_id = (row.get("員工 LINE ID") or "").strip()
        name = (row.get("員工姓名") or "").strip()
        if line_id and name:
            lookup[line_id] = name
    return lookup


def filter_visible_records(records: list, manager_lookup: dict, viewer_name: str) -> list:
    """回傳 viewer_name 有權限看到的補款紀錄：自己以「申請人姓名」送出的，
    或是自己是這個申請人的主管（含逗號分隔的多個主管）。"""
    visible = []
    for record in records:
        applicant = (record.get("申請人姓名") or "").strip()
        if not applicant:
            continue
        if applicant == viewer_name or viewer_name in manager_lookup.get(applicant, []):
            visible.append(record)
    return visible


def _resolve_approver_name(record: dict, line_id_name_lookup: dict) -> str:
    line_id = (record.get("核准主管") or "").strip()
    if not line_id:
        return ""
    return line_id_name_lookup.get(line_id, line_id)


def get_my_repayment_records(viewer_name: str):
    """回傳 (records, error)。records 是 viewer_name 看得到的補款紀錄列表
    （已經把「核准主管」從 LINE ID 換成姓名、依申請時間新到舊排序）；讀取
    失敗時 records 是空列表，error 是可以直接顯示在畫面上的中文說明。"""
    if not SALARY_REPAYMENT_SHEET_ID:
        return [], "尚未設定 SALARY_REPAYMENT_SHEET_ID，請聯絡系統管理員設定。"

    from googleapiclient.errors import HttpError

    try:
        service = _get_sheets_service()
        result = service.spreadsheets().values().batchGet(
            spreadsheetId=SALARY_REPAYMENT_SHEET_ID,
            ranges=[
                f"'{SALARY_REPAYMENT_ORG_SHEET_NAME}'!A1:Z2000",
                f"'{SALARY_REPAYMENT_RECORDS_SHEET_NAME}'!A1:Z5000",
            ],
        ).execute()
    except HttpError as e:
        if e.resp.status == 403:
            return [], _SERVICE_ACCOUNT_HINT
        if e.resp.status == 404:
            return [], "找不到這份 Google Sheet 或指定的分頁，請確認 config.py 的分頁名稱設定是否正確。"
        return [], f"讀取 Google Sheet 時發生錯誤：{e}"
    except Exception as e:
        return [], f"讀取 Google Sheet 時發生錯誤：{e}"

    value_ranges = result.get("valueRanges", [])
    org_rows = rows_to_dicts(value_ranges[0].get("values", [])) if len(value_ranges) > 0 else []
    record_rows = rows_to_dicts(value_ranges[1].get("values", [])) if len(value_ranges) > 1 else []

    manager_lookup = build_manager_lookup_from_accounts(platform_accounts.list_accounts())
    line_id_name_lookup = build_line_id_name_lookup(org_rows)

    visible = filter_visible_records(record_rows, manager_lookup, viewer_name)
    for record in visible:
        record["核准主管"] = _resolve_approver_name(record, line_id_name_lookup)
    visible.sort(key=lambda r: r.get("申請時間", ""), reverse=True)

    return visible, None
