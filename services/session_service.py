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


def _session_ref(user_id: str):
    return db.collection(SESSIONS_COLLECTION).document(user_id)


def _get_or_create_session(user_id: str) -> dict:
    """取得（或建立）使用者的對話 Session（支援軟過期：保留地點偏好）
    改為讀寫 Firestore，取代原本的行程記憶體 dict，
    確保 Cloud Run 多 instance / 重啟後資料不會遺失或不一致。
    """
    now = time.time()
    ref = _session_ref(user_id)
    snapshot = ref.get()

    if snapshot.exists:
        session = snapshot.to_dict() or {}
        session.setdefault("slots", dict(DEFAULT_SLOTS))
        session.setdefault("messages", [])

        if now - session.get("last_time", 0) < SESSION_TTL:
            session["last_time"] = now
            ref.update({"last_time": now})
            return session
        else:
            # 軟過期（Soft Expiration）：超時清空對話歷程，但保留使用者最後鎖定的地點偏好
            old_loc = session.get("slots", {}).get("location", "")
            new_slots = dict(DEFAULT_SLOTS)
            new_slots["location"] = old_loc
            session = {"last_time": now, "messages": [], "slots": new_slots}
            ref.set(session)
            return session

    # 使用者第一次出現，建立新的 session 文件
    session = {"last_time": now, "messages": [], "slots": dict(DEFAULT_SLOTS)}
    ref.set(session)
    return session


def get_user_history(user_id: str) -> list:
    return _get_or_create_session(user_id)["messages"]


def get_user_slots(user_id: str) -> dict:
    return _get_or_create_session(user_id)["slots"]


def update_user_slots(user_id: str, location: str = "", category: str = "", shift: str = "", leave: str = "", brand: str = "") -> dict:
    """更新使用者的已知需求條件"""
    session = _get_or_create_session(user_id)
    slots = session["slots"]
    if location:
        slots["location"] = location
    if category:
        slots["category"] = category
    if shift:
        slots["shift"] = shift
    if leave:
        slots["leave"] = leave
    if brand is not None and brand != "":
        slots["brand"] = brand

    _session_ref(user_id).update({"slots": slots})
    return slots


def clear_user_slots(user_id: str) -> dict:
    """清空使用者的已知需求條件（槽位重置）"""
    _get_or_create_session(user_id)  # 確保文件存在
    new_slots = dict(DEFAULT_SLOTS)
    _session_ref(user_id).update({"slots": new_slots})
    return new_slots


def append_user_history(user_id: str, role: str, text: str):
    session = _get_or_create_session(user_id)
    history = session["messages"]
    history.append({"role": role, "text": text})
    if len(history) > 10:
        history.pop(0)
    _session_ref(user_id).update({"messages": history})
