"""少凱業務開發專區（/salesdev）讀取 Google Sheet 內容並整理成網頁表格資料。

跟 services/factory_watch_service.py 一樣用 Cloud Run 服務帳戶的 ADC
（Application Default Credentials）連線，差別是這裡只讀不寫，所以用唯讀
scope、只要分享「檢視者」權限給服務帳戶即可，不需要「編輯者」。
"""
from config import SALESDEV_SHEET_ID

# 避免單一分頁資料量過大時整頁一次全部渲染拖慢畫面，超過的部分不顯示
# （tab["truncated"] 會是 True，畫面上會提示還有更多資料）。
MAX_ROWS_PER_TAB = 500

_SERVICE_ACCOUNT_HINT = (
    "沒有權限讀取這份 Google Sheet，請把這份試算表分享「檢視者」權限給 "
    "Cloud Run 服務帳戶（tsaipei-505807 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱）。"
)


def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _normalize_row(row: list, width: int) -> list:
    # Google Sheets API 省略掉每列尾端的空白儲存格，同一列讀回來的長度可能
    # 比表頭短，補空字串讓樣板可以直接用固定欄位數畫表格，不用另外判斷。
    return row + [""] * (width - len(row))


def fetch_sheet_tabs():
    """回傳 (tabs, error)。

    成功時 tabs 是 [{"title", "headers", "rows", "truncated"}, ...]（每個
    分頁一筆），error 是 None；失敗時 tabs 是空列表，error 是可以直接顯示
    在畫面上的中文說明。刻意把所有例外攔在這裡，不讓例外往外炸出 500
    錯誤頁——這支只是唯讀彙整頁面，讀不到資料時應該顯示清楚的設定提示。
    """
    if not SALESDEV_SHEET_ID:
        return [], "尚未設定 SALESDEV_SHEET_ID，請聯絡系統管理員設定要顯示的 Google Sheet。"

    from googleapiclient.errors import HttpError

    try:
        service = _get_sheets_service()
        meta = service.spreadsheets().get(spreadsheetId=SALESDEV_SHEET_ID).execute()
    except HttpError as e:
        if e.resp.status == 403:
            return [], _SERVICE_ACCOUNT_HINT
        if e.resp.status == 404:
            return [], "找不到這份 Google Sheet，請確認 SALESDEV_SHEET_ID 設定的試算表 ID 是否正確。"
        return [], f"讀取 Google Sheet 時發生錯誤：{e}"
    except Exception as e:
        return [], f"讀取 Google Sheet 時發生錯誤：{e}"

    sheet_titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not sheet_titles:
        return [], "這份 Google Sheet 目前沒有任何分頁。"

    ranges = [f"'{title}'!A1:Z{MAX_ROWS_PER_TAB + 1}" for title in sheet_titles]
    try:
        result = service.spreadsheets().values().batchGet(
            spreadsheetId=SALESDEV_SHEET_ID, ranges=ranges
        ).execute()
    except HttpError as e:
        return [], f"讀取 Google Sheet 內容時發生錯誤：{e}"

    tabs = []
    for title, value_range in zip(sheet_titles, result.get("valueRanges", [])):
        rows = value_range.get("values", [])
        if not rows:
            tabs.append({"title": title, "headers": [], "rows": [], "truncated": False})
            continue
        headers = rows[0]
        data_rows = rows[1:]
        truncated = len(data_rows) > MAX_ROWS_PER_TAB
        data_rows = data_rows[:MAX_ROWS_PER_TAB]
        tabs.append(
            {
                "title": title,
                "headers": headers,
                "rows": [_normalize_row(row, len(headers)) for row in data_rows],
                "truncated": truncated,
            }
        )

    return tabs, None
