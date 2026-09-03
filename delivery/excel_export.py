"""補款記錄/假別查詢頁面「一鍵下載EXCEL」用的小工具。

刻意只依賴 openpyxl（純 Python、沒有原生編譯依賴），輸出 .xlsx 檔案的 bytes，
呼叫端（routes）直接包成 HTTP response，不落地寫檔案。
"""
import io

from openpyxl import Workbook

from delivery.config import LEAVE_TYPE_MAP, VENDOR_MAP


def _build_workbook(sheet_title: str, header: list, rows: list) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(header)
    for row in rows:
        ws.append(row)
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
    header = ["開始日期", "結束日期", "假別", "廠商", "人員", "原因", "核准狀態"]
    rows = [
        [
            r.get("start_date", ""),
            r.get("end_date", ""),
            LEAVE_TYPE_MAP.get(r.get("leave_type"), r.get("leave_type") or ""),
            VENDOR_MAP.get(r.get("vendor"), r.get("vendor")),
            r.get("personnel_name", ""),
            r.get("reason", ""),
            "已核准" if r.get("approved") else "未核准",
        ]
        for r in records
    ]
    return _build_workbook("假別查詢", header, rows)
