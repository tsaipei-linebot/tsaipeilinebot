"""少凱業務開發專區（/salesdev）讀取 Google Sheet 內容並整理成網頁表格資料。

跟 services/factory_watch_service.py 一樣用 Cloud Run 服務帳戶的 ADC
（Application Default Credentials）連線。讀取（`fetch_sheet_tabs()`）維持
唯讀 scope、只要分享「檢視者」權限即可；2026-09-17 新增「勾選要反查的職缺」
功能後，`mark_rows_selected_for_reverse_lookup()` 這個寫入路徑改用可讀寫的
scope，這份試算表需要額外分享「編輯者」權限給同一個服務帳戶才能用（見
HANDOFF.md「少凱業務開發專區：勾選反查」章節的上線前設定）。
"""
from config import SALESDEV_SHEET_ID

# 避免單一分頁資料量過大時整頁一次全部渲染拖慢畫面，超過的部分不顯示
# （tab["truncated"] 會是 True，畫面上會提示還有更多資料）。
MAX_ROWS_PER_TAB = 500

# 「審查狀態」欄位的三種值，要跟 tsaipei-linebot-recruitment-leads-scraper
# 這個抓職缺程式（src/models.py 的 REVIEW_STATUS_*）完全一致的字串，兩邊
# 讀寫的是同一份 Google Sheet 同一欄，任何一邊改了字串另一邊就會對不上。
REVIEW_STATUS_HEADER = "審查狀態"
REVIEW_STATUS_PENDING = "待審查"
REVIEW_STATUS_SELECTED = "已勾選待反查"

_SERVICE_ACCOUNT_HINT = (
    "沒有權限讀取這份 Google Sheet，請把這份試算表分享「檢視者」權限給 "
    "Cloud Run 服務帳戶（tsaipei-505807 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱）。"
)

_SERVICE_ACCOUNT_WRITE_HINT = (
    "沒有權限寫入這份 Google Sheet，請把這份試算表分享「編輯者」權限給 "
    "Cloud Run 服務帳戶（跟唯讀權限分享的是同一個服務帳戶信箱，把權限從"
    "「檢視者」改成「編輯者」即可，不用重新分享一次）。"
)


def _get_sheets_service():
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _get_sheets_service_write():
    """給需要寫入的操作用，scope 是可讀寫的 `spreadsheets`（不是
    `.readonly`）。刻意跟 `_get_sheets_service()` 分開，讓「這支頁面大部分
    功能只讀」這件事在程式碼裡看得出來，只有真的要寫入的呼叫端才會用到
    這個需要「編輯者」權限的版本。"""
    from google.auth import default as google_auth_default
    from googleapiclient.discovery import build

    credentials, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _col_index_to_letter(index: int) -> str:
    """0-indexed 欄位編號轉成 Google Sheets 的欄位字母（0→A、25→Z、26→AA…）。"""
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _normalize_row(row: list, width: int) -> list:
    # Google Sheets API 省略掉每列尾端的空白儲存格，同一列讀回來的長度可能
    # 比表頭短，補空字串讓樣板可以直接用固定欄位數畫表格，不用另外判斷。
    return row + [""] * (width - len(row))


def fetch_sheet_tabs():
    """回傳 (tabs, error)。

    成功時 tabs 是
    [{"title", "headers", "rows", "truncated", "review_status_col_index"}, ...]
    （每個分頁一筆），error 是 None；失敗時 tabs 是空列表，error 是可以直接
    顯示在畫面上的中文說明。刻意把所有例外攔在這裡，不讓例外往外炸出 500
    錯誤頁——這支只是唯讀彙整頁面，讀不到資料時應該顯示清楚的設定提示。

    `rows` 裡每一列是 {"row_number", "cells"}，`row_number` 是這一列在
    Google Sheet 裡實際的列號（從 2 開始，因為第 1 列是表頭）——「勾選要
    反查」功能要靠這個列號才知道使用者勾的是哪一列，需要靠它才能精準寫回
    正確的儲存格，不能只靠畫面上排第幾筆（有搜尋框篩選、跨分頁時排序會
    對不上）。`review_status_col_index` 是這個分頁表頭裡「審查狀態」欄位
    的 0-indexed 位置，這個分頁沒有這個欄位就是 None——畫面靠這個值決定
    要不要在這個分頁顯示勾選框。
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
            tabs.append(
                {
                    "title": title,
                    "headers": [],
                    "rows": [],
                    "truncated": False,
                    "review_status_col_index": None,
                }
            )
            continue
        headers = rows[0]
        data_rows = rows[1:]
        truncated = len(data_rows) > MAX_ROWS_PER_TAB
        data_rows = data_rows[:MAX_ROWS_PER_TAB]
        review_status_col_index = (
            headers.index(REVIEW_STATUS_HEADER) if REVIEW_STATUS_HEADER in headers else None
        )
        numbered_rows = [
            {
                # +2：第 1 列是表頭，data_rows 的第 0 筆對應到試算表第 2 列。
                "row_number": i + 2,
                "cells": _normalize_row(row, len(headers)),
            }
            for i, row in enumerate(data_rows)
        ]
        tabs.append(
            {
                "title": title,
                "headers": headers,
                "rows": numbered_rows,
                "truncated": truncated,
                "review_status_col_index": review_status_col_index,
            }
        )

    return tabs, None


def mark_rows_selected_for_reverse_lookup(tab_title: str, row_numbers: list) -> tuple:
    """把 `tab_title` 這個分頁裡 `row_numbers`（Google Sheet 實際列號）這些
    列的「審查狀態」欄位，從「待審查」改成「已勾選待反查」。

    回傳 (實際更新的列數, error)：error 是 None 代表沒有發生例外（就算
    `row_numbers` 裡有些列已經不是「待審查」而被跳過，也不算 error，實際
    更新的列數會反映在第一個回傳值裡）；error 非 None 是可以直接顯示在
    畫面上的中文說明。

    寫入前會先重新讀一次這些列目前的「審查狀態」，只更新目前真的還是
    「待審查」的列——避免使用者在畫面上停留很久才送出勾選時，這中間
    每日反查排程已經把某幾列從「待審查」處理成別的狀態，結果使用者這次
    送出的舊畫面內容把排程剛寫好的結果又覆蓋回「已勾選待反查」。
    """
    if not row_numbers:
        return 0, None
    if not SALESDEV_SHEET_ID:
        return 0, "尚未設定 SALESDEV_SHEET_ID，請聯絡系統管理員設定要顯示的 Google Sheet。"

    from googleapiclient.errors import HttpError

    try:
        service = _get_sheets_service_write()
        meta = service.spreadsheets().get(spreadsheetId=SALESDEV_SHEET_ID).execute()
        sheet_titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if tab_title not in sheet_titles:
            return 0, f"找不到分頁「{tab_title}」，請重新整理頁面後再試一次。"

        header_result = service.spreadsheets().values().get(
            spreadsheetId=SALESDEV_SHEET_ID, range=f"'{tab_title}'!1:1"
        ).execute()
        header_rows = header_result.get("values", [])
        headers = header_rows[0] if header_rows else []
        if REVIEW_STATUS_HEADER not in headers:
            return 0, f"分頁「{tab_title}」找不到「{REVIEW_STATUS_HEADER}」欄位，無法勾選反查。"
        col_letter = _col_index_to_letter(headers.index(REVIEW_STATUS_HEADER))

        current = service.spreadsheets().values().batchGet(
            spreadsheetId=SALESDEV_SHEET_ID,
            ranges=[f"'{tab_title}'!{col_letter}{n}" for n in row_numbers],
        ).execute()
        rows_still_pending = []
        for row_number, value_range in zip(row_numbers, current.get("valueRanges", [])):
            values = value_range.get("values", [])
            current_value = values[0][0] if values and values[0] else ""
            if current_value == REVIEW_STATUS_PENDING:
                rows_still_pending.append(row_number)

        if not rows_still_pending:
            return 0, None

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SALESDEV_SHEET_ID,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {
                        "range": f"'{tab_title}'!{col_letter}{n}",
                        "values": [[REVIEW_STATUS_SELECTED]],
                    }
                    for n in rows_still_pending
                ],
            },
        ).execute()
        return len(rows_still_pending), None
    except HttpError as e:
        if e.resp.status == 403:
            return 0, _SERVICE_ACCOUNT_WRITE_HINT
        return 0, f"寫回審查狀態時發生錯誤：{e}"
    except Exception as e:
        return 0, f"寫回審查狀態時發生錯誤：{e}"
