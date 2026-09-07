"""配送部子系統的 Firestore 存取集中處。

跟 services/session_service.py 不同，這裡刻意把 firestore.Client() 的建立
延後到「第一次真正需要用到」才執行（lazy singleton），而不是在模組載入當下
就連線。這樣測試或本機在沒有 GCP Application Default Credentials 的情況下，
仍然可以 import 這個模組（例如只是要測 auth.py 的密碼雜湊邏輯），不會因為
匯入鏈間接觸發 Firestore 連線而整個炸掉。

使用者帳號（users_ref）已經搬到根目錄的 platform_db.py——那是全平台共用的
帳號表，不是配送部專屬的資料，這裡重新匯入只是為了不用改遍所有既有的
`from delivery.db import users_ref` 呼叫端。
"""
from platform_db import get_db, users_ref  # noqa: F401  (向下相容既有匯入)

PERSONNEL_COLLECTION = "delivery_personnel"
REPAYMENTS_COLLECTION = "delivery_repayments"
SICK_LEAVES_COLLECTION = "delivery_sick_leaves"
APPLICANTS_COLLECTION = "delivery_applicants"
VEHICLES_COLLECTION = "delivery_vehicles"
VEHICLE_EVENTS_COLLECTION = "delivery_vehicle_events"
INCIDENT_EVENTS_COLLECTION = "delivery_incident_events"


def personnel_ref():
    return get_db().collection(PERSONNEL_COLLECTION)


def repayments_ref():
    return get_db().collection(REPAYMENTS_COLLECTION)


def sick_leaves_ref():
    return get_db().collection(SICK_LEAVES_COLLECTION)


def applicants_ref():
    return get_db().collection(APPLICANTS_COLLECTION)


def vehicles_ref():
    return get_db().collection(VEHICLES_COLLECTION)


def vehicle_events_ref():
    return get_db().collection(VEHICLE_EVENTS_COLLECTION)


def incident_events_ref():
    return get_db().collection(INCIDENT_EVENTS_COLLECTION)
