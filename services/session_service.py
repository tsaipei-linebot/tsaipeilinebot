import time
from config import SESSION_TTL

user_sessions = {}

def _get_or_create_session(user_id: str) -> dict:
    """取得（或建立）使用者的對話 Session[cite: 2]"""
    now = time.time()
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if now - session["last_time"] < SESSION_TTL:
            session["last_time"] = now
            return session
            
    user_sessions[user_id] = {
        "last_time": now,
        "messages": [],
        # 漸進式需求收集 (Slot-Filling)：地區 / 工作類別 / 時段班別 / 休假方式 / 指定廠商[cite: 2]
        "slots": {"location": "", "category": "", "shift": "", "leave": "", "brand": ""}
    }
    return user_sessions[user_id]

def get_user_history(user_id: str) -> list:
    return _get_or_create_session(user_id)["messages"]

def get_user_slots(user_id: str) -> dict:
    return _get_or_create_session(user_id)["slots"]

def update_user_slots(user_id: str, location: str = "", category: str = "", shift: str = "", leave: str = "", brand: str = "") -> dict:
    """更新使用者的已知需求條件[cite: 2]"""
    slots = get_user_slots(user_id)
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
    return slots

def clear_user_slots(user_id: str) -> dict:
    """清空使用者的已知需求條件（槽位重置）"""
    session = _get_or_create_session(user_id)
    session["slots"] = {"location": "", "category": "", "shift": "", "leave": "", "brand": ""}
    return session["slots"]

def append_user_history(user_id: str, role: str, text: str):
    history = get_user_history(user_id)
    history.append({"role": role, "text": text})
    if len(history) > 10:
        history.pop(0)
