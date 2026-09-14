"""補款記錄/假別查詢頁面「一鍵下載EXCEL」用的小工具。

刻意只依賴 openpyxl（純 Python、沒有原生編譯依賴），輸出 .xlsx 檔案的 bytes，
呼叫端（routes）直接包成 HTTP response，不落地寫檔案。
"""
import io

from openpyxl import Workbook

from delivery.config import LEAVE_TYPE_MAP, VENDOR_MAP

# 防 Excel 公式注入（2026-09-14 新增）：人員/原因這些是自由文字欄位，如果
# 剛好打了以下開頭的內容，openpyxl 存進 .xlsx 時會被標記成公式，同仁打開
# 匯出的 Excel 時就會被當成可執行的公式跑出來。比照 OWASP 建議做法，
# 加一個前導單引號讓它變成純文字——跟 services/contract_summary_excel.py
# 是同一套做法，兩邊各自維護一份是因為 delivery/ 子系統跟主系統的匯出
# 工具本來就是分開兩支獨立的小工具，不共用同一個模組。
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _sanitize_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def _build_workbook(sheet_title: str, header: list, rows: list) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(header)
    for row in rows:
        ws.append([_sanitize_cell(value) for value in row])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_repayment_workbook(records: list) -> bytes:
    header = ["日期", "廠商", "人員", "金額", "原因", "核准狀態"]
    rows = [
        [
            r.get("occurred_date", ""),
            VENDOR_MAP.get(r.get("vendor"), r.get("vendor")),
            r.get("personnel_name", ""),
            r.get("amount", 0),
            r.get("reason", ""),
            "已核准" if r.get("approved") else "未核准",
        ]
        for r in records
    ]
    return _build_workbook("補款記錄", header, rows)


def build_sick_leave_workbook(records: list) -> bytes:
    header = ["申請日期", "時數", "假別", "廠商", "人員", "原因", "核准狀態"]
    rows = [
        [
            r.get("leave_date") or f"{r.get('start_date', '')} ~ {r.get('end_date', '')}",
            r.get("hours", ""),
            LEAVE_TYPE_MAP.get(r.get("leave_type"), r.get("leave_type") or ""),
            VENDOR_MAP.get(r.get("vendor"), r.get("vendor")),
            r.get("personnel_name", ""),
            r.get("reason", ""),
            "已核准" if r.get("approved") else "未核准",
        ]
        for r in records
    ]
    return _build_workbook("假別查詢", header, rows)
