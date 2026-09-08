import time
from google.cloud import firestore
from config import SESSION_TTL, GCP_PROJECT_ID

# ==========================================
# Firestore 客戶端初始化
# 沿用 Cloud Run 服務帳戶的 IAM 權限，不需另外提供金鑰
# database="(default)" 對應第一步在 Console 建立時使用的預設資料庫 ID
# ==========================================
db = firestore.Client(project=GCP_PROJECT_ID, database="(default)")

SESSIONS_COLLECTION = "user_sessions"

DEFAULT_SLOTS = {"location": "", "category": "", "shift": "", "leave": "", "brand": ""}

# ==========================================
# 槽位三態機制的「清除」訊號
# 呼叫端傳這個常數，代表使用者明確表示「不限/取消」該維度，
# 跟「這句話沒提到、維持原值」（傳 "" 或不傳）要區分開來，
# 否則像「品牌不限都可以」這種話，slots 會因為抽不到具體品牌名稱而永遠卡在舊值。
# ==========================================
CLEAR_SLOT = "__CLEAR__"


def _session_ref(user_id: str):
    return db.collection(SESSIONS_COLLECTION).document(user_id)


def _default_session(now: float) -> dict:
    return {"last_time": now, "messages": [], "slots": dict(DEFAULT_SLOTS)}


def _normalize_session(raw: dict, now: float) -> tuple:
    """把 Firestore 讀回來的原始資料，正規化成保證有 slots/messages 兩個欄位的
    session dict（新使用者、或欄位還沒補上的舊資料都適用），並套用軟過期規則：
    超過 SESSION_TTL 就清空對話歷程，但保留使用者最後鎖定的地點偏好。純邏輯，
    不含任何 Firestore 讀寫，方便單元測試。

    回傳 (session, was_fresh)：was_fresh 為 False 代表這次讀取觸發了軟過期重置
    （或原本就不存在），呼叫端會需要整份覆寫（set）而不是局部更新（update）。"""
    if raw is None:
        return _default_session(now), False

    session = dict(raw)
    session.setdefault("slots", dict(DEFAULT_SLOTS))
    session.setdefault("messages", [])

    if now - session.get("last_time", 0) >= SESSION_TTL:
        old_loc = session.get("slots", {}).get("location", "")
        new_slots = dict(DEFAULT_SLOTS)
        new_slots["location"] = old_loc
        return {"last_time": now, "messages": [], "slots": new_slots}, False

    return session, True


def _merge_slot_updates(slots: dict, location: str = "", category: str = "", shift: str = "", leave: str = "", brand: str = "") -> dict:
    """三態機制的純邏輯部分：
    - 傳入空字串或不傳：這句話沒提到這個維度，維持原值
    - 傳入 CLEAR_SLOT：使用者明確表示不限/取消，清空該維度
    - 傳入其他非空字串：設定為該值
    回傳新的 dict，不修改傳入的 slots，方便在 Firestore transaction 內外重複使用，
    也方便不接真的 Firestore 就能單元測試。"""
    new_slots = dict(slots)
    for key, value in [
        ("location", location),
        ("category", category),
        ("shift", shift),
        ("leave", leave),
        ("brand", brand),
    ]:
        if not value:
            continue
        elif value == CLEAR_SLOT:
            new_slots[key] = ""
        else:
            new_slots[key] = value
    return new_slots


def _append_history_entry(messages: list, role: str, text: str, max_len: int = 10) -> list:
    """把一則對話加進歷史紀錄，超過上限時砍掉最舊的一則，回傳新的 list（純邏輯）。"""
    new_messages = list(messages) + [{"role": role, "text": text}]
    if len(new_messages) > max_len:
        new_messages = new_messages[-max_len:]
    return new_messages


def _get_or_create_session(user_id: str) -> dict:
    """取得（或建立）使用者的對話 Session（支援軟過期：保留地點偏好），
    純讀取用途（見 update_user_slots/append_user_history/clear_user_slots 的
    transaction 版本），只在需要時把 last_time 往前推進，避免過期判斷失準。"""
    now = time.time()
    ref = _session_ref(user_id)
    snapshot = ref.get()
    session, was_fresh = _normalize_session(snapshot.to_dict() if snapshot.exists else None, now)

    if was_fresh:
        ref.update({"last_time": now})
    else:
        ref.set(session)

    return session


def get_user_history(user_id: str) -> list:
    return _get_or_create_session(user_id)["messages"]


def get_user_slots(user_id: str) -> dict:
    return _get_or_create_session(user_id)["slots"]


def _run_in_transaction(user_id: str, mutate):
    """在單一 Firestore transaction 內完成「讀取目前 session → 用 mutate() 算出新的
    內容 → 寫回」，確保同一個使用者短時間內多筆並發請求（例如使用者連續快速傳
    兩則訊息、或第一則訊息的 AI 決策還在背景算的時候又傳了第二則——這在「限時
    同步等待＋逾時後背景補發」的架構下並不罕見）不會互相蓋掉對方的更新。

    改版前的做法是「各自獨立讀一次、改一次、寫一次」，兩個並發請求如果讀到同一份
    舊資料，後寫入的會整份覆蓋掉先寫入的，等於遺失其中一次更新（例如求職者剛設定
    的地區被稍晚處理完的另一則訊息覆蓋消失、或某一則對話沒有被記錄進歷史）。上線
    前抓流量問題時發現這個風險：現有 `scripts/load_test.py` 預設把多個模擬請求
    分配到同一批 `--distinct-users`，剛好會反覆觸發這個情境，只是原本沒有另外
    檢查 Firestore 資料本身有沒有遺失，只看回應時間跟狀態碼，所以先前沒被抓到。

    Firestore transaction 遇到並發衝突會自動重試（預設 5 次），讀-改-寫視為一個
    不可分割的整體，才能保證任何時候只會有一次更新真的生效、不會憑空消失。

    mutate(session: dict) -> Any：在讀到的 session dict 上原地修改（例如設定
    session["slots"]、session["messages"]），回傳要給呼叫端的值。"""
    ref = _session_ref(user_id)
    transaction = db.transaction()

    @firestore.transactional
    def _txn(transaction):
        now = time.time()
        snapshot = ref.get(transaction=transaction)
        session, _ = _normalize_session(snapshot.to_dict() if snapshot.exists else None, now)
        result = mutate(session)
        session["last_time"] = now
        transaction.set(ref, session)
        return result

    return _txn(transaction)


def update_user_slots(user_id: str, location: str = "", category: str = "", shift: str = "", leave: str = "", brand: str = "") -> dict:
    def _mutate(session):
        session["slots"] = _merge_slot_updates(session["slots"], location, category, shift, leave, brand)
        return session["slots"]

    return _run_in_transaction(user_id, _mutate)


def clear_user_slots(user_id: str) -> dict:
    """清空使用者的已知需求條件（槽位重置）"""
    def _mutate(session):
        session["slots"] = dict(DEFAULT_SLOTS)
        return session["slots"]

    return _run_in_transaction(user_id, _mutate)


def append_user_history(user_id: str, role: str, text: str):
    def _mutate(session):
        session["messages"] = _append_history_entry(session["messages"], role, text)
        return session["messages"]

    return _run_in_transaction(user_id, _mutate)
