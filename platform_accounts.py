"""跨部門模組共用的帳號驗證與權限邏輯。

平台底下每個部門都是獨立掛載的子系統（配送部在 /delivery、管理部在
/management…之後還會有更多），但帳號是共用的一份：一個人可能同時有好幾個
部門的權限，登入一次、在部門之間切換不用重新輸入密碼（各子系統的
SessionMiddleware 用同一組 secret key + cookie 名稱，瀏覽器端就是同一顆
cookie）。

權限分三層：
- is_platform_admin：全平台只會有一個人（老闆本人），可以指派所有帳號在
  各部門的權限。刻意不開放透過網頁表單修改這個旗標，只能透過部署時的
  seed_admin／migrate 腳本設定，避免這麼關鍵的權限被誤觸或被權限管理頁面
  本身的漏洞連帶波及。
- modules[code] == "admin"：該部門的主管，除了看得到該部門的功能，還可以
  操作只開放管理員的動作（核准、結案、發公告這類）。
- modules[code] == "staff"：該部門的一般同仁，看得到但不能做管理員限定的
  操作。
- 完全不在 modules 裡（也不是 is_platform_admin）：看不到該部門，連首頁
  都會被導去 /portal，不會看到權限錯誤訊息。
"""
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

from platform_db import users_ref

PBKDF2_ITERATIONS = 200_000

ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
MODULE_ROLES = [
    {"code": ROLE_STAFF, "name": "專員"},
    {"code": ROLE_ADMIN, "name": "主管"},
]
MODULE_ROLE_MAP = {r["code"]: r["name"] for r in MODULE_ROLES}

# 目前平台掛載的部門模組。之後每加一個新部門，只要在這裡多加一筆，帳號
# 權限管理頁面（/accounts）就會自動多一欄可以勾選，不用再改權限邏輯本身。
MODULES = [
    {"code": "delivery", "name": "配送部系統"},
    {"code": "management", "name": "管理部"},
]
MODULE_MAP = {m["code"]: m["name"] for m in MODULES}


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


def _to_account(username: str, data: dict) -> dict:
    return {
        "username": username,
        "name": data.get("name", username),
        "modules": data.get("modules", {}) or {},
        "is_platform_admin": bool(data.get("is_platform_admin", False)),
    }


def authenticate(username: str, password: str):
    """帳密正確時回傳帳號 dict（不含密碼雜湊），否則回傳 None。這裡不檢查
    對任何特定模組有沒有權限——那是登入之後，各模組自己的
    require_module_access/require_module_admin 才會檢查的事。"""
    if not username or not password:
        return None
    snapshot = users_ref().document(username).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    if not verify_password(password, data.get("password_hash", "")):
        return None
    return _to_account(username, data)


def get_account(username: str):
    snapshot = users_ref().document(username).get()
    if not snapshot.exists:
        return None
    return _to_account(username, snapshot.to_dict() or {})


def account_exists(username: str) -> bool:
    return users_ref().document(username).get().exists


def list_accounts() -> list:
    result = [_to_account(s.id, s.to_dict() or {}) for s in users_ref().stream()]
    result.sort(key=lambda a: a["username"])
    return result


def create_account(username: str, password: str, name: str, modules: dict):
    users_ref().document(username).set(
        {
            "password_hash": hash_password(password),
            "name": name,
            "modules": modules,
            "is_platform_admin": False,
            "created_at": time.time(),
        }
    )


def update_account(username: str, name: str, modules: dict, password: str = ""):
    """password 空字串代表不改密碼。modules 整包覆蓋（畫面上的表單一次會送出
    所有模組的下拉選單值，包含「不開放」，所以用覆蓋而不是合併新增）。"""
    payload = {"name": name, "modules": modules}
    if password:
        payload["password_hash"] = hash_password(password)
    users_ref().document(username).update(payload)


def delete_account(username: str):
    users_ref().document(username).delete()


def set_platform_admin(username: str, is_admin: bool):
    """只給 seed_admin/migrate 這類命令列腳本呼叫，網頁表單不開放這個操作。"""
    users_ref().document(username).update({"is_platform_admin": is_admin})


def validate_account_deletion(username: str, current_username: str, target_is_platform_admin: bool) -> str:
    """回傳空字串代表可以刪除；非空字串是不能刪除的原因代碼：
    - "self"：不能刪除自己的帳號，避免刪完自己被鎖在外面。
    - "platform_admin"：不能刪除擁有全平台管理權限的帳號（要換人的話，先用
      腳本把權限轉移給別的帳號，再回來刪除這一組）。
    """
    if username == current_username:
        return "self"
    if target_is_platform_admin:
        return "platform_admin"
    return ""


def module_role(account: dict, module_code: str):
    """回傳這個帳號在指定模組的角色代碼（"admin"/"staff"），完全沒有權限
    回傳 None。全平台管理員視同任何模組的管理員。"""
    if not account:
        return None
    if account.get("is_platform_admin"):
        return ROLE_ADMIN
    return account.get("modules", {}).get(module_code)


def has_module_access(account: dict, module_code: str) -> bool:
    return module_role(account, module_code) is not None


def current_account(request: Request):
    return request.session.get("user")


def require_module_access(module_code: str):
    """FastAPI 路由依賴工廠：沒登入導去該模組登入頁；登入了但沒這個模組的
    權限導回 /portal（而不是丟 403），同仁看到「回主頁選系統」比看到權限
    錯誤代碼更容易理解怎麼處理。"""

    def _dependency(request: Request):
        account = current_account(request)
        if not account:
            return RedirectResponse(url=f"/{module_code}/login", status_code=303)
        if not has_module_access(account, module_code):
            return RedirectResponse(url="/portal", status_code=303)
        return None

    return _dependency


def require_module_admin(module_code: str):
    """FastAPI 路由依賴工廠：該模組管理員限定的操作。未登入導去登入頁；
    已登入但不是這個模組的管理員（也不是全平台管理員）一律導回該模組主頁。"""

    def _dependency(request: Request):
        account = current_account(request)
        if not account:
            return RedirectResponse(url=f"/{module_code}/login", status_code=303)
        if module_role(account, module_code) != ROLE_ADMIN:
            return RedirectResponse(url=f"/{module_code}/", status_code=303)
        return None

    return _dependency


def require_platform_admin(request: Request):
    """FastAPI 路由依賴：帳號權限管理（/accounts）只開放全平台管理員（目前
    就是老闆本人這一組帳號）。未登入或不是全平台管理員一律導回 /portal，
    不暴露這個路徑存在。"""
    account = current_account(request)
    if not account or not account.get("is_platform_admin"):
        return RedirectResponse(url="/portal", status_code=303)
    return None
