"""管理部子系統的 Firestore 存取集中處，跟 delivery/db.py 是同樣的作法
（lazy singleton，避免 import 這個模組就必須有 GCP 憑證）。使用者帳號沿用
根目錄 platform_db.py 的共用帳號表，這裡不重複定義。
"""
from platform_db import get_db

ANNOUNCEMENTS_COLLECTION = "management_announcements"
MEETING_NOTES_COLLECTION = "management_meeting_notes"
DOCUMENTS_COLLECTION = "management_documents"


def announcements_ref():
    return get_db().collection(ANNOUNCEMENTS_COLLECTION)


def meeting_notes_ref():
    return get_db().collection(MEETING_NOTES_COLLECTION)


def documents_ref():
    return get_db().collection(DOCUMENTS_COLLECTION)
