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


def get_user(username: str):
    snapshot = users_ref().document(username).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    return {"username": username, "name": data.get("name", username), "role": data.get("role", "staff")}


def user_exists(username: str) -> bool:
    return users_ref().document(username).get().exists


def list_users() -> list:
    result = []
    for snapshot in users_ref().stream():
        data = snapshot.to_dict() or {}
        result.append({"username": snapshot.id, "name": data.get("name", snapshot.id), "role": data.get("role", "staff")})
    result.sort(key=lambda u: u["username"])
    return result


def count_admins() -> int:
    return sum(1 for _ in users_ref().where("role", "==", "admin").stream())


def delete_user(username: str):
    users_ref().document(username).delete()


def validate_user_deletion(username: str, current_username: str, target_role: str, admin_count: int) -> str:
    """回傳空字串代表可以刪除；非空字串是不能刪除的原因代碼，給路由轉成對應
    的錯誤訊息用：
    - "self"：不能刪除自己的帳號，避免刪完自己被鎖在外面。
    - "last_admin"：至少要保留一組管理員帳號，不然沒有人可以再管理帳號了。
    """
    if username == current_username:
        return "self"
    if target_role == "admin" and admin_count <= 1:
        return "last_admin"
    return ""


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


def admin_required(request: Request):
    """FastAPI 路由依賴：帳號管理只開放給 role=="admin" 的人。未登入導去登入
    頁；已登入但不是管理員一律導回主頁（不是丟 403），避免一般同仁看到陌生的
    錯誤頁。"""
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/delivery/login", status_code=303)
    if user.get("role") != "admin":
        return RedirectResponse(url="/delivery/", status_code=303)
    return None
