"""人資專區的 Firestore 存取集中處，跟 delivery/db.py、management/db.py 是
同樣的作法（lazy singleton，避免 import 這個模組就必須有 GCP 憑證）。使用者
帳號沿用根目錄 platform_db.py 的共用帳號表，這裡不重複定義。
"""
from platform_db import get_db

INCIDENT_EVENTS_COLLECTION = "hr_incident_events"
HEALTH_CHECKS_COLLECTION = "hr_health_checks"
CARE_LOGS_COLLECTION = "hr_care_logs"
LICENSES_COLLECTION = "hr_licenses"
TRAININGS_COLLECTION = "hr_trainings"


def incident_events_ref():
    return get_db().collection(INCIDENT_EVENTS_COLLECTION)


def health_checks_ref():
    return get_db().collection(HEALTH_CHECKS_COLLECTION)


def care_logs_ref():
    return get_db().collection(CARE_LOGS_COLLECTION)


def licenses_ref():
    return get_db().collection(LICENSES_COLLECTION)


def trainings_ref():
    return get_db().collection(TRAININGS_COLLECTION)
