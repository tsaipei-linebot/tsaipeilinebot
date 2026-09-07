"""人資專區的資料存取層：意外通報、員工體檢報告、員工關懷彙整、公司證照
彙整、教育訓練彙整，各自獨立的 CRUD，刻意不做成一個共用的泛型 CRUD 函式
——欄位跟業務規則各自不同，分開寫更直觀，之後各自演變也不會互相牽扯（跟
management/repository.py 的作法一致，見該檔案開頭的說明）。
"""
import time
from datetime import date, datetime

from hr.config import RISK_LEVELS
from hr.db import (
    care_logs_ref,
    health_checks_ref,
    incident_events_ref,
    licenses_ref,
    trainings_ref,
)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# ==========================================
# 意外通報
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


def _find_incident_event_by_key(personnel_name: str, occurred_at: str):
    """依「人員名稱＋發生時間」找既有的意外事件回報，找不到回傳 None。跟
    配送部意外事件回報同一套識別鍵（見 create_incident_event() 的說明）。"""
    if not personnel_name or not occurred_at:
        return None
    query = (
        incident_events_ref()
        .where("personnel_name", "==", personnel_name)
        .where("occurred_at", "==", occurred_at)
        .limit(1)
    )
    for snapshot in query.stream():
        return snapshot.id
    return None


def create_incident_event(data: dict) -> tuple:
    """新增一筆意外事件回報，回傳 (incident_id, created)：「人員名稱＋發生
    時間」跟既有紀錄相同時視為同一起事件的更新，覆寫既有那筆的回報內容，
    不新增一筆重複紀錄，created 回傳 False；找不到既有紀錄才真的新建一筆，
    風險等級／結案狀態用預設值，created 回傳 True。刻意不覆寫既有紀錄的
    risk_level／status／created_at，避免同仁重傳同一起事件的內容更新，把
    管理員已經做的風險評估／結案狀態洗掉。"""
    payload = {key: data.get(key, "") for key in _INCIDENT_FIELDS}

    existing_id = _find_incident_event_by_key(data.get("personnel_name", ""), data.get("occurred_at", ""))
    if existing_id:
        incident_events_ref().document(existing_id).update(payload)
        return existing_id, False

    ref = incident_events_ref().document()
    payload["risk_level"] = ""
    payload["status"] = "open"
    payload["created_at"] = time.time()
    ref.set(payload)
    return ref.id, True


def get_incident_event(incident_id: str):
    snapshot = incident_events_ref().document(incident_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def incident_matches_filters(
    incident: dict,
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> bool:
    """判斷這筆意外事件要不要出現在清單裡（純函式）。"""
    if status_filter and incident.get("status") != status_filter:
        return False
    if risk_level_filter and incident.get("risk_level") != risk_level_filter:
        return False
    if personnel_name_filter and personnel_name_filter not in (incident.get("personnel_name") or ""):
        return False
    return True


def list_incident_events(
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> list:
    status_filter = (status_filter or "").strip()
    risk_level_filter = (risk_level_filter or "").strip()
    personnel_name_filter = (personnel_name_filter or "").strip()

    result = []
    for snapshot in incident_events_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if incident_matches_filters(data, status_filter, risk_level_filter, personnel_name_filter):
            result.append(data)
    result.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    return result


def list_open_incident_events() -> list:
    """未結案案件清單，給每週提醒用。"""
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
    """標記結案，單向操作，沒有重新打開的路徑（比照補款/假別核准機制）。"""
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.update({"status": "closed"})
    return True


# ==========================================
# 員工體檢報告統整及追蹤
# ==========================================
def create_health_check(
    personnel_name: str,
    department: str,
    check_date: str,
    next_due_date: str,
    note: str,
    blob_path: str,
    filename: str,
    created_by: str,
    created_by_name: str,
) -> str:
    ref = health_checks_ref().document()
    ref.set(
        {
            "personnel_name": personnel_name,
            "department": department,
            "check_date": check_date,
            "next_due_date": next_due_date,
            "note": note,
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_health_check(record_id: str):
    snapshot = health_checks_ref().document(record_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def health_check_matches_filters(record: dict, name_filter: str = "", department_filter: str = "") -> bool:
    if name_filter and name_filter not in (record.get("personnel_name") or ""):
        return False
    if department_filter and department_filter not in (record.get("department") or ""):
        return False
    return True


def list_health_checks(name_filter: str = "", department_filter: str = "") -> list:
    name_filter = (name_filter or "").strip()
    department_filter = (department_filter or "").strip()
    result = []
    for snapshot in health_checks_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if health_check_matches_filters(data, name_filter, department_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("check_date", ""), reverse=True)
    return result


def update_health_check(
    record_id: str,
    personnel_name: str,
    department: str,
    check_date: str,
    next_due_date: str,
    note: str,
    blob_path: str = None,
    filename: str = None,
) -> bool:
    """blob_path/filename 是 None 代表這次沒有上傳新檔案，維持原本的附件。"""
    ref = health_checks_ref().document(record_id)
    if not ref.get().exists:
        return False
    update = {
        "personnel_name": personnel_name,
        "department": department,
        "check_date": check_date,
        "next_due_date": next_due_date,
        "note": note,
    }
    if blob_path is not None:
        update["blob_path"] = blob_path
        update["filename"] = filename
    ref.update(update)
    return True


def delete_health_check(record_id: str):
    """回傳被刪除紀錄的 blob_path（給呼叫端一併清掉 GCS 上的檔案），找不到
    紀錄時回傳 None。"""
    ref = health_checks_ref().document(record_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path


# ==========================================
# 員工關懷彙整及追蹤
# 不做死板分類，主題（subject）由同仁自己打字描述（例如「關懷面談」
# 「不法侵害會議記錄」），可選填當事人姓名方便之後搜尋。
# ==========================================
def create_care_log(
    log_date: str,
    subject: str,
    personnel_name: str,
    content: str,
    blob_path: str,
    filename: str,
    created_by: str,
    created_by_name: str,
) -> str:
    ref = care_logs_ref().document()
    ref.set(
        {
            "log_date": log_date,
            "subject": subject,
            "personnel_name": personnel_name,
            "content": content,
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_care_log(record_id: str):
    snapshot = care_logs_ref().document(record_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def care_log_matches_filters(record: dict, keyword_filter: str = "") -> bool:
    if not keyword_filter:
        return True
    keyword_filter = keyword_filter.lower()
    haystack = " ".join(
        [record.get("subject") or "", record.get("personnel_name") or "", record.get("content") or ""]
    ).lower()
    return keyword_filter in haystack


def list_care_logs(keyword_filter: str = "") -> list:
    keyword_filter = (keyword_filter or "").strip()
    result = []
    for snapshot in care_logs_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if care_log_matches_filters(data, keyword_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("log_date", ""), reverse=True)
    return result


def update_care_log(
    record_id: str,
    log_date: str,
    subject: str,
    personnel_name: str,
    content: str,
    blob_path: str = None,
    filename: str = None,
) -> bool:
    ref = care_logs_ref().document(record_id)
    if not ref.get().exists:
        return False
    update = {
        "log_date": log_date,
        "subject": subject,
        "personnel_name": personnel_name,
        "content": content,
    }
    if blob_path is not None:
        update["blob_path"] = blob_path
        update["filename"] = filename
    ref.update(update)
    return True


def delete_care_log(record_id: str):
    ref = care_logs_ref().document(record_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path


# ==========================================
# 公司證照彙整（公司本身持有的證照/執照/許可，不是同仁個人文件）
# 到期提醒做法比照配送部文件到期提醒：last_reminded_at 記錄提醒時間，避免
# 短時間內重複提醒；到期日異動時清掉 last_reminded_at，重新進入提醒週期。
# ==========================================
def create_license(
    name: str,
    issuer: str,
    license_no: str,
    expiry_date: str,
    blob_path: str,
    filename: str,
    created_by: str,
    created_by_name: str,
) -> str:
    ref = licenses_ref().document()
    ref.set(
        {
            "name": name,
            "issuer": issuer,
            "license_no": license_no,
            "expiry_date": expiry_date,
            "last_reminded_at": "",
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_license(license_id: str):
    snapshot = licenses_ref().document(license_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def license_matches_filters(record: dict, name_filter: str = "") -> bool:
    if name_filter and name_filter not in (record.get("name") or ""):
        return False
    return True


def list_licenses(name_filter: str = "") -> list:
    name_filter = (name_filter or "").strip()
    result = []
    for snapshot in licenses_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if license_matches_filters(data, name_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("expiry_date", ""))
    return result


def update_license(
    license_id: str,
    name: str,
    issuer: str,
    license_no: str,
    expiry_date: str,
    blob_path: str = None,
    filename: str = None,
) -> bool:
    """到期日跟建立時不一樣時，順便清掉 last_reminded_at，讓到期提醒的週期
    重新開始算（比照配送部文件到期提醒同一套做法）。"""
    ref = licenses_ref().document(license_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False
    existing = snapshot.to_dict() or {}
    update = {
        "name": name,
        "issuer": issuer,
        "license_no": license_no,
        "expiry_date": expiry_date,
    }
    if expiry_date != existing.get("expiry_date"):
        update["last_reminded_at"] = ""
    if blob_path is not None:
        update["blob_path"] = blob_path
        update["filename"] = filename
    ref.update(update)
    return True


def delete_license(license_id: str):
    ref = licenses_ref().document(license_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path


def list_expiring_licenses(days_ahead: int, resend_interval_days: int) -> list:
    """回傳到期日在「今天~今天+days_ahead 天」之間、或已經過期，而且沒有在
    最近 resend_interval_days 天內提醒過的證照，依到期日由近到遠排序。"""
    today = date.today()
    result = []
    for snapshot in licenses_ref().stream():
        data = snapshot.to_dict() or {}
        expiry = _parse_date(data.get("expiry_date"))
        if expiry is None or (expiry - today).days > days_ahead:
            continue
        last_reminded = _parse_date(data.get("last_reminded_at"))
        if last_reminded is not None and (today - last_reminded).days < resend_interval_days:
            continue
        data["id"] = snapshot.id
        data["expired"] = expiry < today
        result.append(data)
    result.sort(key=lambda item: item.get("expiry_date", ""))
    return result


def mark_licenses_reminded(license_ids: list):
    today_iso = date.today().isoformat()
    for license_id in license_ids:
        licenses_ref().document(license_id).update({"last_reminded_at": today_iso})


# ==========================================
# 教育訓練訓練匯整
# ==========================================
def create_training(
    personnel_name: str,
    course_name: str,
    training_date: str,
    hours: str,
    note: str,
    blob_path: str,
    filename: str,
    created_by: str,
    created_by_name: str,
) -> str:
    ref = trainings_ref().document()
    ref.set(
        {
            "personnel_name": personnel_name,
            "course_name": course_name,
            "training_date": training_date,
            "hours": hours,
            "note": note,
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_training(record_id: str):
    snapshot = trainings_ref().document(record_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def training_matches_filters(record: dict, name_filter: str = "", course_filter: str = "") -> bool:
    if name_filter and name_filter not in (record.get("personnel_name") or ""):
        return False
    if course_filter and course_filter not in (record.get("course_name") or ""):
        return False
    return True


def list_trainings(name_filter: str = "", course_filter: str = "") -> list:
    name_filter = (name_filter or "").strip()
    course_filter = (course_filter or "").strip()
    result = []
    for snapshot in trainings_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if training_matches_filters(data, name_filter, course_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("training_date", ""), reverse=True)
    return result


def update_training(
    record_id: str,
    personnel_name: str,
    course_name: str,
    training_date: str,
    hours: str,
    note: str,
    blob_path: str = None,
    filename: str = None,
) -> bool:
    ref = trainings_ref().document(record_id)
    if not ref.get().exists:
        return False
    update = {
        "personnel_name": personnel_name,
        "course_name": course_name,
        "training_date": training_date,
        "hours": hours,
        "note": note,
    }
    if blob_path is not None:
        update["blob_path"] = blob_path
        update["filename"] = filename
    ref.update(update)
    return True


def delete_training(record_id: str):
    ref = trainings_ref().document(record_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path
