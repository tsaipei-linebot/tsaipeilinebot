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
from datetime import datetime

from google.cloud import firestore

from config import TAIPEI_TZ
from delivery import repository
from delivery.config import (
    COOPERATION_CATEGORY_CONTRACT,
    COOPERATION_CATEGORY_EMPLOYED,
    RIDER_DEFAULT_SEARCH_RADIUS_KM,
    RIDER_PENDING_CLAIM_TTL_SECONDS,
)
from delivery.db import (
    get_db,
    rider_bindings_ref,
    rider_claims_ref,
    rider_order_locations_ref,
    rider_shift_locations_ref,
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

# 門市當日量的管控依據（2026-09-22 改版）：原本是用「總件數 vs 已承接
# 件數」管控（騎士點承接後要再回一則訊息輸入自己要接幾件），使用者確認
# 實際作業上不需要騎士報件數，改成**只看「需求騎士數量」**：騎士點一下
# 「承接」就直接完成，一位騎士佔一個名額，名額滿了這筆門市當日量就不再
# 出現在其他騎士的清單上。
#
# `total_quantity`（當日量幾件）欄位保留，但**只是顯示給騎士參考的資訊**，
# 不再是管控依據，也不再累加 `claimed_quantity`（那個欄位留著只是為了
# 舊資料還看得懂，新的承接不會再寫它）。
#
# 舊資料（改版前建立、沒有 rider_capacity 欄位的門市當日量）一律視為
# 需求 1 位騎士——門市當日量本來就是每天各自獨立的資料，隔天就過期，
# 不需要寫遷移腳本回頭補這個欄位。
DEFAULT_RIDER_CAPACITY = 1

SHIFT_STATUS_OPEN = "open"
SHIFT_STATUS_CLOSED = "closed"

# 報班名額改成需要管理員人工審核（2026-09-21 新增）：騎士報名不再由系統
# 即時比對名額決定成不成功，一律先存成「待審核」，管理員在報名名單頁面
# 手動按核准／駁回，才會變成「已核准」（報名成功）或「已駁回」（額滿，
# 請騎士改報其他時段）。核准/駁回不會主動推播 LINE 訊息給騎士——騎士要
# 自己傳「查詢報名狀態」查詢結果（見 rider_events.py），刻意不做「系統
# 主動推播」是因為那需要讓配送部系統（Python）反過來呼叫 GAS 才能推播
# LINE 訊息，牽動 GAS 那邊所有 LINE 機器人共用的核心轉發程式，風險比較
# 高，使用者確認「騎士自己查詢」這個更簡單的做法就夠用。
REGISTRATION_STATUS_PENDING = "pending"
REGISTRATION_STATUS_APPROVED = "approved"
REGISTRATION_STATUS_REJECTED = "rejected"
REGISTRATION_STATUSES = [
    {"code": REGISTRATION_STATUS_PENDING, "name": "待審核"},
    {"code": REGISTRATION_STATUS_APPROVED, "name": "已核准"},
    {"code": REGISTRATION_STATUS_REJECTED, "name": "已駁回（額滿）"},
]


# ==========================================
# 騎士綁定（跟 GAS「綁定+工號+姓名」同步）＋ 名單管理（啟用/停用）
# ==========================================
def upsert_rider_binding(user_id: str, employee_id: str, name: str) -> None:
    """GAS 那邊騎士完成/更新「綁定+工號+姓名」後同步呼叫。刻意保留既有的
    status——已經被管理員停用的騎士，重新綁定或改名不會自動解除停用，只有
    全新綁定才預設 active。

    2026-09-21 新增 personnel_id：拿騎士自己輸入的工號去人員名冊
    （delivery_personnel）核對，核對到才存這個關聯欄位，方便後台顯示/除錯
    用（例如將來要連到人員詳細頁）。**注意：即時接單/報班媒合能不能用，
    不是靠這個欄位判斷**——那個判斷是 rider_feature_category() 每次都
    拿 employee_id 現查一次人員名冊，不會有「這裡沒即時更新」的問題；這裡
    存的 personnel_id 只是快照，工號核對不到（打錯、或這個人還沒建到
    人員名冊）時存空字串。"""
    ref = rider_bindings_ref().document(user_id)
    snapshot = ref.get()
    status = RIDER_STATUS_ACTIVE
    if snapshot.exists:
        status = (snapshot.to_dict() or {}).get("status") or RIDER_STATUS_ACTIVE
    personnel = repository.find_personnel_by_employee_no(employee_id) if employee_id else None
    ref.set(
        {
            "employee_id": employee_id,
            "name": name,
            "status": status,
            "personnel_id": personnel["id"] if personnel else "",
            "updated_at": time.time(),
        },
        merge=True,
    )


def rider_feature_category(binding: dict) -> str:
    """回傳這位騎士目前對應到人員名冊的合作方式分類（COOPERATION_CATEGORY_
    CONTRACT／COOPERATION_CATEGORY_EMPLOYED）。查不到人員資料、查無合作
    方式、或合作方式沒有設定分類，一律回傳空字串，呼叫端視同兩個功能都
    不能用——即時接單只給承攬、報班媒合只給雇傭（見 rider_events.py）。

    2026-09-21 修正：改成每次都拿 binding.employee_id 現查一次人員名冊
    （不是讀 binding.personnel_id 這個綁定當下存的快照）。原本的寫法會
    導致綁定之後才在人員名冊補工號/合作方式分類時，「騎士名單管理」畫面
    看不到最新結果，要等騎士重新綁定或管理員重跑 GAS 的
    backfillRiderBindings8() 才會更新——不符合原本設計「即時查、不是
    綁定當下寫死」的初衷，所以拿掉這個中間快照欄位的依賴。"""
    employee_id = (binding or {}).get("employee_id") or ""
    if not employee_id:
        return ""
    personnel = repository.find_personnel_by_employee_no(employee_id)
    if not personnel:
        return ""
    coop = repository.get_cooperation_type(personnel.get("cooperation_type") or "")
    if not coop:
        return ""
    return coop.get("category") or ""


def build_rider_category_map() -> dict:
    """一次建好 {工號: 合作方式分類} 的對照表，給「騎士名單管理」整頁用。

    **為什麼要有這支（效能）**：`rider_feature_category()` 是為了「查一位
    騎士」設計的，它每次都會打兩趟 Firestore（找人員名冊、讀合作方式）。
    名單頁一位騎士呼叫一次，N 位騎士就是 **2N 趟往返**，而且是一趟做完才
    做下一趟——騎士越多頁面越慢，使用者回報「這一頁點進來都要等很久」就
    是這個原因。

    改成先把人員名冊與合作方式各撈一次（共 2 趟），在記憶體裡組成對照表，
    整頁的往返次數就跟騎士人數無關了。

    **判斷規則跟 `rider_feature_category()` 完全一致**（同樣是拿工號對人員
    名冊、再看合作方式的分類），只是換一種取得方式，所以兩邊的結果一定
    相同；單筆查詢（LINE 那側一次只處理一位騎士）仍然用原本那支，不需要
    為了一個人把整份名冊撈回來。

    包含已停用的合作方式（`include_inactive=True`）：合作方式被停用不代表
    既有騎士的身份就消失了，名單上仍然要顯示得出來他是承攬還是雇傭。
    """
    category_by_type_id = {
        coop["id"]: coop.get("category") or ""
        for coop in repository.list_cooperation_types(include_inactive=True)
    }
    category_by_employee_no = {}
    for snapshot in repository.personnel_ref().stream():
        data = snapshot.to_dict() or {}
        employee_no = (data.get("employee_no") or "").strip()
        if not employee_no:
            continue
        category_by_employee_no[employee_no] = category_by_type_id.get(data.get("cooperation_type") or "", "")
    return category_by_employee_no


def rider_matches_filters(rider: dict, employee_id_filter: str = "", name_filter: str = "") -> bool:
    """騎士名單的搜尋比對（純函式）。工號跟姓名都是「包含就算符合」、
    不分大小寫——同仁常常只記得工號後面幾碼，要求打完整組不合實際。"""
    employee_id_filter = (employee_id_filter or "").strip()
    name_filter = (name_filter or "").strip()
    if employee_id_filter and employee_id_filter.upper() not in (rider.get("employee_id") or "").upper():
        return False
    if name_filter and name_filter not in (rider.get("name") or ""):
        return False
    return True


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


def set_awaiting_location(user_id: str) -> None:
    """騎士私訊「查詢附近單」關鍵字後，暫存「正在等他分享位置」。"""
    rider_bindings_ref().document(user_id).update(
        {"awaiting_location": {"set_at": time.time()}}
    )


def pop_awaiting_location(user_id: str) -> bool:
    """取出並清掉「正在等待分享位置」的暫存狀態，回傳是否真的有暫存中且
    未過期。給 rider_events.py 判斷「這則位置訊息看起來是不是真的在回覆
    查詢附近單」用——2026-09-19 使用者反映任何位置分享都會被當成相關事件
    太容易誤觸發，改成只有先問過「查詢附近單」、還在有效期限內，才算數。"""
    ref = rider_bindings_ref().document(user_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False
    pending = (snapshot.to_dict() or {}).get("awaiting_location") or {}
    set_at = pending.get("set_at") or 0
    if not set_at:
        return False
    ref.update({"awaiting_location": None})
    return (time.time() - set_at) <= RIDER_PENDING_CLAIM_TTL_SECONDS


def set_awaiting_shift_location(user_id: str) -> None:
    """騎士私訊「瀏覽報班」關鍵字後，暫存「正在等他分享位置」——2026-09-21
    新增，報班媒合開始支援「幾公里內才看得到」之後，瀏覽報班也要先知道
    騎士的位置才能篩選。跟 set_awaiting_location() 是同一種機制，但存在
    不同的欄位（awaiting_shift_location），這樣「查詢附近單」跟「瀏覽
    報班」兩種前置動作互不干擾，就算同時暫存中也不會互相蓋掉。"""
    rider_bindings_ref().document(user_id).update(
        {"awaiting_shift_location": {"set_at": time.time()}}
    )


def pop_awaiting_shift_location(user_id: str) -> bool:
    """跟 pop_awaiting_location() 是同一種邏輯，只是對應報班媒合這條線。"""
    ref = rider_bindings_ref().document(user_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False
    pending = (snapshot.to_dict() or {}).get("awaiting_shift_location") or {}
    set_at = pending.get("set_at") or 0
    if not set_at:
        return False
    ref.update({"awaiting_shift_location": None})
    return (time.time() - set_at) <= RIDER_PENDING_CLAIM_TTL_SECONDS


# ==========================================
# 地點主檔（2026-09-19 新增；2026-09-19 拆成即時接單／報班媒合兩份獨立清單）
# 門市當日量、報班時段這兩個表單原本都要同仁自己輸入經緯度／地點名稱，
# 手動查經緯度很不方便。改成主管先在「地點管理」把常用地點（含經緯度）
# 登記一次，之後表單都改用帶搜尋功能的下拉選單選現成的地點，選了之後
# 經緯度自動帶出，不用再手動輸入。跟服務區域管理／裝備品項管理同一種
# 「主管自行維護清單」模式，只是這裡多一組經緯度欄位。
#
# 即時接單（門市取貨地點）跟報班媒合（報班工作地點）原本共用同一份清單，
# 但實際上根本是兩組不同的地方，使用者反映「必須分開」——現在改成兩個
# 完全獨立的集合／CRUD 函式／後台頁面，互不影響，也不會共用同一份下拉
# 選單選項。
# ==========================================
def create_order_location(name: str, lat: float, lng: float, created_by: str) -> str:
    ref = rider_order_locations_ref().document()
    ref.set(
        {
            "name": name,
            "lat": lat,
            "lng": lng,
            "active": True,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return ref.id


def list_order_locations(include_inactive: bool = False) -> list:
    result = []
    for snapshot in rider_order_locations_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda loc: loc.get("name", ""))
    return result


def get_order_location(location_id: str):
    if not location_id:
        return None
    snapshot = rider_order_locations_ref().document(location_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def set_order_location_active(location_id: str, active: bool) -> bool:
    ref = rider_order_locations_ref().document(location_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active})
    return True


def create_shift_location(name: str, lat: float, lng: float, created_by: str) -> str:
    ref = rider_shift_locations_ref().document()
    ref.set(
        {
            "name": name,
            "lat": lat,
            "lng": lng,
            "active": True,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return ref.id


def list_shift_locations(include_inactive: bool = False) -> list:
    result = []
    for snapshot in rider_shift_locations_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda loc: loc.get("name", ""))
    return result


def get_shift_location(location_id: str):
    if not location_id:
        return None
    snapshot = rider_shift_locations_ref().document(location_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def set_shift_location_active(location_id: str, active: bool) -> bool:
    ref = rider_shift_locations_ref().document(location_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active})
    return True


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


def create_store_delivery(
    store_name: str,
    lat: float,
    lng: float,
    date_str: str,
    total_quantity: int,
    rider_capacity: int,
    created_by: str,
    radius_km: float = RIDER_DEFAULT_SEARCH_RADIUS_KM,
) -> str:
    ref = rider_store_deliveries_ref().document()
    ref.set(
        {
            "store_name": store_name,
            "lat": lat,
            "lng": lng,
            "date": date_str,
            "total_quantity": total_quantity,
            "rider_capacity": rider_capacity or DEFAULT_RIDER_CAPACITY,
            # 已經承接的騎士 LINE userId，直接存在門市當日量這份文件裡
            # （不是另外查 rider_claims）：承接的 transaction 只讀這一份
            # 文件就能同時判斷「名額滿了沒」跟「這位騎士是不是已經接過
            # 這間了」，不需要在 transaction 裡再跑一次 collection 查詢。
            # rider_claims 那邊照樣會留一筆完整紀錄（含姓名/時間）給後台
            # 對帳用。
            "claimed_rider_ids": [],
            "radius_km": radius_km or RIDER_DEFAULT_SEARCH_RADIUS_KM,
            "source": "manual",
            "status": STORE_DELIVERY_STATUS_OPEN,
            "created_by": created_by,
            "updated_by": created_by,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    )
    return ref.id


def _decorate_store_delivery(data: dict) -> dict:
    """補上畫面/訊息要用、但不直接存在文件裡的衍生欄位。舊資料沒有
    rider_capacity／claimed_rider_ids 時分別視為「需求 1 位」跟「還沒有人
    承接」（見 DEFAULT_RIDER_CAPACITY 的說明）。"""
    capacity = data.get("rider_capacity") or DEFAULT_RIDER_CAPACITY
    claimed_count = len(data.get("claimed_rider_ids") or [])
    data["rider_capacity"] = capacity
    data["claimed_rider_count"] = claimed_count
    data["remaining_rider_slots"] = max(capacity - claimed_count, 0)
    return data


def get_store_delivery(store_id: str):
    snapshot = rider_store_deliveries_ref().document(store_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return _decorate_store_delivery(data)


def list_store_deliveries(date_str: str = "") -> list:
    """後台管理用：列出（可選依日期篩選）全部門市當日量，不管開放/關閉。"""
    query = rider_store_deliveries_ref()
    if date_str:
        query = query.where("date", "==", date_str)
    items = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        items.append(_decorate_store_delivery(data))
    items.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return items


def update_store_delivery_quantities(store_id: str, total_quantity: int, rider_capacity: int, updated_by: str) -> bool:
    """後台修改這筆門市當日量的「當日量（件）」跟「需求騎士數量」。需求
    騎士數量調小到比已經承接的人數還少時不會踢掉任何人，只是後續不會再
    有人接得到（remaining_rider_slots 會是 0）。"""
    ref = rider_store_deliveries_ref().document(store_id)
    if not ref.get().exists:
        return False
    ref.update(
        {
            "total_quantity": total_quantity,
            "rider_capacity": rider_capacity,
            "updated_by": updated_by,
            "updated_at": time.time(),
        }
    )
    return True


def set_store_delivery_status(store_id: str, status: str, updated_by: str) -> bool:
    if status not in (STORE_DELIVERY_STATUS_OPEN, STORE_DELIVERY_STATUS_CLOSED):
        return False
    ref = rider_store_deliveries_ref().document(store_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status, "updated_by": updated_by, "updated_at": time.time()})
    return True


def list_nearby_open_stores(lat: float, lng: float, date_str: str, rider_id: str = "", limit: int = 8) -> list:
    """騎士查詢附近單：當日開放中、騎士名額還沒滿、而且在這筆門市自己設定的
    服務半徑（radius_km，同仁開這筆門市當日量時可以自行調整，預設
    RIDER_DEFAULT_SEARCH_RADIUS_KM）以內的門市，依距離由近到遠排序，最多
    列出 `limit` 間。算不出距離的門市（理論上不會發生，門市當日量建立時
    一定會有經緯度）不套用半徑限制，一律視為符合，避免資料異常時整筆
    憑空消失。

    帶 rider_id 時，會把這位騎士已經承接過的門市直接濾掉——重複承接本來
    就會被 _evaluate_claim() 擋下，先不要列出來比較不會讓騎士白點一次
    才看到錯誤訊息。"""
    results = []
    query = rider_store_deliveries_ref().where("date", "==", date_str).where("status", "==", STORE_DELIVERY_STATUS_OPEN)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        claimed_ids = data.get("claimed_rider_ids") or []
        if rider_id and rider_id in claimed_ids:
            continue
        capacity = data.get("rider_capacity") or DEFAULT_RIDER_CAPACITY
        if len(claimed_ids) >= capacity:
            continue
        store_lat, store_lng = data.get("lat"), data.get("lng")
        distance_km = None
        if store_lat is not None and store_lng is not None:
            distance_km = _haversine_km(lat, lng, store_lat, store_lng)
            radius_km = data.get("radius_km") or RIDER_DEFAULT_SEARCH_RADIUS_KM
            if distance_km > radius_km:
                continue
        results.append(
            {
                "id": snapshot.id,
                "store_name": data.get("store_name", ""),
                "total_quantity": data.get("total_quantity") or 0,
                "rider_capacity": capacity,
                "remaining_rider_slots": capacity - len(claimed_ids),
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


def _evaluate_claim(store_data: dict, rider_id: str):
    """純邏輯：目前這筆門市當日量的資料 + 要承接的騎士，決定接不接受。
    回傳 (是否接受, 給騎士的訊息, 接受後 claimed_rider_ids 應更新成的值)。

    2026-09-22 改版：不再比對件數，只看騎士名額（見 DEFAULT_RIDER_CAPACITY
    的說明）。同一位騎士對同一筆門市當日量重複承接會被擋下，不會重複
    佔掉名額。"""
    if store_data.get("status") != STORE_DELIVERY_STATUS_OPEN:
        return False, "這筆門市當日量已經關閉，無法承接。", None
    claimed_ids = list(store_data.get("claimed_rider_ids") or [])
    if rider_id in claimed_ids:
        return False, "您已經承接過這間門市了，請直接前往配送。", None
    capacity = store_data.get("rider_capacity") or DEFAULT_RIDER_CAPACITY
    if len(claimed_ids) >= capacity:
        return False, "這間門市需要的騎士人數已經額滿，請改承接其他門市。", None
    return True, "", claimed_ids + [rider_id]


def claim_store_delivery(store_id: str, rider_id: str, rider_name: str):
    """在單一 transaction 內完成「讀取門市當日量 → 用 _evaluate_claim() 決定
    接不接受 → 接受的話才把這位騎士加進 claimed_rider_ids、寫入一筆 claims
    紀錄」，確保兩位騎士幾乎同時承接同一筆門市當日量時不會一起超收。

    回傳 (是否成功, 給騎士的訊息, 承接結果資訊)。第三個值只有成功時才有
    內容（dict：store_name／rider_capacity／claimed_rider_count／
    remaining_rider_slots），給呼叫端推播配送群組通知用（見
    rider_events.py）。"""
    store_ref = rider_store_deliveries_ref().document(store_id)
    claim_ref = rider_claims_ref().document()
    transaction = get_db().transaction()

    @firestore.transactional
    def _txn(transaction):
        snapshot = store_ref.get(transaction=transaction)
        if not snapshot.exists:
            return False, "找不到這筆門市當日量，可能已經被下架。", None
        store_data = snapshot.to_dict() or {}
        ok, message, new_claimed_ids = _evaluate_claim(store_data, rider_id)
        if not ok:
            return False, message, None
        transaction.update(store_ref, {"claimed_rider_ids": new_claimed_ids, "updated_at": time.time()})
        transaction.set(
            claim_ref,
            {
                "store_delivery_id": store_id,
                "store_name": store_data.get("store_name", ""),
                "rider_id": rider_id,
                "rider_name": rider_name,
                "status": "active",
                "claimed_at": time.time(),
            },
        )
        store_name = store_data.get("store_name", "")
        capacity = store_data.get("rider_capacity") or DEFAULT_RIDER_CAPACITY
        info = {
            "store_name": store_name,
            "rider_capacity": capacity,
            "claimed_rider_count": len(new_claimed_ids),
            "remaining_rider_slots": max(capacity - len(new_claimed_ids), 0),
        }
        return True, f"✅ 已登記承攬請前往配送\n門市：{store_name}", info

    return _txn(transaction)


# ==========================================
# 報班媒合：需求時段
# ==========================================
def create_shift_posting(
    posted_by: str,
    location: str,
    lat: float,
    lng: float,
    start_time: float,
    end_time: float,
    capacity: int,
    radius_km: float = RIDER_DEFAULT_SEARCH_RADIUS_KM,
) -> str:
    ref = rider_shift_postings_ref().document()
    ref.set(
        {
            "posted_by": posted_by,
            "location": location,
            "lat": lat,
            "lng": lng,
            "radius_km": radius_km or RIDER_DEFAULT_SEARCH_RADIUS_KM,
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


def list_shift_postings(location: str = "", date_str: str = "") -> list:
    """後台管理用：列出全部報班時段（可選依地點/日期篩選），不管開放/
    關閉。地點/日期規模都不大，這裡用「抓全部後在程式端篩選」，跟
    repository.search_personnel() 同一種做法，不用為此另外建 Firestore
    複合索引。「已核准」「待審核」分開算給管理員看，方便核對誰還在排隊
    等審核——2026-09-21 起報名不再由系統自動比對名額決定成不成功，一律
    先存成待審核，管理員在報名名單頁面手動核准/駁回（見
    update_registration_status()）。"""
    items = []
    for snapshot in rider_shift_postings_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if location and data.get("location") != location:
            continue
        if date_str:
            start_time = data.get("start_time")
            posting_date = datetime.fromtimestamp(start_time, TAIPEI_TZ).strftime("%Y-%m-%d") if start_time else ""
            if posting_date != date_str:
                continue
        data["registered_count"] = count_registrations(snapshot.id, status=REGISTRATION_STATUS_APPROVED)
        data["pending_count"] = count_registrations(snapshot.id, status=REGISTRATION_STATUS_PENDING)
        items.append(data)
    items.sort(key=lambda d: d.get("start_time") or 0, reverse=True)
    return items


def list_open_shift_postings(lat: float = None, lng: float = None, limit: int = 8) -> list:
    """騎士瀏覽報班：列出開放中的時段。

    沒給騎士目前位置（lat/lng 皆為 None）時維持原本的行為：全部開放中
    時段、依開始時間排序，不做距離篩選（後台管理／Postback 觸發等不
    知道騎士位置的情境用這個模式）。

    有給位置時，改成只列出在這筆時段自己設定的服務半徑（radius_km，
    同仁開報班時段時可以自行調整，預設 RIDER_DEFAULT_SEARCH_RADIUS_KM）
    以內的時段，依距離由近到遠排序，最多列出 `limit` 筆——跟
    list_nearby_open_stores() 是同一套設計。算不出距離的時段（例如
    這個功能上線前就建立、還沒有經緯度的舊資料）不套用半徑限制，一律
    視為符合，避免舊資料整批憑空消失。"""
    items = []
    for snapshot in rider_shift_postings_ref().where("status", "==", SHIFT_STATUS_OPEN).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["registered_count"] = count_registrations(snapshot.id, status=REGISTRATION_STATUS_APPROVED)
        items.append(data)

    if lat is None or lng is None:
        items.sort(key=lambda d: d.get("start_time") or 0)
        return items

    results = []
    for data in items:
        shift_lat, shift_lng = data.get("lat"), data.get("lng")
        distance_km = None
        if shift_lat is not None and shift_lng is not None:
            distance_km = _haversine_km(lat, lng, shift_lat, shift_lng)
            radius_km = data.get("radius_km") or RIDER_DEFAULT_SEARCH_RADIUS_KM
            if distance_km > radius_km:
                continue
        data["distance_km"] = distance_km
        results.append(data)
    results.sort(key=lambda d: (d["distance_km"] is None, d["distance_km"]))
    return results[:limit]


def set_shift_posting_status(shift_id: str, status: str) -> bool:
    if status not in (SHIFT_STATUS_OPEN, SHIFT_STATUS_CLOSED):
        return False
    ref = rider_shift_postings_ref().document(shift_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status})
    return True


def count_registrations(shift_id: str, status: str = None) -> int:
    """status 留空回傳這個時段全部報名紀錄的筆數（不分狀態）；有給的話
    只算符合這個狀態的（例如 REGISTRATION_STATUS_APPROVED）。"""
    query = rider_shift_registrations_ref().where("shift_id", "==", shift_id)
    count = 0
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        if status and data.get("status", REGISTRATION_STATUS_APPROVED) != status:
            continue
        count += 1
    return count


def list_registrations(shift_id: str) -> list:
    items = []
    for snapshot in rider_shift_registrations_ref().where("shift_id", "==", shift_id).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        # 2026-09-21 前建立的報名紀錄沒有 status 欄位（當時是系統即時比對
        # 名額決定成不成功，能寫進 Firestore 的都代表已經確定報名成功），
        # 補上預設值時比照那個時候的語意視為「已核准」，不會讓舊資料在
        # 畫面上突然顯示成「待審核」。
        data.setdefault("status", REGISTRATION_STATUS_APPROVED)
        items.append(data)
    items.sort(key=lambda d: d.get("registered_at") or 0)
    return items


def list_registrations_by_rider(rider_id: str, limit: int = 5) -> list:
    """「查詢報名狀態」用：這位騎士最近的報名紀錄，附上對應時段的地點/
    時間，依報名時間新到舊排序，最多回傳 limit 筆。"""
    items = []
    for snapshot in rider_shift_registrations_ref().where("rider_id", "==", rider_id).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("status", REGISTRATION_STATUS_APPROVED)
        items.append(data)
    items.sort(key=lambda d: d.get("registered_at") or 0, reverse=True)
    items = items[:limit]
    for item in items:
        shift = get_shift_posting(item.get("shift_id", ""))
        item["shift_location"] = shift.get("location") if shift else "（時段已刪除）"
        item["shift_start_time"] = shift.get("start_time") if shift else None
        item["shift_end_time"] = shift.get("end_time") if shift else None
    return items


def update_registration_status(registration_id: str, status: str) -> bool:
    """管理員在報名名單頁面手動核准/駁回——這裡只負責更新狀態，不會主動
    推播 LINE 訊息給騎士，騎士要自己傳「查詢報名狀態」查詢結果（見
    rider_events.py 開頭的說明）。"""
    if status not in (REGISTRATION_STATUS_PENDING, REGISTRATION_STATUS_APPROVED, REGISTRATION_STATUS_REJECTED):
        return False
    ref = rider_shift_registrations_ref().document(registration_id)
    if not ref.get().exists:
        return False
    ref.update({"status": status, "decided_at": time.time()})
    return True


def _evaluate_registration(shift_data: dict, existing_rider_ids: list, rider_id: str):
    """純邏輯：這個時段目前的資料 + 已報名的騎士清單 + 這次要報名的騎士，
    決定接不接受這次報名請求。回傳 (是否接受, 給騎士的訊息)。

    2026-09-21 起改成人工審核制：這裡的「接受」只代表「這次報名請求有效、
    存成待審核」，不是報名成功——是否成功由管理員在報名名單頁面手動核准/
    駁回決定（見 update_registration_status()），所以不再檢查名額是否已滿
    （額滿與否交由管理員自己判斷，核准超過需求人數也可以，系統不擋）。"""
    if shift_data.get("status") != SHIFT_STATUS_OPEN:
        return False, "這個報班時段已經關閉，無法報名。"
    if rider_id in existing_rider_ids:
        return False, "您已經報名過這個時段了，可以傳「查詢報名狀態」查詢目前結果。"
    return True, "已收到您的報名！需等管理人員確認後才算報名成功，可以傳「查詢報名狀態」查詢目前結果。"


def register_shift(shift_id: str, rider_id: str, rider_name: str):
    """在單一 transaction 內完成「讀取時段 + 目前已報名的騎士清單 → 用
    _evaluate_registration() 決定接不接受這次報名請求 → 接受的話才寫入
    待審核的報名紀錄」，確保兩位騎士幾乎同時重複報名同一個時段時不會
    各自被 _evaluate_registration() 誤判成「還沒報名過」而各寫入一筆。"""
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
                "status": REGISTRATION_STATUS_PENDING,
            },
        )
        return True, message

    return _txn(transaction)
