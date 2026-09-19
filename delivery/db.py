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
COOPERATION_TYPES_COLLECTION = "delivery_cooperation_types"
REPAYMENTS_COLLECTION = "delivery_repayments"
SICK_LEAVES_COLLECTION = "delivery_sick_leaves"
APPLICANTS_COLLECTION = "delivery_applicants"
VEHICLES_COLLECTION = "delivery_vehicles"
VEHICLE_EVENTS_COLLECTION = "delivery_vehicle_events"
VEHICLE_SERVICE_AREAS_COLLECTION = "delivery_vehicle_service_areas"
INCIDENT_EVENTS_COLLECTION = "delivery_incident_events"
EQUIPMENT_ITEMS_COLLECTION = "delivery_equipment_items"
EQUIPMENT_LOCATIONS_COLLECTION = "delivery_equipment_locations"
EQUIPMENT_STOCK_COLLECTION = "delivery_equipment_stock"
EQUIPMENT_DEBT_COLLECTION = "delivery_equipment_debt"
EQUIPMENT_TRANSACTIONS_COLLECTION = "delivery_equipment_transactions"

# 外送員接單媒合（即時接單／報班媒合，2026-09-19 新增）：跟裝備借還一樣，
# 刻意用「扁平集合 + 外鍵欄位」而不是真的 Firestore 子集合（例如
# delivery_rider_claims 用 store_delivery_id 欄位指回它屬於哪一筆
# delivery_rider_store_deliveries，不是 store_deliveries/{id}/claims 這種
# 巢狀路徑）——整個 repo 目前沒有任何地方用到真的子集合，保持這個唯一的
# 做法，查詢/測試方式都能沿用既有其他功能的寫法。
RIDER_BINDINGS_COLLECTION = "delivery_rider_bindings"
RIDER_STORE_DELIVERIES_COLLECTION = "delivery_rider_store_deliveries"
RIDER_CLAIMS_COLLECTION = "delivery_rider_claims"
RIDER_SHIFT_POSTINGS_COLLECTION = "delivery_rider_shift_postings"
RIDER_SHIFT_REGISTRATIONS_COLLECTION = "delivery_rider_shift_registrations"
RIDER_LOCATIONS_COLLECTION = "delivery_rider_locations"


def personnel_ref():
    return get_db().collection(PERSONNEL_COLLECTION)


def cooperation_types_ref():
    return get_db().collection(COOPERATION_TYPES_COLLECTION)


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


def vehicle_service_areas_ref():
    return get_db().collection(VEHICLE_SERVICE_AREAS_COLLECTION)


def incident_events_ref():
    return get_db().collection(INCIDENT_EVENTS_COLLECTION)


def equipment_items_ref():
    return get_db().collection(EQUIPMENT_ITEMS_COLLECTION)


def equipment_locations_ref():
    return get_db().collection(EQUIPMENT_LOCATIONS_COLLECTION)


def equipment_stock_ref():
    return get_db().collection(EQUIPMENT_STOCK_COLLECTION)


def equipment_debt_ref():
    return get_db().collection(EQUIPMENT_DEBT_COLLECTION)


def equipment_transactions_ref():
    return get_db().collection(EQUIPMENT_TRANSACTIONS_COLLECTION)


def rider_bindings_ref():
    return get_db().collection(RIDER_BINDINGS_COLLECTION)


def rider_store_deliveries_ref():
    return get_db().collection(RIDER_STORE_DELIVERIES_COLLECTION)


def rider_claims_ref():
    return get_db().collection(RIDER_CLAIMS_COLLECTION)


def rider_shift_postings_ref():
    return get_db().collection(RIDER_SHIFT_POSTINGS_COLLECTION)


def rider_shift_registrations_ref():
    return get_db().collection(RIDER_SHIFT_REGISTRATIONS_COLLECTION)


def rider_locations_ref():
    return get_db().collection(RIDER_LOCATIONS_COLLECTION)
