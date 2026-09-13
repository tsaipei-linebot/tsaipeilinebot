"""總表功能（/contract-summary）「匯出 EXCEL」用的小工具，跟
`delivery/excel_export.py` 是同樣的做法：只依賴 openpyxl，輸出 .xlsx
檔案的 bytes，呼叫端（`contract_summary_routes.py`）直接包成 HTTP
response，不落地寫檔案。

版本代碼（`contract_version`）轉成中文顯示名稱要用
`services.client_contract_service.CONTRACT_VERSIONS`，這裡不重複定義一份，
呼叫端把 `CONTRACT_VERSIONS` 傳進來即可，避免兩邊各存一份版本清單、以後
新增版本時漏改其中一邊。
"""
import io

from openpyxl import Workbook


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


def build_client_contract_summary_workbook(rows: list, contract_versions: dict) -> bytes:
    header = ["客戶名稱", "統一編號", "合約年", "簽約公司", "合約版本", "報價方式", "匯款截止日", "送出人"]
    sheet_rows = [
        [
            r["client_name"],
            r["tax_id"],
            r["year"],
            r["party_b_name"],
            contract_versions.get(r["contract_version"], {}).get("label", r["contract_version"]),
            r["pricing_summary"],
            r["remit_day"],
            r["submitted_by"],
        ]
        for r in rows
    ]
    return _build_workbook("合約產生器總表", header, sheet_rows)


def build_dispatch_contract_summary_workbook(rows: list) -> bytes:
    header = ["客戶名稱", "最近異動年份", "職稱/班別", "工作時間", "時薪", "工時獎金", "加班", "結薪週期"]
    sheet_rows = [
        [
            r["client_name"],
            r["updated_year"] or "",
            r["title"],
            r["hours"],
            r["wage"],
            r["bonus"],
            r["overtime"],
            r["pay_cycle"],
        ]
        for r in rows
    ]
    return _build_workbook("派遣契約總表", header, sheet_rows)
