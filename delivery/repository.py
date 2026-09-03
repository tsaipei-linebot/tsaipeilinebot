"""人員 / 補款 / 病假登記的資料存取與「缺件狀況」計算邏輯。

缺件判斷刻意寫成不依賴 Firestore 的純函式（missing_documents / doc_status），
方便直接寫單元測試，不需要真的連線 GCP。
"""
import time
from datetime import date, datetime

from delivery.config import DOC_TYPES
from delivery.db import applicants_ref, personnel_ref, repayments_ref, sick_leaves_ref

TODAY_ISO = lambda: date.today().isoformat()  # noqa: E731


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def doc_status(doc_type_code: str, documents: dict) -> dict:
    """回傳單一文件類型的狀態：{"has_file": bool, "expired": bool, "missing": bool, ...}"""
    doc_type = next((d for d in DOC_TYPES if d["code"] == doc_type_code), None)
    entry = (documents or {}).get(doc_type_code) or {}
    has_file = bool(entry.get("file_path"))
    expired = False
    if doc_type and doc_type["has_expiry"]:
        expiry = _parse_date(entry.get("expiry_date"))
        if expiry is not None and expiry < date.today():
            expired = True
    return {
        "code": doc_type_code,
        "name": doc_type["name"] if doc_type else doc_type_code,
        "has_file": has_file,
        "expiry_date": entry.get("expiry_date") or "",
        "expired": expired,
        "missing": (not has_file) or expired,
        "file_path": entry.get("file_path") or "",
    }


def missing_documents(documents: dict) -> list:
    """回傳缺件（沒上傳或已過期）的文件類型清單，供列表頁的「缺件狀況」顯示。"""
    statuses = [doc_status(d["code"], documents) for d in DOC_TYPES]
    return [s for s in statuses if s["missing"]]


def all_document_statuses(documents: dict) -> list:
    return [doc_status(d["code"], documents) for d in DOC_TYPES]


# ==========================================
# 人員 CRUD
# ==========================================
def create_personnel(name: str, id_number: str, phone: str, vendor: str, created_by: str) -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
            "name": name,
            "id_number": id_number,
            "phone": phone,
            "vendor": vendor,
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
    documents[doc_type_code] = entry
    ref.update({"documents": documents, "updated_at": time.time()})


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
def create_applicant(name: str, phone: str, answers: dict) -> str:
    doc_ref = applicants_ref().document()
    doc_ref.set(
        {
            "name": name,
            "phone": phone,
            "answers": answers or {},
            "interviewed": False,
            "hired": False,
            "withdrawn": False,
            "converted_personnel_id": None,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def list_applicants() -> list:
    query = applicants_ref().order_by("created_at", direction="DESCENDING")
    result = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    return result


def get_applicant(applicant_id: str):
    snapshot = applicants_ref().document(applicant_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def update_applicant_status(applicant_id: str, interviewed: bool, withdrawn: bool):
    applicants_ref().document(applicant_id).update({"interviewed": interviewed, "withdrawn": withdrawn})


def mark_applicant_hired(applicant_id: str, personnel_id: str):
    applicants_ref().document(applicant_id).update({"hired": True, "converted_personnel_id": personnel_id})
