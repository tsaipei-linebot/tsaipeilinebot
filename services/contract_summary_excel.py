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

# 防 Excel 公式注入（2026-09-14 新增）：同仁填的客戶名稱、統編、匯款截止日、
# 班別欄位等都是自由文字，如果剛好打了以下開頭的內容，openpyxl 存進 .xlsx
# 時會被標記成公式（data_type="f"），管理員打開匯出的 Excel 時就會被當成
# 可執行的公式跑出來（例如連到釣魚網站的 HYPERLINK，或舊版 Excel/DDE的
# 命令注入）。比照 OWASP 的建議做法：這類字串開頭補一個前導單引號，讓
# openpyxl 存成純文字（data_type="s"），Excel 打開時只會照字面顯示，不會
# 被當成公式執行。
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
    header = ["客戶名稱", "送出人", "最近異動年份", "職稱/班別", "工作時間", "時薪", "工時獎金", "加班", "結薪週期"]
    sheet_rows = [
        [
            r["client_name"],
            r["submitted_by"],
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


def build_merged_summary_workbook(rows: list, max_shifts: int, contract_versions: dict) -> bytes:
    """合併視圖的匯出：左半段是合約產生器總表既有的欄位，右半段是契約側的
    資訊——班別固定切成「班別N-欄位」幾組（組數＝`max_shifts`，見
    `services/contract_summary_service.build_merged_summary_rows()`），
    不是每個班別各自一個工作表分頁，方便同仁一次匯出、一次篩選。"""
    header = [
        "客戶名稱", "統一編號", "合約年", "簽約公司", "合約版本", "報價方式", "匯款截止日", "合約送出人",
        "契約結薪週期", "契約送出人",
    ]
    for i in range(max_shifts):
        header += [f"班別{i + 1}-職稱", f"班別{i + 1}-工作時間", f"班別{i + 1}-時薪", f"班別{i + 1}-工時獎金", f"班別{i + 1}-加班"]

    sheet_rows = []
    for r in rows:
        row = [
            r["client_name"],
            r["tax_id"],
            r["year"],
            r["party_b_name"],
            contract_versions.get(r["contract_version"], {}).get("label", r["contract_version"]),
            r["pricing_summary"],
            r["remit_day"],
            r["submitted_by"],
            r["dispatch_pay_cycle"],
            r["dispatch_submitted_by"],
        ]
        for col in r["shift_columns"]:
            row += [col["title"], col["hours"], col["wage"], col["bonus"], col["overtime"]]
        sheet_rows.append(row)
    return _build_workbook("合約契約合併總表", header, sheet_rows)
