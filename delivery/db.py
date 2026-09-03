"""配送部子系統的 Firestore 存取集中處。

跟 services/session_service.py 不同，這裡刻意把 firestore.Client() 的建立
延後到「第一次真正需要用到」才執行（lazy singleton），而不是在模組載入當下
就連線。這樣測試或本機在沒有 GCP Application Default Credentials 的情況下，
仍然可以 import 這個模組（例如只是要測 auth.py 的密碼雜湊邏輯），不會因為
匯入鏈間接觸發 Firestore 連線而整個炸掉。
"""
from google.cloud import firestore

from config import GCP_PROJECT_ID

USERS_COLLECTION = "delivery_users"
PERSONNEL_COLLECTION = "delivery_personnel"
REPAYMENTS_COLLECTION = "delivery_repayments"
SICK_LEAVES_COLLECTION = "delivery_sick_leaves"
APPLICANTS_COLLECTION = "delivery_applicants"

_client = None


def get_db():
    global _client
    if _client is None:
        _client = firestore.Client(project=GCP_PROJECT_ID, database="(default)")
    return _client


def users_ref():
    return get_db().collection(USERS_COLLECTION)


def personnel_ref():
    return get_db().collection(PERSONNEL_COLLECTION)


def repayments_ref():
    return get_db().collection(REPAYMENTS_COLLECTION)


def sick_leaves_ref():
    return get_db().collection(SICK_LEAVES_COLLECTION)


def applicants_ref():
    return get_db().collection(APPLICANTS_COLLECTION)
