"""外送員接單媒合（即時接單／報班媒合，2026-09-19 新增）的資料存取與交易邏輯。

跟現有排班「發包」機制（專員主動推播調度表、騎士被動接受）是相反方向的
操作模式（pull vs. push），資料表、程式邏輯完全分開，不會動到發包機制。

「接不接受這次操作」的核心規則刻意拆成不依賴 Firestore 的純函式
（_evaluate_claim／_evaluate_registration），Firestore transaction 只負責
「讀一次目前資料 → 呼叫純函式決定 → 接受的話才寫回去」，跟
repository.py 缺件判斷、services/session_service.py 的 _run_in_transaction
是同一個分工方式：純邏輯本身可以直接單元測試邊界情況（剛好用完／超過／
已關閉／重複報名），不需要真的連 Firestore、也不需要 emulator。
"""
import math
import time

from google.cloud import firestore

from delivery.config import RIDER_PENDING_CLAIM_TTL_SECONDS
from delivery.db import (
    get_db,
    rider_bindings_ref,
    rider_claims_ref,
    rider_shift_postings_ref,
    rider_shift_registrations_ref,
    rider_store_deliveries_ref,
)

RIDER_STATUS_ACTIVE = "active"
RIDER_STATUS_BLOCKED = "blocked"
RIDER_STATUSES = [
    {"code": RIDER_STATUS_ACTIVE, "name": "啟用"},
    {"code": RIDER_STATUS_BLOCKED, "name": "停用"},
]

STORE_DELIVERY_STATUS_OPEN = "open"
STORE_DELIVERY_STATUS_CLOSED = "closed"

SHIFT_STATUS_OPEN = "open"
SHIFT_STATUS_CLOSED = "closed"


# ==========================================
# 騎士綁定（跟 GAS「綁定+工號+姓名」同步）＋ 名單管理（啟用/停用）
# ==========================================
def upsert_rider_binding(user_id: str, employee_id: str, name: str) -> None:
    """GAS 那邊騎士完成/更新「綁定+工號+姓名」後同步呼叫。刻意保留既有的
    status——已經被管理員停用的騎士，重新綁定或改名不會自動解除停用，只有
    全新綁定才預設 active。"""
    ref = rider_bindings_ref().document(user_id)
    snapshot = ref.get()
    status = RIDER_STATUS_ACTIVE
    if snapshot.exists:
        status = (snapshot.to_dict() or {}).get("status") or RIDER_STATUS_ACTIVE
    ref.set(
        {
            "employee_id": employee_id,
            "name": name,
            "status": status,
            "updated_at": time.time(),
        },
        merge=True,
    )


def get_rider_binding(user_id: str):
    snapshot = rider_bindings_ref().document(user_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["user_id"] = snapshot.id
    data.setdefault("status", RIDER_STATUS_ACTIVE)
    return data


def is_rider_active(user_id: str) -> bool:
    binding = get_rider_binding(user_id)
    return bool(binding) and binding.get("status") == RIDER_STATUS_ACTIVE


def list_riders() -> list:
    riders = []
    for snapshot in rider_bindings_ref().stream():
        data = snapshot.to_dict() or {}
        data["user_id"] = snapshot.id
        data.setdefault("status", RIDER_STATUS_ACTIVE)
        riders.append(data)
    riders.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return riders


def set_rider_status(user_id: str, status: str) -> bool:
    """後台「騎士名單管理」啟用/停用一個騎士——停用後這個 LINE 帳號即時
    接單/報班媒合這兩個功能都會被擋下（見 delivery/routes/webhook_routes.py
    的 rider-events 端點）。"""
    if status not in (RIDER_STATUS_ACTIVE, RIDER_STATUS_BLOCKED):
        return False
    ref = rider_bindings_ref().document(user_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status, "updated_at": time.time()})
    return True


def set_pending_claim(user_id: str, store_id: str) -> None:
    """騎士點了「承接」但還沒輸入件數，暫存他正要承接哪一筆門市當日量。"""
    rider_bindings_ref().document(user_id).update(
        {"pending_claim": {"store_id": store_id, "set_at": time.time()}}
    )


def pop_pending_claim(user_id: str) -> str:
    """取出目前暫存的「正要承接哪一筆」並清掉。沒有暫存、或暫存已經超過
    RIDER_PENDING_CLAIM_TTL_SECONDS 都回傳空字串——避免騎士點了「承接」後
    放著不理，很久之後才傳一則不相干的數字訊息被誤當成件數輸入。"""
    ref = rider_bindings_ref().document(user_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return ""
    pending = (snapshot.to_dict() or {}).get("pending_claim") or {}
    store_id = pending.get("store_id") or ""
    set_at = pending.get("set_at") or 0
    if not store_id:
        return ""
    ref.update({"pending_claim": None})
    if (time.time() - set_at) > RIDER_PENDING_CLAIM_TTL_SECONDS:
        return ""
    return store_id


# ==========================================
# 即時接單：門市當日可承接量
# ==========================================
def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """兩個經緯度之間的球面距離（公里）。Firestore 沒有「依距離排序」的
    原生查詢能力，這裡是刻意先撈出候選門市、程式碼自己算距離再排序——
    門市數量不多的話完全沒問題。"""
    earth_radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_p = math.radians(lat2 - lat1)
    delta_l = math.radians(lng2 - lng1)
    a = math.sin(delta_p / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_l / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def create_store_delivery(store_name: str, lat: float, lng: float, date_str: str, total_quantity: int, created_by: str) -> str:
    ref = rider_store_deliveries_ref().document()
    ref.set(
        {
            "store_name": store_name,
            "lat": lat,
            "lng": lng,
            "date": date_str,
            "total_quantity": total_quantity,
            "claimed_quantity": 0,
            "source": "manual",
            "status": STORE_DELIVERY_STATUS_OPEN,
            "created_by": created_by,
            "updated_by": created_by,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    )
    return ref.id


def get_store_delivery(store_id: str):
    snapshot = rider_store_deliveries_ref().document(store_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data["remaining_quantity"] = (data.get("total_quantity") or 0) - (data.get("claimed_quantity") or 0)
    return data


def list_store_deliveries(date_str: str = "") -> list:
    """後台管理用：列出（可選依日期篩選）全部門市當日量，不管開放/關閉。"""
    query = rider_store_deliveries_ref()
    if date_str:
        query = query.where("date", "==", date_str)
    items = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["remaining_quantity"] = (data.get("total_quantity") or 0) - (data.get("claimed_quantity") or 0)
        items.append(data)
    items.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return items


def update_store_delivery_quantity(store_id: str, total_quantity: int, updated_by: str) -> bool:
    ref = rider_store_deliveries_ref().document(store_id)
    if not ref.get().exists:
        return False
    ref.update({"total_quantity": total_quantity, "updated_by": updated_by, "updated_at": time.time()})
    return True


def set_store_delivery_status(store_id: str, status: str, updated_by: str) -> bool:
    if status not in (STORE_DELIVERY_STATUS_OPEN, STORE_DELIVERY_STATUS_CLOSED):
        return False
    ref = rider_store_deliveries_ref().document(store_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status, "updated_by": updated_by, "updated_at": time.time()})
    return True


def list_nearby_open_stores(lat: float, lng: float, date_str: str, limit: int = 8) -> list:
    """騎士查詢附近單：當日開放中、還有剩餘量的門市，依距離由近到遠排序。"""
    results = []
    query = rider_store_deliveries_ref().where("date", "==", date_str).where("status", "==", STORE_DELIVERY_STATUS_OPEN)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        remaining = (data.get("total_quantity") or 0) - (data.get("claimed_quantity") or 0)
        if remaining <= 0:
            continue
        store_lat, store_lng = data.get("lat"), data.get("lng")
        distance_km = None
        if store_lat is not None and store_lng is not None:
            distance_km = _haversine_km(lat, lng, store_lat, store_lng)
        results.append(
            {
                "id": snapshot.id,
                "store_name": data.get("store_name", ""),
                "remaining_quantity": remaining,
                "distance_km": distance_km,
            }
        )
    results.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"]))
    return results[:limit]


def list_claims(store_id: str) -> list:
    """後台對帳用：這筆門市當日量目前有誰承接了多少。"""
    items = []
    for snapshot in rider_claims_ref().where("store_delivery_id", "==", store_id).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        items.append(data)
    items.sort(key=lambda d: d.get("claimed_at") or 0)
    return items


def _evaluate_claim(store_data: dict, quantity: int):
    """純邏輯：目前這筆門市當日量的資料 + 騎士要承接的件數，決定接不接受。
    回傳 (是否接受, 給騎士的訊息, 接受時 claimed_quantity 應更新成的值)。"""
    if quantity <= 0:
        return False, "承接件數要是大於 0 的整數，請重新輸入。", None
    if store_data.get("status") != STORE_DELIVERY_STATUS_OPEN:
        return False, "這筆門市當日量已經關閉，無法承接。", None
    claimed = store_data.get("claimed_quantity") or 0
    total = store_data.get("total_quantity") or 0
    remaining = total - claimed
    if quantity > remaining:
        return False, f"目前剩餘可承接量只有 {remaining} 件，請重新輸入不超過這個數字的件數。", None
    return True, "", claimed + quantity


def claim_store_delivery(store_id: str, rider_id: str, rider_name: str, quantity: int):
    """在單一 transaction 內完成「讀取門市當日量 → 用 _evaluate_claim() 決定
    接不接受 → 接受的話才更新 claimed_quantity、寫入一筆 claims 紀錄」，確保
    兩位騎士幾乎同時承接同一筆門市當日量時不會一起超放。回傳 (是否成功,
    給騎士的訊息)。"""
    store_ref = rider_store_deliveries_ref().document(store_id)
    claim_ref = rider_claims_ref().document()
    transaction = get_db().transaction()

    @firestore.transactional
    def _txn(transaction):
        snapshot = store_ref.get(transaction=transaction)
        if not snapshot.exists:
            return False, "找不到這筆門市當日量，可能已經被下架。"
        store_data = snapshot.to_dict() or {}
        ok, message, new_claimed = _evaluate_claim(store_data, quantity)
        if not ok:
            return False, message
        transaction.update(store_ref, {"claimed_quantity": new_claimed, "updated_at": time.time()})
        transaction.set(
            claim_ref,
            {
                "store_delivery_id": store_id,
                "store_name": store_data.get("store_name", ""),
                "rider_id": rider_id,
                "rider_name": rider_name,
                "quantity": quantity,
                "status": "active",
                "claimed_at": time.time(),
            },
        )
        store_name = store_data.get("store_name", "")
        return True, f"承接成功！{store_name} {quantity} 件，已經記錄在您的名下。"

    return _txn(transaction)


# ==========================================
# 報班媒合：需求時段
# ==========================================
def create_shift_posting(posted_by: str, location: str, start_time: float, end_time: float, capacity: int) -> str:
    ref = rider_shift_postings_ref().document()
    ref.set(
        {
            "posted_by": posted_by,
            "location": location,
            "start_time": start_time,
            "end_time": end_time,
            "capacity": capacity,
            "status": SHIFT_STATUS_OPEN,
            "created_at": time.time(),
        }
    )
    return ref.id


def get_shift_posting(shift_id: str):
    snapshot = rider_shift_postings_ref().document(shift_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_shift_postings() -> list:
    """後台管理用：列出全部報班時段，不管開放/關閉。"""
    items = []
    for snapshot in rider_shift_postings_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["registered_count"] = count_registrations(snapshot.id)
        items.append(data)
    items.sort(key=lambda d: d.get("start_time") or 0, reverse=True)
    return items


def list_open_shift_postings() -> list:
    """騎士瀏覽報班：只列出開放中的時段，依開始時間排序。"""
    items = []
    for snapshot in rider_shift_postings_ref().where("status", "==", SHIFT_STATUS_OPEN).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["registered_count"] = count_registrations(snapshot.id)
        items.append(data)
    items.sort(key=lambda d: d.get("start_time") or 0)
    return items


def set_shift_posting_status(shift_id: str, status: str) -> bool:
    if status not in (SHIFT_STATUS_OPEN, SHIFT_STATUS_CLOSED):
        return False
    ref = rider_shift_postings_ref().document(shift_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status})
    return True


def count_registrations(shift_id: str) -> int:
    return sum(1 for _ in rider_shift_registrations_ref().where("shift_id", "==", shift_id).stream())


def list_registrations(shift_id: str) -> list:
    items = []
    for snapshot in rider_shift_registrations_ref().where("shift_id", "==", shift_id).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        items.append(data)
    items.sort(key=lambda d: d.get("registered_at") or 0)
    return items


def _evaluate_registration(shift_data: dict, existing_rider_ids: list, rider_id: str):
    """純邏輯：這個時段目前的資料 + 已報名的騎士清單 + 這次要報名的騎士，
    決定接不接受。回傳 (是否接受, 給騎士的訊息)。"""
    if shift_data.get("status") != SHIFT_STATUS_OPEN:
        return False, "這個報班時段已經關閉，無法報名。"
    if rider_id in existing_rider_ids:
        return False, "您已經報名過這個時段了。"
    capacity = shift_data.get("capacity") or 0
    if len(existing_rider_ids) >= capacity:
        return False, "這個時段名額已滿。"
    return True, "報名成功！"


def register_shift(shift_id: str, rider_id: str, rider_name: str):
    """在單一 transaction 內完成「讀取時段 + 目前報名人數 → 用
    _evaluate_registration() 決定接不接受 → 接受的話才寫入報名紀錄」，確保
    兩位騎士幾乎同時報名同一個時段時不會一起超收。目前人數用實際報名紀錄
    筆數計算（不另存一個 counter 欄位），避免計數跟實際報名紀錄對不上。"""
    shift_ref = rider_shift_postings_ref().document(shift_id)
    registration_ref = rider_shift_registrations_ref().document()
    transaction = get_db().transaction()

    @firestore.transactional
    def _txn(transaction):
        snapshot = shift_ref.get(transaction=transaction)
        if not snapshot.exists:
            return False, "找不到這個報班時段，可能已經被下架。"
        shift_data = snapshot.to_dict() or {}
        existing_query = rider_shift_registrations_ref().where("shift_id", "==", shift_id)
        existing_docs = list(transaction.get(existing_query))
        existing_rider_ids = [(doc.to_dict() or {}).get("rider_id") for doc in existing_docs]
        ok, message = _evaluate_registration(shift_data, existing_rider_ids, rider_id)
        if not ok:
            return False, message
        transaction.set(
            registration_ref,
            {
                "shift_id": shift_id,
                "rider_id": rider_id,
                "rider_name": rider_name,
                "registered_at": time.time(),
            },
        )
        return True, message

    return _txn(transaction)
