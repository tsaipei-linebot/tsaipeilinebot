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

除了模組權限，每個帳號還有兩個跟「部門模組」無關、給「我的專區」這類個人化
功能共用的欄位：
- manager_usernames：這個帳號的主管，存的是「別的帳號的 username」清單
  （不是文字姓名），可以有多個。之所以存帳號而不是姓名，是為了避免同名
  同姓或姓名打法不一致造成比對錯誤——之前 /me 的薪資補款紀錄就是靠外部
  Google Sheet 的文字姓名比對主管關係，容易出這種問題，這裡改成參照系統
  自己的帳號，資料由這個系統自己管理維護。
- department：部門，自由文字，目前只是存起來備用，還沒有功能會用到，
  之後如果要做「同部門互看」這類功能可以直接用。
"""
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

from platform_db import get_db, users_ref

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
    {"code": "hr", "name": "人資專區"},
    {"code": "salesdev", "name": "少凱業務開發專區"},
    {"code": "job_listings", "name": "職缺維護"},
    {"code": "project_contracts", "name": "專案合約維護"},
    {"code": "chicken_points", "name": "小雞點數自費申請"},
    {"code": "dispatch_contracts", "name": "派遣契約產生器"},
    {"code": "client_contracts", "name": "合約產生器"},
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
        # 所屬主管（帳號清單，不是文字姓名）跟部門：給「主管能看部屬資料」
        # 這類跨模組功能共用（例如 /me 的薪資補款紀錄），比對用帳號本身，
        # 不是靠文字姓名比對，才不會有同名同姓或姓名打法不一致的問題。
        "manager_usernames": data.get("manager_usernames", []) or [],
        "department": data.get("department", "") or "",
        # 同部門內手動拖曳排序用（見 /accounts 帳號權限管理），沒被拖曳過
        # 的帳號是 None，排序時當成「還沒排」，放在有排過的人後面、依姓名
        # 排——新帳號一律 None，所以天生就會排在該部門最後面，不用另外
        # 處理「新帳號插入哪裡」的邏輯。
        "sort_index": data.get("sort_index"),
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
    """回傳全部帳號，排序規則：先依部門分組（部門名稱字母順序），組內
    「有手動排過序」的帳號依排序數字排在前面，「還沒排過」的帳號依姓名
    排在後面——見 `_to_account()` 的 `sort_index` 說明、`reorder_department()`
    的拖曳存檔邏輯。"""
    result = [_to_account(s.id, s.to_dict() or {}) for s in users_ref().stream()]

    def sort_key(a):
        has_index = a["sort_index"] is not None
        return (a["department"], 0 if has_index else 1, a["sort_index"] if has_index else a["name"])

    result.sort(key=sort_key)
    return result


def reorder_department(department: str, ordered_usernames: list) -> None:
    """儲存「帳號權限管理」頁面拖曳排序後的結果：`ordered_usernames` 是
    這個部門裡的帳號 username，依畫面上排好的新順序給。只會更新「真的
    屬於這個部門」的帳號，其餘一律忽略——避免呼叫端傳入不相干的
    username（或帳號剛好被改了部門、被刪除）意外改到不該動的排序，
    不完全信任前端送來的內容。"""
    valid_usernames = {a["username"] for a in list_accounts() if a["department"] == department}
    batch = get_db().batch()
    index = 0
    for username in ordered_usernames:
        if username not in valid_usernames:
            continue
        batch.update(users_ref().document(username), {"sort_index": index})
        index += 1
    batch.commit()


def create_account(
    username: str, password: str, name: str, modules: dict,
    manager_usernames: list = None, department: str = "",
):
    users_ref().document(username).set(
        {
            "password_hash": hash_password(password),
            "name": name,
            "modules": modules,
            "manager_usernames": manager_usernames or [],
            "department": department,
            "is_platform_admin": False,
            "created_at": time.time(),
        }
    )


def update_account(
    username: str, name: str, modules: dict, password: str = "",
    manager_usernames: list = None, department: str = "",
):
    """password 空字串代表不改密碼。modules／manager_usernames 整包覆蓋
    （畫面上的表單一次會送出所有值，包含「不開放」/「沒有勾選」，所以用
    覆蓋而不是合併新增）。"""
    payload = {
        "name": name,
        "modules": modules,
        "manager_usernames": manager_usernames or [],
        "department": department,
    }
    if password:
        payload["password_hash"] = hash_password(password)
    users_ref().document(username).update(payload)


def set_manager_usernames(username: str, manager_usernames: list):
    """只給一次性遷移腳本（例如 scripts/import_account_managers.py）呼叫的
    窄範圍更新，只動 manager_usernames 這個欄位，不會不小心動到帳密／
    modules／department 等其他資料。"""
    users_ref().document(username).update({"manager_usernames": manager_usernames})


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
