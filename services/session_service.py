import time
from config import SESSION_TTL

user_sessions = {}

def _get_or_create_session(user_id: str) -> dict:
    """取得（或建立）使用者的對話 Session，內含歷史訊息與已收集到的需求條件 (slots)[cite: 2]。"""
    now = time.time()
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if now - session["last_time"] < SESSION_TTL:
            session["last_time"] = now
            return session
            
    user_sessions[user_id] = {
        "last_time": now,
        "messages": [],
        # 漸進式需求收集 (Slot-Filling)：地區 / 工作類別(行業別) / 時段班別[cite: 2]
        "slots": {"location": "", "category": "", "shift": ""}
    }
    return user_sessions[user_id]

def get_user_history(user_id: str) -> list:
    return _get_or_create_session(user_id)["messages"]

def get_user_slots(user_id: str) -> dict:
    """回傳使用者目前已被顧問掌握的需求條件（地區/類別/時段）[cite: 2]。"""
    return _get_or_create_session(user_id)["slots"]

def update_user_slots(user_id: str, location: str = "", category: str = "", shift: str = "") -> dict:
    """更新使用者的已知需求條件，只有傳入非空值才會覆寫，避免把已掌握的條件洗掉[cite: 2]。"""
    slots = get_user_slots(user_id)
    if location:
        slots["location"] = location
    if category:
        slots["category"] = category
    if shift:
        slots["shift"] = shift
    return slots

def append_user_history(user_id: str, role: str, text: str):
    history = get_user_history(user_id)
    history.append({"role": role, "text": text})
    if len(history) > 10:
        history.pop(0)