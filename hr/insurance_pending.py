"""台北所專區「待進人員」的規則（2026-09-25 新增，見 HANDOFF.md「台北所(派遣組)／台北所(國際組)專區」）。

待進人員就是每日加退保暫存區（`hr/insurance_draft_repository.py`，collection `hr_insurance_drafts`）裡、
這兩個部門提前排好的資料；這裡只放台北所才有的規則，存取一律走 drafts repository：

- **廠商必選、班別可空**，兩個都只能是主管維護的「啟用中」選項（`hr/insurance_options.py`）。
- **身分證必填**（只有配送部例外），存成大寫、去空白。
- 加保／退保／追退日期至少一個。
- **加保日、退保日不同天 → 自動拆成兩列**（一列只有加保、一列只有退保＋追退日期），不然加保那天會把
  還沒到的退保一起送出去。同一天的（當天加退）不拆。
- **重複**：同一個身分證、同一天，已經有加保又登記加保、或已經有退保又登記退保 → 擋下來（已取消的
  不算）。同一次送出的幾列之間也會互相比。
- **多日期**：人員資料填一次，選類型（當天加退／只加保／只退保）＋勾好幾個日期，每個日期一列。
- **Excel 匯入**：跟各所上傳範本同樣 11 欄（`hr/insurance_excel._SOURCE_HEADER`），有問題的列不匯入、
  列出第幾列為什麼。
"""
import datetime

from hr import insurance_draft_repository as drafts
from hr import insurance_options as options

KIND_ZONE = "zone"  # 待進人員（草稿的 kind）

MULTI_TYPES = {
    "both": "當天加退",
    "add": "只加保",
    "remove": "只退保",
}

_TEXT_FIELDS = ("vendor", "shift", "name", "id_number", "health_month", "dependents", "note")
_DATE_FIELDS = ("insured_date", "withdrawn_date", "recovery_date")


def _iso(value) -> str:
    """表單字串或 Excel 儲存格 → YYYY-MM-DD；解析不出來回傳 None（跟「空白」分開，才能報錯）。"""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def clean_fields(raw: dict) -> tuple:
    """回傳 (fields, error)。raw 的 key 用 drafts.FIELD_HEADERS 的 key。"""
    fields = {key: str(raw.get(key) or "").strip() for key in _TEXT_FIELDS}
    fields["id_number"] = fields["id_number"].replace(" ", "").upper()
    for key in _DATE_FIELDS:
        value = _iso(raw.get(key))
        if value is None:
            return fields, f"{drafts.FIELD_HEADERS[key]}格式不正確（請用 2026-09-26 或 2026/9/26）。"
        fields[key] = value
    return fields, ""


def validate(department: str, fields: dict, vendor_names=None, shift_names=None) -> str:
    """欄位規則檢查，回傳錯誤訊息（空字串＝通過）。vendor/shift_names 可以先查好傳進來（匯入時不用每列查）。"""
    vendor_names = options.active_names(department, options.TYPE_VENDOR) if vendor_names is None else vendor_names
    shift_names = options.active_names(department, options.TYPE_SHIFT) if shift_names is None else shift_names
    if not fields.get("name"):
        return "請填姓名。"
    if not fields.get("id_number"):
        return "請填身分證字號。"
    if not fields.get("vendor"):
        return "請選廠商。"
    if fields["vendor"] not in vendor_names:
        return f"廠商「{fields['vendor']}」不在廠商清單裡（或已停用），請從清單選，或請主管到「廠商維護」新增。"
    if fields.get("shift") and fields["shift"] not in shift_names:
        return f"班別「{fields['shift']}」不在班別清單裡（或已停用），請從清單選，或請主管到「班別維護」新增。"
    if not (fields.get("insured_date") or fields.get("withdrawn_date") or fields.get("recovery_date")):
        return "勞保加保日期、退保日期、追退日期至少要填一個。"
    return ""


def split(fields: dict) -> list:
    """加保日、退保日不同天就拆成兩列；追退日期跟著退保那一列。"""
    insured, withdrawn = fields.get("insured_date"), fields.get("withdrawn_date")
    if insured and withdrawn and insured != withdrawn:
        return [
            {**fields, "withdrawn_date": "", "recovery_date": ""},
            {**fields, "insured_date": ""},
        ]
    return [dict(fields)]


def expand_multi(base: dict, multi_type: str, dates: list) -> tuple:
    """多日期：回傳 (entries, error)。每個日期一列，依類型填加保／退保日期。"""
    if multi_type not in MULTI_TYPES:
        return [], "請選類型（當天加退／只加保／只退保）。"
    clean_dates = []
    for value in dates:
        iso = _iso(value)
        if iso is None:
            return [], f"日期「{value}」格式不正確。"
        if iso and iso not in clean_dates:
            clean_dates.append(iso)
    if not clean_dates:
        return [], "請至少選一個日期。"
    entries = []
    for day in sorted(clean_dates):
        entry = {**base, "insured_date": "", "withdrawn_date": "", "recovery_date": ""}
        if multi_type in ("both", "add"):
            entry["insured_date"] = day
        if multi_type in ("both", "remove"):
            entry["withdrawn_date"] = day
        entries.append(entry)
    return entries, ""


def _events(entry: dict) -> set:
    events = set()
    if entry.get("insured_date"):
        events.add(("加保", entry["insured_date"]))
    if entry.get("withdrawn_date"):
        events.add(("退保", entry["withdrawn_date"]))
    return events


def find_duplicates(department: str, entries: list, exclude_ids=(), existing=None) -> list:
    """回傳重複說明（空清單＝沒有重複）。比對部門裡還沒取消的資料，以及這次 entries 彼此之間。"""
    existing = drafts.list_drafts(department) if existing is None else existing
    seen = {}
    for draft in existing:
        if draft.get("status") == drafts.STATUS_CANCELLED or draft.get("id") in exclude_ids:
            continue
        for event in _events(draft):
            seen.setdefault((draft.get("id_number", "").upper(), event), draft.get("name", ""))
    messages = []
    for entry in entries:
        key_id = entry.get("id_number", "").upper()
        for label, day in sorted(_events(entry), key=lambda e: e[1]):
            key = (key_id, (label, day))
            if key in seen:
                messages.append(f"{entry.get('name')}（{key_id}）{day} 已經登記過{label}")
            else:
                seen[key] = entry.get("name", "")
    return messages


def create_entries(department: str, entries: list, actor: dict, note: str) -> list:
    return [drafts.add_draft(department, e, actor, kind=KIND_ZONE, note=note) for e in entries]


def row_to_raw(record: dict) -> dict:
    """`parse_department_workbook()` 的一列（key 是範本表頭）→ drafts 欄位 key。"""
    return {key: record.get(header) for key, header in drafts.FIELD_HEADERS.items()}


def prepare_import(department: str, records: list) -> tuple:
    """Excel 匯入：回傳 (entries, errors)。errors 是 [(Excel 第幾列, 原因)]；有問題的列整列不匯入。"""
    vendor_names = options.active_names(department, options.TYPE_VENDOR)
    shift_names = options.active_names(department, options.TYPE_SHIFT)
    existing = drafts.list_drafts(department)
    entries, errors, accepted = [], [], []
    for index, record in enumerate(records):
        excel_row = index + 2  # 第 1 列是表頭
        fields, error = clean_fields(row_to_raw(record))
        error = error or validate(department, fields, vendor_names, shift_names)
        if error:
            errors.append((excel_row, error))
            continue
        rows = split(fields)
        duplicates = find_duplicates(department, rows, existing=existing + accepted)
        if duplicates:
            errors.append((excel_row, "；".join(duplicates)))
            continue
        accepted.extend({**r, "status": drafts.STATUS_PENDING} for r in rows)
        entries.extend(rows)
    return entries, errors
