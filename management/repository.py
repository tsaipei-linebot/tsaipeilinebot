"""管理部系統的資料存取層：公告、會議記錄、文件庫，三組獨立的 CRUD，刻意
不做成一個共用的泛型 CRUD 函式——欄位跟業務規則各自不同（公告沒有附件、
文件庫一定要有附件），分開寫更直觀，之後各自演變也不會互相牽扯。
"""
import time

from management.db import announcements_ref, documents_ref, meeting_notes_ref

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
