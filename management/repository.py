"""管理部系統的資料存取層：公告、會議記錄、文件庫……等各自獨立的 CRUD，刻意
不做成一個共用的泛型 CRUD 函式——欄位跟業務規則各自不同（公告沒有附件、
文件庫一定要有附件、客戶拜訪紀錄只有特定人看得到），分開寫更直觀，之後
各自演變也不會互相牽扯。
"""
import time

from management.db import (
    announcements_ref,
    assets_ref,
    client_visits_ref,
    documents_ref,
    kpi_reports_ref,
    meeting_notes_ref,
    staff_directory_ref,
)

# ==========================================
# 公告事項
# ==========================================
_ANNOUNCEMENT_FIELDS = ["title", "body", "created_by", "created_by_name"]


def create_announcement(title: str, body: str, created_by: str, created_by_name: str) -> str:
    ref = announcements_ref().document()
    ref.set(
        {
            "title": title,
            "body": body,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_announcement(announcement_id: str):
    snapshot = announcements_ref().document(announcement_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_announcements() -> list:
    result = []
    for snapshot in announcements_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def delete_announcement(announcement_id: str) -> bool:
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 會議記錄
# ==========================================
def create_meeting_note(
    title: str,
    meeting_date: str,
    department: str,
    content: str,
    created_by: str,
    created_by_name: str,
    attachment_blob_path: str = "",
    attachment_filename: str = "",
) -> str:
    ref = meeting_notes_ref().document()
    ref.set(
        {
            "title": title,
            "meeting_date": meeting_date,
            "department": department,
            "content": content,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "attachment_blob_path": attachment_blob_path,
            "attachment_filename": attachment_filename,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_meeting_note(note_id: str):
    snapshot = meeting_notes_ref().document(note_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_meeting_notes(department_filter: str = "") -> list:
    department_filter = (department_filter or "").strip()
    result = []
    for snapshot in meeting_notes_ref().stream():
        data = snapshot.to_dict() or {}
        if department_filter and data.get("department") != department_filter:
            continue
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda n: n.get("meeting_date", ""), reverse=True)
    return result


def delete_meeting_note(note_id: str) -> bool:
    ref = meeting_notes_ref().document(note_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 規章/SOP 文件庫
# ==========================================
def create_document(title: str, category: str, description: str, blob_path: str, filename: str, created_by: str, created_by_name: str) -> str:
    ref = documents_ref().document()
    ref.set(
        {
            "title": title,
            "category": category,
            "description": description,
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_document(document_id: str):
    snapshot = documents_ref().document(document_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_documents(category_filter: str = "") -> list:
    category_filter = (category_filter or "").strip()
    result = []
    for snapshot in documents_ref().stream():
        data = snapshot.to_dict() or {}
        if category_filter and data.get("category") != category_filter:
            continue
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return result


def delete_document(document_id: str):
    """回傳被刪除文件的 blob_path（給呼叫端一併清掉 GCS 上的檔案），
    找不到文件時回傳 None。"""
    ref = documents_ref().document(document_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path


# ==========================================
# 業績報表庫（業務主管專用）
# 單純是檔案上傳/下載（Excel/PDF/PPT/圖檔），不做成計算目標達成率的儀表板
# ——老闆表示這樣就夠用，之後真的需要再擴充。
# ==========================================
def create_kpi_report(title: str, description: str, blob_path: str, filename: str, created_by: str, created_by_name: str) -> str:
    ref = kpi_reports_ref().document()
    ref.set(
        {
            "title": title,
            "description": description,
            "blob_path": blob_path,
            "filename": filename,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_kpi_report(report_id: str):
    snapshot = kpi_reports_ref().document(report_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_kpi_reports() -> list:
    result = []
    for snapshot in kpi_reports_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return result


def delete_kpi_report(report_id: str):
    """回傳被刪除報表的 blob_path（給呼叫端一併清掉 GCS 上的檔案），
    找不到報表時回傳 None。"""
    ref = kpi_reports_ref().document(report_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    blob_path = (snapshot.to_dict() or {}).get("blob_path")
    ref.delete()
    return blob_path


# ==========================================
# 客戶拜訪紀錄（業務主管專用）
# 有管理部權限的同仁都可以新增，但刻意設計成「只有記錄本人跟全平台管理員
# （老闆）看得到」——這是業務同仁私下的拜訪紀錄，不是像公告/會議記錄那樣
# 全部門共享的資訊，跟其他管理部功能的可見範圍邏輯不一樣。
# ==========================================
def create_client_visit(
    client_name: str,
    visit_date: str,
    arranged_by: str,
    visitor: str,
    follow_up_status: str,
    notes: str,
    created_by: str,
    created_by_name: str,
) -> str:
    ref = client_visits_ref().document()
    ref.set(
        {
            "client_name": client_name,
            "visit_date": visit_date,
            "arranged_by": arranged_by,
            "visitor": visitor,
            "follow_up_status": follow_up_status,
            "notes": notes,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def can_view_client_visit(visit: dict, username: str, is_platform_admin: bool) -> bool:
    """純函式，方便測試：只有記錄本人（created_by）或全平台管理員看得到
    這筆拜訪紀錄。"""
    if is_platform_admin:
        return True
    return visit.get("created_by") == username


def get_client_visit(visit_id: str):
    snapshot = client_visits_ref().document(visit_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_client_visits(username: str, is_platform_admin: bool) -> list:
    """只回傳這個帳號看得到的拜訪紀錄（見 can_view_client_visit）。"""
    result = []
    for snapshot in client_visits_ref().stream():
        data = snapshot.to_dict() or {}
        if not can_view_client_visit(data, username, is_platform_admin):
            continue
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda v: v.get("visit_date", ""), reverse=True)
    return result


def delete_client_visit(visit_id: str) -> bool:
    ref = client_visits_ref().document(visit_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 全公司員工名冊／組織圖（人事/組織）
# 跟配送部系統的「人員管理」是兩回事：那個是配送員/廠商人員，這裡是公司
# 內部同仁（含業務、管理部、內勤……），欄位刻意精簡（部門/姓名/職稱），
# 之後真的有需要再擴充。組織圖是這份名冊依部門分組後的畫面呈現，不是
# 另外維護一份資料。
# ==========================================
def create_staff_member(department: str, name: str, title: str, created_by: str, created_by_name: str) -> str:
    ref = staff_directory_ref().document()
    ref.set(
        {
            "department": department,
            "name": name,
            "title": title,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_staff_member(staff_id: str):
    snapshot = staff_directory_ref().document(staff_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_staff_members() -> list:
    result = []
    for snapshot in staff_directory_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda s: (s.get("department", ""), s.get("name", "")))
    return result


def group_staff_by_department(staff_list: list) -> list:
    """把名冊依部門分組，回傳 [{"department": ..., "members": [...]}, ...]，
    純函式方便測試，組織圖畫面直接拿這個結果去畫。部門依名稱排序，同部門內
    依姓名排序（沿用 list_staff_members() 已經排好的順序）。"""
    groups = {}
    order = []
    for member in staff_list:
        dept = member.get("department", "")
        if dept not in groups:
            groups[dept] = []
            order.append(dept)
        groups[dept].append(member)
    return [{"department": dept, "members": groups[dept]} for dept in order]


def delete_staff_member(staff_id: str) -> bool:
    ref = staff_directory_ref().document(staff_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 資產/設備管理
# 公務車（跟配送部車輛管理無關）、公務手機、門號、電腦。
# ==========================================
def create_asset(category: str, name: str, assigned_to: str, status: str, notes: str, created_by: str, created_by_name: str) -> str:
    ref = assets_ref().document()
    ref.set(
        {
            "category": category,
            "name": name,
            "assigned_to": assigned_to,
            "status": status,
            "notes": notes,
            "created_by": created_by,
            "created_by_name": created_by_name,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_asset(asset_id: str):
    snapshot = assets_ref().document(asset_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_assets(category_filter: str = "") -> list:
    category_filter = (category_filter or "").strip()
    result = []
    for snapshot in assets_ref().stream():
        data = snapshot.to_dict() or {}
        if category_filter and data.get("category") != category_filter:
            continue
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def delete_asset(asset_id: str) -> bool:
    ref = assets_ref().document(asset_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True
