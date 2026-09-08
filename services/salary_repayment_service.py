"""我的專區（/me）目前唯一的小工具：薪資補款紀錄。

資料來源是「職缺維護表單」（Netlify + Apps Script，跟這個 repo 完全獨立的
系統，見 CLAUDE.md）背後的 Google Sheet，這裡只讀、不寫回，用跟
services/factory_watch_service.py／services/salesdev_sheet_service.py 一樣的
Cloud Run 服務帳戶 ADC 連線。

這份試算表有兩個相關分頁：
- 「員工主管組織表」：材霈自己同仁（不是配送人員）對到誰是他的主管，
  「主管姓名」欄位可能是逗號分隔的多個名字（例如同時受兩個主管管轄）。
  用這份資料判斷誰能看到誰的補款紀錄。
- 「薪資補款紀錄」：同仁在職缺維護表單送出的補款申請，「申請人姓名」是
  送出申請的同仁本人，「員工姓名」欄位反而是被補款的配送人員（不是同一個
  人），比對權限用的是「申請人姓名」。

比對邏輯是文字姓名完全相同（跟 job_portal_sso.py 的比對方式一致）；姓名
對不上的話，該筆資料就看不到，不會噴錯，只是查不到而已。
"""
from config import (
    SALARY_REPAYMENT_ORG_SHEET_NAME,
    SALARY_REPAYMENT_RECORDS_SHEET_NAME,
    SALARY_REPAYMENT_SHEET_ID,
)

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


def _rows_to_dicts(values: list) -> list:
    if not values:
        return []
    headers = values[0]
    dicts = []
    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        dicts.append(dict(zip(headers, padded)))
    return dicts


def _parse_name_list(raw: str) -> list:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def build_manager_lookup(org_rows: list) -> dict:
    """回傳 {員工姓名: [主管姓名, ...]}，員工姓名空白的列會被忽略。"""
    lookup = {}
    for row in org_rows:
        name = (row.get("員工姓名") or "").strip()
        if name:
            lookup[name] = _parse_name_list(row.get("主管姓名"))
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
    org_rows = _rows_to_dicts(value_ranges[0].get("values", [])) if len(value_ranges) > 0 else []
    record_rows = _rows_to_dicts(value_ranges[1].get("values", [])) if len(value_ranges) > 1 else []

    manager_lookup = build_manager_lookup(org_rows)
    line_id_name_lookup = build_line_id_name_lookup(org_rows)

    visible = filter_visible_records(record_rows, manager_lookup, viewer_name)
    for record in visible:
        record["核准主管"] = _resolve_approver_name(record, line_id_name_lookup)
    visible.sort(key=lambda r: r.get("申請時間", ""), reverse=True)

    return visible, None
