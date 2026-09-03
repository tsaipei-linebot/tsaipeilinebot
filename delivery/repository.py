"""人員 / 補款 / 病假登記的資料存取與「缺件狀況」計算邏輯。

缺件判斷刻意寫成不依賴 Firestore 的純函式（missing_documents / doc_status），
方便直接寫單元測試，不需要真的連線 GCP。
"""
import time
from datetime import date, datetime, timedelta

from delivery.config import DOC_TYPES, SELECTABLE_APPLICANT_STATUSES
from delivery.db import applicants_ref, get_db, personnel_ref, repayments_ref, sick_leaves_ref
from delivery.validators import is_valid_taiwan_id

TODAY_ISO = lambda: date.today().isoformat()  # noqa: E731


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def applicable_doc_types(vendor: str, cooperation_type: str) -> list:
    """依廠商 + 合作方式，篩出這個人實際需要檢查的應備項目清單。
    exclude_vendors 命中就整個排除；cooperation_types 存在但對不上（含合作方式
    根本還沒設定的情況）也排除——所以沒設合作方式的人，強制險等三項不會出現在
    缺件清單裡，等設定好合作方式才會開始追蹤。"""
    result = []
    for doc_type in DOC_TYPES:
        if vendor in (doc_type.get("exclude_vendors") or []):
            continue
        required_coop = doc_type.get("cooperation_types")
        if required_coop is not None and cooperation_type not in required_coop:
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

    if kind == "checkbox":
        checked = bool(entry.get("checked"))
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "checked": checked,
            "missing": not checked,
        }

    # kind == "file_expiry"
    has_file = bool(entry.get("file_path"))
    expired = False
    expiry = _parse_date(entry.get("expiry_date"))
    if expiry is not None and expiry < date.today():
        expired = True
    return {
        "code": code,
        "name": doc_type["name"],
        "kind": kind,
        "has_file": has_file,
        "expiry_date": entry.get("expiry_date") or "",
        "expired": expired,
        "missing": (not has_file) or expired,
        "file_path": entry.get("file_path") or "",
    }


def missing_documents(personnel: dict) -> list:
    """回傳缺件（依廠商+合作方式篩選過的應備項目裡，沒填/沒勾/沒上傳或已過期的）
    清單，供列表頁的「缺件狀況」顯示。"""
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"))
    statuses = [doc_status(dt, personnel) for dt in doc_types]
    return [s for s in statuses if s["missing"]]


def all_document_statuses(personnel: dict) -> list:
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"))
    return [doc_status(dt, personnel) for dt in doc_types]


# ==========================================
# 人員 CRUD
# ==========================================
def create_personnel(name: str, id_number: str, phone: str, vendor: str, created_by: str, cooperation_type: str = "") -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
            "name": name,
            "id_number": id_number,
            "phone": phone,
            "vendor": vendor,
            "cooperation_type": cooperation_type or "",
            "status": "active",
            "documents": {},
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }
    )
    return doc_ref.id


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


def personnel_matches_filters(personnel: dict, missing: list, name_keyword: str = "", phone_keyword: str = "") -> bool:
    """判斷這個人要不要出現在廠商人員清單裡（純函式，missing 需已經算好傳入）。
    預設（沒有搜尋姓名）不顯示缺件狀況「齊全」的人，避免洗版；主動搜尋姓名，
    齊全的人才會被列出來。"""
    if name_keyword and name_keyword not in (personnel.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (personnel.get("phone") or ""):
        return False
    if not missing and not name_keyword:
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


def update_personnel_cooperation_type(personnel_id: str, cooperation_type: str):
    personnel_ref().document(personnel_id).update({"cooperation_type": cooperation_type, "updated_at": time.time()})


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
        doc_types = applicable_doc_types(data.get("vendor"), data.get("cooperation_type"))
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
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def list_recent_repayments(limit: int = 20) -> list:
    query = repayments_ref().order_by("created_at", direction="DESCENDING").limit(limit)
    result = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    return result


# ==========================================
# 病假登記
# ==========================================
def create_sick_leave(personnel_id: str, personnel_name: str, vendor: str, start_date: str, end_date: str, reason: str, receipt_file_path: str, created_by: str) -> str:
    doc_ref = sick_leaves_ref().document()
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "vendor": vendor,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "receipt_file_path": receipt_file_path,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def list_recent_sick_leaves(limit: int = 20) -> list:
    query = sick_leaves_ref().order_by("created_at", direction="DESCENDING").limit(limit)
    result = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    return result


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


def applicant_matches_filters(data: dict, name_keyword: str = "", phone_keyword: str = "", status_filter: str = "") -> bool:
    """判斷這筆應徵資料要不要出現在清單裡（純函式，data 需已經算好 status）。

    預設（沒指定狀態篩選、也沒搜尋姓名）不顯示「放棄」的紀錄，避免洗版；
    只要主動搜尋姓名、或直接篩選狀態為「放棄」，就會顯示，方便事後回頭查。
    """
    if name_keyword and name_keyword not in (data.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (data.get("phone") or ""):
        return False

    status = data.get("status") or normalize_applicant_status(data)
    if status_filter:
        return status == status_filter
    if status == "withdrawn" and not name_keyword:
        return False
    return True


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


def upsert_applicant(name: str, phone: str, answers: dict) -> str:
    """姓名+電話相同視為同一人重複投遞表單：覆蓋既有應徵紀錄的回覆內容，
    並把處理狀態清空回到「未面試」，不會疊加成新的一筆。姓名+電話對不到
    既有紀錄（含兩者缺一的情況）時直接新增一筆。"""
    payload = {
        "name": name,
        "phone": phone,
        "answers": answers or {},
        "status": "not_interviewed",
        "converted_personnel_id": None,
        "created_at": time.time(),
    }
    existing = find_applicant_by_name_and_phone(name, phone)
    if existing:
        applicants_ref().document(existing["id"]).set(payload)
        return existing["id"]

    doc_ref = applicants_ref().document()
    doc_ref.set(payload)
    return doc_ref.id


def list_applicants(name_keyword: str = "", phone_keyword: str = "", status_filter: str = "") -> list:
    name_keyword = (name_keyword or "").strip()
    phone_keyword = (phone_keyword or "").strip()
    status_filter = (status_filter or "").strip()

    result = []
    query = applicants_ref().order_by("created_at", direction="DESCENDING")
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["status"] = normalize_applicant_status(data)
        if applicant_matches_filters(data, name_keyword, phone_keyword, status_filter):
            result.append(data)
    return result


def get_applicant(applicant_id: str):
    snapshot = applicants_ref().document(applicant_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data["status"] = normalize_applicant_status(data)
    return data


def bulk_set_applicant_status(status_by_id: dict) -> None:
    """一次更新多筆應徵紀錄的狀態，配合前端「一鍵更新所選狀態」。只接受
    未面試/已面試/放棄這三種可以手動勾選的狀態——「已錄取」只能透過
    「錄取並建立人員」那個流程設定，不能用這個批次更新繞過去。"""
    batch = get_db().batch()
    has_writes = False
    for applicant_id, status in status_by_id.items():
        if status not in _SELECTABLE_STATUS_CODES:
            continue
        batch.update(applicants_ref().document(applicant_id), {"status": status})
        has_writes = True
    if has_writes:
        batch.commit()


def mark_applicant_hired(applicant_id: str, personnel_id: str):
    applicants_ref().document(applicant_id).update({"status": "hired", "converted_personnel_id": personnel_id})
