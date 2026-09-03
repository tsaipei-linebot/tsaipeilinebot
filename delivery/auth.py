"""帳號密碼登入：雜湊/驗證邏輯 + session 存取小工具。

密碼雜湊只用標準函式庫的 hashlib.pbkdf2_hmac（200,000 次疊代 + 每組帳號
各自隨機 salt），刻意不引入 passlib/bcrypt 這類第三方套件——這個系統的
使用者是配送部同仁，帳號數量小，不需要為此多背一個原生編譯依賴。
"""
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

from delivery.db import users_ref

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
    except (ValueError, AttributeError):
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def authenticate(username: str, password: str):
    """帳密正確時回傳使用者 dict（不含密碼雜湊），否則回傳 None。"""
    if not username or not password:
        return None
    snapshot = users_ref().document(username).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    if not verify_password(password, data.get("password_hash", "")):
        return None
    return {"username": username, "name": data.get("name", username), "role": data.get("role", "staff")}


def create_user(username: str, password: str, name: str, role: str = "staff"):
    users_ref().document(username).set(
        {
            "password_hash": hash_password(password),
            "name": name,
            "role": role,
            "created_at": time.time(),
        }
    )


def current_user(request: Request):
    return request.session.get("user")


def login_required(request: Request):
    """FastAPI 路由依賴：未登入時導回登入頁，而不是丟 401。

    回傳值是 None（代表已登入，呼叫端可以再用 current_user() 取資料）或
    一個 RedirectResponse——路由函式收到非 None 就直接回傳它即可短路。
    """
    if not current_user(request):
        return RedirectResponse(url="/delivery/login", status_code=303)
    return None
