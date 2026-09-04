"""人員 / 補款 / 病假登記的資料存取與「缺件狀況」計算邏輯。

缺件判斷刻意寫成不依賴 Firestore 的純函式（missing_documents / doc_status），
方便直接寫單元測試，不需要真的連線 GCP。
"""
import re
import time
from datetime import date, datetime, timedelta

from delivery.config import (
    COOPERATION_TYPE_MAP,
    DEFAULT_INCIDENT_STATUS,
    DEFAULT_PERSONNEL_STATUS,
    DEFAULT_TEST_DRIVE_STATUS,
    DEFAULT_VEHICLE_STATUS,
    DOC_TYPES,
    HIDDEN_PERSONNEL_STATUSES,
    LEGACY_PERSONNEL_STATUS,
    RISK_LEVELS,
    SELECTABLE_APPLICANT_STATUSES,
    TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES,
    TEST_DRIVE_REQUIRED_VENDORS,
    TEST_DRIVE_STATUS_MAP,
    VEHICLE_STATUS_MAP,
    VENDOR_MAP,
)
from delivery.db import (
    applicants_ref,
    get_db,
    incident_events_ref,
    personnel_ref,
    repayments_ref,
    sick_leaves_ref,
    vehicle_events_ref,
    vehicles_ref,
)
from delivery.validators import is_valid_taiwan_id

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TODAY_ISO = lambda: date.today().isoformat()  # noqa: E731


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def applicable_doc_types(vendor: str, cooperation_type: str, client: str = "") -> list:
    """依廠商 + 合作方式 + 負責客戶，篩出這個人實際需要檢查的應備項目清單。
    exclude_vendors 命中就整個排除；include_vendors 存在但對不上就排除（白名單，
    給只有特定廠商才有的項目用，例如 UD 專屬的 UBER系統/MOMO測驗/自拍照）；
    cooperation_types、clients 同理——存在但對不上（含這個維度根本還沒設定的
    情況）也排除，所以沒設合作方式/負責客戶的人，對應的項目不會出現在缺件清單裡，
    等設定好才開始追蹤。"""
    result = []
    for doc_type in DOC_TYPES:
        if vendor in (doc_type.get("exclude_vendors") or []):
            continue
        include_vendors = doc_type.get("include_vendors")
        if include_vendors is not None and vendor not in include_vendors:
            continue
        required_coop = doc_type.get("cooperation_types")
        if required_coop is not None and cooperation_type not in required_coop:
            continue
        required_clients = doc_type.get("clients")
        if required_clients is not None and client not in required_clients:
            continue
        result.append(doc_type)
    return result


def doc_status(doc_type: dict, personnel: dict) -> dict:
    """回傳單一應備項目的狀態。personnel 要是完整的人員資料（不只是 documents
    子物件），因為「身分證」這一項改成直接檢查 id_number 欄位格式合不合法，
    不是看有沒有上傳檔案。"""
    code = doc_type["code"]
    kind = doc_type["kind"]
    documents = personnel.get("documents") or {}
    entry = documents.get(code) or {}

    if kind == "id_number":
        id_number = (personnel.get("id_number") or "").strip()
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "value": id_number,
            "missing": not is_valid_taiwan_id(id_number),
        }

    if kind == "email":
        email = (personnel.get("email") or "").strip()
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "value": email,
            "missing": not bool(_EMAIL_PATTERN.match(email)),
        }

    if kind == "checkbox":
        checked = bool(entry.get("checked"))
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "checked": checked,
            "missing": not checked,
        }

    if kind == "file":
        has_file = bool(entry.get("file_path"))
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "has_file": has_file,
            "missing": not has_file,
            "file_path": entry.get("file_path") or "",
        }

    # kind == "file_expiry"
    has_file = bool(entry.get("file_path"))
    expired = False
    expiry = _parse_date(entry.get("expiry_date"))
    if expiry is not None and expiry < date.today():
        expired = True
    required = doc_type.get("required", True)
    return {
        "code": code,
        "name": doc_type["name"],
        "kind": kind,
        "has_file": has_file,
        "expiry_date": entry.get("expiry_date") or "",
        "expired": expired,
        "required": required,
        # 非必填的項目沒交不算缺件，但只要交了、過期了一樣算缺件要處理。
        "missing": expired or (required and not has_file),
        "file_path": entry.get("file_path") or "",
    }


def missing_documents(personnel: dict) -> list:
    """回傳缺件（依廠商+合作方式+負責客戶篩選過的應備項目裡，沒填/沒勾/沒上傳
    或已過期的）清單，供列表頁的「缺件狀況」顯示。"""
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"), personnel.get("client"))
    statuses = [doc_status(dt, personnel) for dt in doc_types]
    return [s for s in statuses if s["missing"]]


def all_document_statuses(personnel: dict) -> list:
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"), personnel.get("client"))
    return [doc_status(dt, personnel) for dt in doc_types]


# ==========================================
# 人員 CRUD
# ==========================================
def create_personnel(
    name: str,
    id_number: str,
    phone: str,
    vendor: str,
    created_by: str,
    cooperation_type: str = "",
    client: str = "",
    employment_status: str = "",
) -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
            "name": name,
            "id_number": id_number,
            "phone": phone,
            "vendor": vendor,
            "cooperation_type": cooperation_type or "",
            "client": client or "",
            "employment_status": employment_status or DEFAULT_PERSONNEL_STATUS,
            "status": "active",
            "documents": {},
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }
    )
    return doc_ref.id


def personnel_employment_status(personnel: dict) -> str:
    """回傳人員的報到/在職狀態代碼。這個功能上線前就存在的舊資料沒有
    employment_status 欄位，當作「在職」，不會被誤判成剛建立、還沒報到。"""
    return personnel.get("employment_status") or LEGACY_PERSONNEL_STATUS


def update_personnel_employment_status(personnel_id: str, employment_status: str):
    personnel_ref().document(personnel_id).update(
        {"employment_status": employment_status, "updated_at": time.time()}
    )


def get_personnel(personnel_id: str):
    snapshot = personnel_ref().document(personnel_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_personnel_by_vendor(vendor: str) -> list:
    query = personnel_ref().where("vendor", "==", vendor).where("status", "==", "active")
    result = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda p: p.get("name", ""))
    return result


def personnel_matches_filters(
    personnel: dict,
    missing: list,
    name_keyword: str = "",
    phone_keyword: str = "",
    status_filter: str = "",
    missing_filter: str = "",
) -> bool:
    """判斷這個人要不要出現在廠商人員清單裡（純函式，missing 需已經算好傳入）。

    人員狀態：預設（沒有明確篩選狀態）不顯示「離職」「放棄報到」的人，跟應徵
    名單「放棄」預設隱藏一樣；主動搜尋姓名、或直接篩選狀態為這兩項才會顯示。
    缺件狀態：預設（沒有明確篩選、也沒搜尋姓名）不顯示缺件狀況「齊全」的人，
    避免洗版；主動搜尋姓名，或直接篩選「缺件」「無缺件」都可以覆蓋這個預設。
    """
    if name_keyword and name_keyword not in (personnel.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (personnel.get("phone") or ""):
        return False

    employment_status = personnel_employment_status(personnel)
    if status_filter:
        if employment_status != status_filter:
            return False
    elif employment_status in HIDDEN_PERSONNEL_STATUSES and not name_keyword:
        return False

    if missing_filter == "missing":
        if not missing:
            return False
    elif missing_filter == "complete":
        if missing:
            return False
    elif not missing and not name_keyword:
        return False

    return True


def search_personnel(keyword: str) -> list:
    """簡易查詢：抓全部在職人員後在應用程式端比對姓名/身分證字號（人數規模小，
    不需要為此另外接全文檢索服務）。"""
    keyword = (keyword or "").strip()
    result = []
    for snapshot in personnel_ref().where("status", "==", "active").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if not keyword or keyword in data.get("name", "") or keyword in data.get("id_number", ""):
            result.append(data)
    result.sort(key=lambda p: p.get("name", ""))
    return result


def find_active_personnel_by_name_and_phone(name: str, phone: str):
    """批次匯入用：同一個「姓名+手機號碼」組合已經有在職人員資料時回傳該筆，
    讓呼叫端可以跳過重複匯入，而不是每次匯入都建出重複的人員記錄。
    兩個欄位都要有值才會查（單靠姓名或單靠電話都不足以判定是同一人）。"""
    if not name or not phone:
        return None
    query = (
        personnel_ref()
        .where("name", "==", name)
        .where("phone", "==", phone)
        .where("status", "==", "active")
        .limit(1)
    )
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data
    return None


def update_personnel_document(personnel_id: str, doc_type_code: str, file_path: str = None, expiry_date: str = None):
    """用於 kind="file_expiry" 的項目（強制險/公會加保證明/營業用第三責任險/
    良民證）。expiry_date 有變動時順便清掉 last_reminded_at，讓到期提醒的
    「最近提醒過」判斷用新的到期日重新算，不會因為舊到期日剛提醒過就把新到期日
    的提醒也跳過。"""
    ref = personnel_ref().document(personnel_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return
    data = snapshot.to_dict() or {}
    documents = data.get("documents") or {}
    entry = dict(documents.get(doc_type_code) or {})
    if file_path is not None:
        entry["file_path"] = file_path
    if expiry_date is not None:
        entry["expiry_date"] = expiry_date
        entry.pop("last_reminded_at", None)
    documents[doc_type_code] = entry
    ref.update({"documents": documents, "updated_at": time.time()})


def update_personnel_checkbox(personnel_id: str, doc_type_code: str, checked: bool):
    """用於 kind="checkbox" 的項目（駕照、合約簽定）。"""
    ref = personnel_ref().document(personnel_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return
    data = snapshot.to_dict() or {}
    documents = data.get("documents") or {}
    entry = dict(documents.get(doc_type_code) or {})
    entry["checked"] = checked
    documents[doc_type_code] = entry
    ref.update({"documents": documents, "updated_at": time.time()})


def update_personnel_id_number(personnel_id: str, id_number: str):
    """用於 kind="id_number" 的項目（身分證）。格式驗證交給呼叫端
    （validators.is_valid_taiwan_id）先擋一次，這裡單純負責寫入。"""
    personnel_ref().document(personnel_id).update({"id_number": id_number, "updated_at": time.time()})


def update_personnel_email(personnel_id: str, email: str):
    """用於 kind="email" 的項目。格式檢查交給呼叫端／doc_status，這裡單純負責寫入。"""
    personnel_ref().document(personnel_id).update({"email": email, "updated_at": time.time()})


def update_personnel_cooperation_type(personnel_id: str, cooperation_type: str):
    personnel_ref().document(personnel_id).update({"cooperation_type": cooperation_type, "updated_at": time.time()})


def update_personnel_client(personnel_id: str, client: str):
    personnel_ref().document(personnel_id).update({"client": client, "updated_at": time.time()})


def list_expiring_documents(days_ahead: int, resend_interval_days: int) -> list:
    """掃過全部在職人員，回傳需要發到期提醒的 (人員, 文件) 配對：到期日在
    「今天~今天+days_ahead 天」之間、或已經過期，而且沒有在最近
    resend_interval_days 天內提醒過。只掃 kind="file_expiry" 的項目（強制險/
    公會加保證明/營業用第三責任險/良民證），身分證、駕照、合約簽定沒有到期日
    不適用。"""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = []
    for snapshot in personnel_ref().where("status", "==", "active").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        documents = data.get("documents") or {}
        doc_types = applicable_doc_types(data.get("vendor"), data.get("cooperation_type"), data.get("client"))
        for doc_type in doc_types:
            if doc_type["kind"] != "file_expiry":
                continue
            entry = documents.get(doc_type["code"]) or {}
            expiry = _parse_date(entry.get("expiry_date"))
            if expiry is None or expiry > cutoff:
                continue
            last_reminded = _parse_date(entry.get("last_reminded_at"))
            if last_reminded is not None and (today - last_reminded).days < resend_interval_days:
                continue
            result.append(
                {
                    "personnel_id": data["id"],
                    "personnel_name": data.get("name"),
                    "vendor": data.get("vendor"),
                    "doc_code": doc_type["code"],
                    "doc_name": doc_type["name"],
                    "expiry_date": entry.get("expiry_date"),
                    "expired": expiry < today,
                }
            )
    return result


def mark_documents_reminded(items: list):
    """items 是 list_expiring_documents() 回傳的那種 dict，LINE 推播成功後呼叫，
    記錄提醒時間，避免同一份文件短時間內被重複提醒。"""
    today_iso = date.today().isoformat()
    by_personnel = {}
    for item in items:
        by_personnel.setdefault(item["personnel_id"], []).append(item["doc_code"])

    batch = get_db().batch()
    for personnel_id, doc_codes in by_personnel.items():
        snapshot = personnel_ref().document(personnel_id).get()
        if not snapshot.exists:
            continue
        data = snapshot.to_dict() or {}
        documents = data.get("documents") or {}
        for doc_code in doc_codes:
            entry = dict(documents.get(doc_code) or {})
            entry["last_reminded_at"] = today_iso
            documents[doc_code] = entry
        batch.update(personnel_ref().document(personnel_id), {"documents": documents})
    batch.commit()


# ==========================================
# 補款登記
# ==========================================
def create_repayment(personnel_id: str, personnel_name: str, vendor: str, amount: float, reason: str, occurred_date: str, created_by: str) -> str:
    doc_ref = repayments_ref().document()
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "vendor": vendor,
            "amount": amount,
            "reason": reason,
            "occurred_date": occurred_date,
            "approved": False,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def repayment_matches_filters(record: dict, name_keyword: str = "", vendor_filter: str = "", month_filter: str = "") -> bool:
    """判斷這筆補款登記要不要出現在「補款記錄」清單裡（純函式）。month_filter
    是 "YYYY-MM" 格式（對應 <input type="month">），比對 occurred_date 開頭。"""
    if name_keyword and name_keyword not in (record.get("personnel_name") or ""):
        return False
    if vendor_filter and record.get("vendor") != vendor_filter:
        return False
    if month_filter and not (record.get("occurred_date") or "").startswith(month_filter):
        return False
    return True


def list_repayments(name_keyword: str = "", vendor_filter: str = "", month_filter: str = "") -> list:
    name_keyword = (name_keyword or "").strip()
    vendor_filter = (vendor_filter or "").strip()
    month_filter = (month_filter or "").strip()

    result = []
    for snapshot in repayments_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["approved"] = bool(data.get("approved"))
        if repayment_matches_filters(data, name_keyword, vendor_filter, month_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("occurred_date", ""), reverse=True)
    return result


def bulk_approve_repayments(repayment_ids: list) -> None:
    """把指定的補款登記標記為已核准。核准是單向的——這裡只會把 approved 設成
    True，沒有讓它變回 False 的路徑；已經核准過的重複送出沒有副作用。"""
    if not repayment_ids:
        return
    batch = get_db().batch()
    for repayment_id in repayment_ids:
        batch.update(repayments_ref().document(repayment_id), {"approved": True})
    batch.commit()


# ==========================================
# 假別登記
# ==========================================
def create_sick_leave(
    personnel_id: str,
    personnel_name: str,
    vendor: str,
    start_date: str,
    end_date: str,
    reason: str,
    receipt_file_path: str,
    created_by: str,
    leave_type: str = "",
) -> str:
    doc_ref = sick_leaves_ref().document()
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "vendor": vendor,
            "leave_type": leave_type or "",
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "receipt_file_path": receipt_file_path,
            "approved": False,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def sick_leave_matches_filters(
    record: dict,
    name_keyword: str = "",
    vendor_filter: str = "",
    month_filter: str = "",
    leave_type_filter: str = "",
) -> bool:
    """判斷這筆假別登記要不要出現在「假別查詢」清單裡（純函式）。month_filter
    是 "YYYY-MM" 格式，比對 start_date（請假開始日期）開頭。"""
    if name_keyword and name_keyword not in (record.get("personnel_name") or ""):
        return False
    if vendor_filter and record.get("vendor") != vendor_filter:
        return False
    if month_filter and not (record.get("start_date") or "").startswith(month_filter):
        return False
    if leave_type_filter and record.get("leave_type") != leave_type_filter:
        return False
    return True


def list_sick_leaves(
    name_keyword: str = "", vendor_filter: str = "", month_filter: str = "", leave_type_filter: str = ""
) -> list:
    name_keyword = (name_keyword or "").strip()
    vendor_filter = (vendor_filter or "").strip()
    month_filter = (month_filter or "").strip()
    leave_type_filter = (leave_type_filter or "").strip()

    result = []
    for snapshot in sick_leaves_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["approved"] = bool(data.get("approved"))
        if sick_leave_matches_filters(data, name_keyword, vendor_filter, month_filter, leave_type_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("start_date", ""), reverse=True)
    return result


def bulk_approve_sick_leaves(sick_leave_ids: list) -> None:
    """把指定的假別登記標記為已核准，一樣是單向的（見 bulk_approve_repayments）。"""
    if not sick_leave_ids:
        return
    batch = get_db().batch()
    for sick_leave_id in sick_leave_ids:
        batch.update(sick_leaves_ref().document(sick_leave_id), {"approved": True})
    batch.commit()


# ==========================================
# 應徵名單（Google 表單 webhook 寫入，錄取後轉正式人員）
# ==========================================
_SELECTABLE_STATUS_CODES = {s["code"] for s in SELECTABLE_APPLICANT_STATUSES}


def normalize_applicant_status(data: dict) -> str:
    """新資料一律直接存 status 欄位；這裡額外相容改版前只有
    interviewed/hired/withdrawn 三個布林欄位的舊資料，讓舊紀錄不用手動搬移
    也能正確顯示狀態。"""
    status = data.get("status")
    if status:
        return status
    if data.get("hired"):
        return "hired"
    if data.get("withdrawn"):
        return "withdrawn"
    if data.get("interviewed"):
        return "interviewed"
    return "not_interviewed"


def applicant_matches_filters(
    data: dict,
    name_keyword: str = "",
    phone_keyword: str = "",
    status_filter: str = "",
    vendor_filter: str = "",
) -> bool:
    """判斷這筆應徵資料要不要出現在清單裡（純函式，data 需已經算好 status）。

    預設（沒指定狀態篩選、也沒搜尋姓名）不顯示「放棄」的紀錄，避免洗版；
    只要主動搜尋姓名、或直接篩選狀態為「放棄」，就會顯示，方便事後回頭查。
    廠商正常顯示，不特別隱藏「未指定廠商」的紀錄。
    """
    if name_keyword and name_keyword not in (data.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (data.get("phone") or ""):
        return False
    if vendor_filter and (data.get("vendor") or "") != vendor_filter:
        return False

    status = data.get("status") or normalize_applicant_status(data)
    if status_filter:
        return status == status_filter
    if status == "withdrawn" and not name_keyword:
        return False
    return True


def applicant_needs_test_drive(vendor: str, cooperation_type: str) -> bool:
    """判斷這個應徵者需不需要試駕：UD、UC 一律需要；蝦皮只有合作方式是
    「三輪雇傭」才需要（二輪承攬/二輪雇傭不用）；順豐不需要。"""
    if vendor in TEST_DRIVE_REQUIRED_VENDORS:
        return True
    if vendor == "shopee" and cooperation_type in TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES:
        return True
    return False


def find_applicant_by_name_and_phone(name: str, phone: str):
    """兩者都要有值才會查（單靠姓名或單靠電話都不足以判定是同一人）。"""
    if not name or not phone:
        return None
    query = applicants_ref().where("name", "==", name).where("phone", "==", phone).limit(1)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data
    return None


def upsert_applicant(name: str, phone: str, answers: dict, vendor: str = "", cooperation_type: str = "") -> str:
    """姓名+電話相同視為同一人重複投遞表單：覆蓋既有應徵紀錄的回覆內容（含
    廠商、合作方式），並把處理狀態清空回到「未面試」，不會疊加成新的一筆。
    姓名+電話對不到既有紀錄（含兩者缺一的情況）時直接新增一筆。

    試駕狀態刻意不隨表單重投而重置——那是同仁自己操作/記錄的結果，不是表單
    填寫的內容，重複投遞表單不該把已經記錄的試駕結果洗掉。"""
    existing = find_applicant_by_name_and_phone(name, phone)
    payload = {
        "name": name,
        "phone": phone,
        "answers": answers or {},
        "vendor": vendor or "",
        "cooperation_type": cooperation_type or "",
        "test_drive": (existing or {}).get("test_drive") or DEFAULT_TEST_DRIVE_STATUS,
        "status": "not_interviewed",
        "converted_personnel_id": None,
        "created_at": time.time(),
    }
    if existing:
        applicants_ref().document(existing["id"]).set(payload)
        return existing["id"]

    doc_ref = applicants_ref().document()
    doc_ref.set(payload)
    return doc_ref.id


def _normalize_applicant(data: dict) -> dict:
    data["status"] = normalize_applicant_status(data)
    data["vendor"] = data.get("vendor") or ""
    data["cooperation_type"] = data.get("cooperation_type") or ""
    data["test_drive"] = data.get("test_drive") or DEFAULT_TEST_DRIVE_STATUS
    return data


def list_applicants(
    name_keyword: str = "", phone_keyword: str = "", status_filter: str = "", vendor_filter: str = ""
) -> list:
    name_keyword = (name_keyword or "").strip()
    phone_keyword = (phone_keyword or "").strip()
    status_filter = (status_filter or "").strip()
    vendor_filter = (vendor_filter or "").strip()

    result = []
    query = applicants_ref().order_by("created_at", direction="DESCENDING")
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data = _normalize_applicant(data)
        if applicant_matches_filters(data, name_keyword, phone_keyword, status_filter, vendor_filter):
            result.append(data)
    return result


def get_applicant(applicant_id: str):
    snapshot = applicants_ref().document(applicant_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return _normalize_applicant(data)


def bulk_update_applicants(updates: dict) -> None:
    """一次更新多筆應徵紀錄的狀態/廠商/合作方式/試駕，配合前端「一鍵全部
    更新」。每個欄位獨立驗證，只有合法值才會真的寫入；「已錄取」狀態一樣
    不能透過這裡設定，只能透過「錄取並建立人員」那個流程。

    updates 格式：{applicant_id: {"status": ..., "vendor": ..., "cooperation_type": ..., "test_drive": ...}}，
    每個 applicant 底下的欄位都可以缺，缺的就不動。"""
    batch = get_db().batch()
    has_writes = False
    for applicant_id, fields in updates.items():
        patch = {}
        status = fields.get("status")
        if status is not None and status in _SELECTABLE_STATUS_CODES:
            patch["status"] = status
        vendor = fields.get("vendor")
        if vendor is not None and (vendor == "" or vendor in VENDOR_MAP):
            patch["vendor"] = vendor
        cooperation_type = fields.get("cooperation_type")
        if cooperation_type is not None and (cooperation_type == "" or cooperation_type in COOPERATION_TYPE_MAP):
            patch["cooperation_type"] = cooperation_type
        test_drive = fields.get("test_drive")
        if test_drive is not None and test_drive in TEST_DRIVE_STATUS_MAP:
            patch["test_drive"] = test_drive
        if patch:
            batch.update(applicants_ref().document(applicant_id), patch)
            has_writes = True
    if has_writes:
        batch.commit()


def mark_applicant_hired(applicant_id: str, personnel_id: str):
    applicants_ref().document(applicant_id).update({"status": "hired", "converted_personnel_id": personnel_id})


# ==========================================
# 車輛管理（LINE 群組回報領車/還車 + 網頁手動管理）
# ==========================================
def _normalize_vehicle_no(value: str) -> str:
    """車號統一轉大寫＋去頭尾空白再當 Firestore 文件 ID：LINE 回報時同仁常常
    不會特別注意大小寫（例如把 ERV-2360 打成 erv-2360），沒有這層正規化的話
    會查不到明明已經存在的車輛、回覆誤導性的「查不到這台車」。"""
    return (value or "").strip().upper()


def create_vehicle(vehicle_no: str, vendor: str, created_by: str) -> bool:
    """新增車輛，車號當文件 ID、全公司唯一。已經存在就回傳 False、不會覆蓋
    既有資料；成功新增回傳 True。"""
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if ref.get().exists:
        return False
    ref.set(
        {
            "vehicle_no": vehicle_no,
            "vendor": vendor,
            "status": DEFAULT_VEHICLE_STATUS,
            "current_holder": "",
            "current_location": "",
            "last_event_at": None,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return True


def get_vehicle(vehicle_no: str):
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    snapshot = vehicles_ref().document(vehicle_no).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["vehicle_no"] = snapshot.id
    return data


def vehicle_matches_filters(
    vehicle: dict, vendor_filter: str = "", status_filter: str = "", vehicle_no_filter: str = ""
) -> bool:
    """判斷這台車要不要出現在車輛清單裡（純函式）。"""
    if vendor_filter and vehicle.get("vendor") != vendor_filter:
        return False
    if status_filter and vehicle.get("status") != status_filter:
        return False
    if vehicle_no_filter and vehicle_no_filter.upper() not in (vehicle.get("vehicle_no") or "").upper():
        return False
    return True


def list_vehicles(vendor_filter: str = "", status_filter: str = "", vehicle_no_filter: str = "") -> list:
    vendor_filter = (vendor_filter or "").strip()
    status_filter = (status_filter or "").strip()
    vehicle_no_filter = (vehicle_no_filter or "").strip()

    result = []
    for snapshot in vehicles_ref().stream():
        data = snapshot.to_dict() or {}
        data["vehicle_no"] = snapshot.id
        if vehicle_matches_filters(data, vendor_filter, status_filter, vehicle_no_filter):
            result.append(data)
    result.sort(key=lambda v: v.get("vehicle_no", ""))
    return result


def list_vehicle_events(vehicle_no: str) -> list:
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    result = []
    for snapshot in vehicle_events_ref().where("vehicle_no", "==", vehicle_no).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return result


def set_vehicle_status(vehicle_no: str, status: str) -> bool:
    """網頁上手動調整車輛狀態用（例如標記/解除待維修）。只接受合法的狀態
    代碼，車輛不存在或狀態不合法都回傳 False、不會寫入。"""
    if status not in VEHICLE_STATUS_MAP:
        return False
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if not ref.get().exists:
        return False
    ref.update({"status": status})
    return True


def vehicle_event_error(vehicle, vendor: str, event_type: str) -> str:
    """判斷一筆領車/還車事件套用到這台車目前的狀態合不合理（純函式，vehicle
    需已經查好、找不到就傳 None）。回傳空字串代表可以記錄；非空字串是擋下的
    錯誤代碼：
    - "vehicle_not_found"：車號不存在，要先在網頁新增這台車。
    - "vendor_mismatch"：回報的廠商跟這台車登記的廠商不一樣。
    - "not_available"：領車時車輛目前是使用中或待維修，不能再派車。
    - "not_in_use"：還車時車輛目前不是使用中，沒有領用中的紀錄可以還。
    """
    if vehicle is None:
        return "vehicle_not_found"
    if vehicle.get("vendor") != vendor:
        return "vendor_mismatch"
    status = vehicle.get("status", DEFAULT_VEHICLE_STATUS)
    if event_type == "checkout" and status in ("in_use", "maintenance"):
        return "not_available"
    if event_type == "return" and status != "in_use":
        return "not_in_use"
    return ""


def record_vehicle_event(
    vehicle_no: str,
    vendor: str,
    personnel_name: str,
    event_type: str,
    event_date: str,
    location: str,
    source: str,
    reported_by: str = "",
) -> tuple:
    """驗證通過（見 vehicle_event_error）才會真的寫入事件紀錄、同步更新車輛
    主檔的狀態/使用人/地點。回傳 (True, "") 代表成功；(False, 錯誤代碼) 代表
    被擋下，呼叫端可以把錯誤代碼轉成對應的訊息（LINE 回覆或網頁錯誤提示）。
    source 是 "line" 或 "manual"，用來區分這筆事件是 LINE 群組回報還是網頁
    手動補登的。"""
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    vehicle = get_vehicle(vehicle_no)
    error = vehicle_event_error(vehicle, vendor, event_type)
    if error:
        return False, error

    now = time.time()
    vehicle_events_ref().document().set(
        {
            "vehicle_no": vehicle_no,
            "vendor": vendor,
            "personnel_name": personnel_name,
            "event_type": event_type,
            "event_date": event_date,
            "location": location,
            "source": source,
            "reported_by": reported_by,
            "created_at": now,
        }
    )

    new_status = "in_use" if event_type == "checkout" else "available"
    vehicles_ref().document(vehicle_no).update(
        {
            "status": new_status,
            "current_holder": personnel_name if event_type == "checkout" else "",
            "current_location": location,
            "last_event_at": now,
        }
    )
    return True, ""


# ==========================================
# 意外事件回報
# 跟車輛回報同一個 LINE 群組，但資料完全獨立的一份 collection。風險等級
# （risk_level）跟結案狀態（status）都不是回報當下填的，是管理員事後在
# 網頁上評估／操作，所以新增時一律是空風險等級 + 未結案。
# ==========================================
_INCIDENT_FIELDS = (
    "vendor",
    "identity_type",
    "personnel_name",
    "occurred_at",
    "location",
    "duty_status",
    "police_called",
    "injury",
    "family_contacted",
    "third_party_involved",
    "description",
)


def create_incident_event(data: dict) -> str:
    """新增一筆意外事件回報，回傳新文件 ID。data 需含 _INCIDENT_FIELDS 這
    11 個欄位（見 delivery.incident_report.parse_incident_report 的回傳
    值），風險等級／結案狀態一律用預設值，不接受呼叫端指定。"""
    ref = incident_events_ref().document()
    payload = {key: data.get(key, "") for key in _INCIDENT_FIELDS}
    payload["risk_level"] = ""
    payload["status"] = DEFAULT_INCIDENT_STATUS
    payload["created_at"] = time.time()
    ref.set(payload)
    return ref.id


def get_incident_event(incident_id: str):
    snapshot = incident_events_ref().document(incident_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def incident_matches_filters(
    incident: dict,
    vendor_filter: str = "",
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> bool:
    """判斷這筆意外事件要不要出現在清單裡（純函式）。"""
    if vendor_filter and incident.get("vendor") != vendor_filter:
        return False
    if status_filter and incident.get("status") != status_filter:
        return False
    if risk_level_filter and incident.get("risk_level") != risk_level_filter:
        return False
    if personnel_name_filter and personnel_name_filter not in (incident.get("personnel_name") or ""):
        return False
    return True


def list_incident_events(
    vendor_filter: str = "",
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> list:
    vendor_filter = (vendor_filter or "").strip()
    status_filter = (status_filter or "").strip()
    risk_level_filter = (risk_level_filter or "").strip()
    personnel_name_filter = (personnel_name_filter or "").strip()

    result = []
    for snapshot in incident_events_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if incident_matches_filters(data, vendor_filter, status_filter, risk_level_filter, personnel_name_filter):
            result.append(data)
    result.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    return result


def list_open_incident_events() -> list:
    """未結案案件清單，給每週一群組提醒跟系統登入提醒用。"""
    result = []
    for snapshot in incident_events_ref().where("status", "==", "open").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    return result


def set_incident_risk_level(incident_id: str, risk_level: str) -> bool:
    """管理員在詳細頁設定風險等級，只接受合法的等級代碼。"""
    if risk_level not in RISK_LEVELS:
        return False
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.update({"risk_level": risk_level})
    return True


def close_incident_event(incident_id: str) -> bool:
    """標記結案，單向操作（跟補款/假別核准一樣，沒有重新打開的路徑，如果
    真的填錯，可請管理員直接調整資料）。"""
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.update({"status": "closed"})
    return True
