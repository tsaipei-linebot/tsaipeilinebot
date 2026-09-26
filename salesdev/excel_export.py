"""/salesdev「下載 Excel」（2026-09-24 新增）：資料改存 Firestore 之後，
同仁要自己整理或交給別人時從這裡下載，取代原本直接開試算表。

一個檔案五個工作表：開發名單（依地點）、全部職缺、104 產線徵才公司、台灣就業通、新登記工廠。
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font

from salesdev.repository import is_internal

# 防 Excel 公式注入：自由文字欄位（備註、聯絡紀錄、職缺標題）如果剛好以這些
# 字元開頭，Excel 打開時會被當成公式執行，前面補單引號變成純文字（跟
# delivery/excel_export.py 同一套做法）。
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")

SOURCE_LABELS = {"104": "104", "1111": "1111", "chickpt": "小雞上工"}

GROUP_HEADER = [
    "狀態", "地點／名稱", "職缺數", "派遣公司", "來源", "最新職缺標題", "第一次出現", "最近出現",
    "要派公司", "電話", "分機", "Email", "查到的來源網址", "反查備註", "備註", "聯絡紀錄",
]
JOB_HEADER = [
    "所屬組", "來源", "派遣公司", "職缺名稱", "工作地址", "刊登者電話", "刊登者分機", "刊登者Email",
    "第一次出現", "最近出現", "非客戶線索", "判斷原因", "職缺連結",
]
HIRING_HEADER = [
    "公司名稱", "統一編號", "產業", "員工人數", "最近一次抓到的職缺數", "職缺例子", "工作地區",
    "第一次出現", "最近出現", "104 公司頁",
]
TJ_HEADER = [
    "公司名稱", "Email", "聯絡人", "電話", "地址", "職缺數", "職缺例子", "地區", "派遣公司",
    "第一次出現", "最近出現",
]
FACTORY_HEADER = ["發現日期", "工廠名稱", "統一編號", "工廠地址", "行業別", "主要產品", "登記核准日期", "工廠登記編號"]


def _sanitize(value):
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def _add_sheet(workbook, title: str, header: list, rows: list, first: bool = False):
    worksheet = workbook.active if first else workbook.create_sheet()
    worksheet.title = title
    worksheet.append(header)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        worksheet.append([_sanitize(v) for v in row])
    worksheet.freeze_panes = "A2"


def group_row(group: dict) -> list:
    logs = "\n".join(f"{log.get('at', '')} {log.get('by', '')}：{log.get('text', '')}" for log in group.get("contact_logs") or [])
    return [
        group.get("review_status", ""),
        group.get("label", ""),
        group.get("job_count", 0),
        "、".join(group.get("agency_names") or []),
        "、".join(SOURCE_LABELS.get(s, s) for s in group.get("sources") or []),
        group.get("sample_title", ""),
        group.get("first_seen", ""),
        group.get("last_seen", ""),
        group.get("client_company", ""),
        group.get("client_phone", ""),
        group.get("client_phone_ext", ""),
        group.get("client_email", ""),
        group.get("client_source_url", ""),
        group.get("lookup_note", ""),
        group.get("note", ""),
        logs,
    ]


def job_row(job: dict, group_labels: dict) -> list:
    internal = is_internal(job)
    return [
        "" if internal else group_labels.get(job.get("group_id", ""), job.get("group_label", "")),
        SOURCE_LABELS.get(job.get("source", ""), job.get("source", "")),
        job.get("company_name", ""),
        job.get("job_title", ""),
        job.get("work_address", ""),
        job.get("poster_phone", ""),
        job.get("poster_phone_ext", ""),
        job.get("poster_email", ""),
        job.get("first_seen", ""),
        job.get("last_seen", ""),
        "是" if internal else "",
        (job.get("internal_reason", "") or "人工標記") if internal else "",
        job.get("job_url", ""),
    ]


def factory_row(factory: dict) -> list:
    return [factory.get(k, "") for k in ("found_date", "name", "tax_id", "address", "industry", "products", "approval_date_raw", "reg_no")]


def hiring_row(company: dict) -> list:
    return [
        company.get("company_name", ""),
        company.get("tax_id", ""),
        company.get("industry", ""),
        company.get("employee_count") if company.get("employee_count") is not None else "",
        company.get("latest_job_count", 0),
        "、".join(company.get("latest_job_titles") or []),
        "、".join(company.get("areas") or []),
        company.get("first_seen", ""),
        company.get("last_seen", ""),
        company.get("company_url", ""),
    ]


def tj_row(company: dict) -> list:
    return [
        company.get("company_name", ""),
        "、".join(company.get("emails") or []),
        "、".join(company.get("contact_names") or []),
        "、".join(company.get("contact_phones") or []),
        "、".join(company.get("addresses") or []),
        company.get("job_count", 0),
        "、".join(company.get("latest_job_titles") or []),
        "、".join(company.get("areas") or []),
        "是" if company.get("is_dispatch") else "",
        company.get("first_seen", ""),
        company.get("last_seen", ""),
    ]


def build_workbook(
    groups: list, jobs: list, factories: list, hiring_companies: list = None, taiwanjobs_companies: list = None
) -> bytes:
    workbook = Workbook()
    group_labels = {g["id"]: g.get("label", "") for g in groups}
    _add_sheet(workbook, "開發名單（依地點）", GROUP_HEADER, [group_row(g) for g in groups], first=True)
    sorted_jobs = sorted(jobs, key=lambda j: (j.get("last_seen", ""), j.get("first_seen", "")), reverse=True)
    _add_sheet(workbook, "全部職缺", JOB_HEADER, [job_row(j, group_labels) for j in sorted_jobs])
    _add_sheet(workbook, "104產線徵才公司", HIRING_HEADER, [hiring_row(c) for c in hiring_companies or []])
    _add_sheet(workbook, "台灣就業通", TJ_HEADER, [tj_row(c) for c in taiwanjobs_companies or []])
    _add_sheet(workbook, "新登記工廠", FACTORY_HEADER, [factory_row(f) for f in factories])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
