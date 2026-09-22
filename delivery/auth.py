"""配送部系統的登入檢查：這支模組本身不再存密碼/帳號邏輯，那些已經搬到
根目錄的 platform_accounts.py（全平台共用，帳號可能同時橫跨好幾個部門）。
這裡只是薄薄一層，把「delivery」這個模組代碼固定下來，並且：

1. 保留 login_required / admin_required / current_user / authenticate 這幾個
   名字，讓既有的路由檔案（vehicle_routes.py、incident_routes.py……）完全
   不用改匯入。
2. current_user() 額外算出一個 role 欄位（"admin"/"staff"，只反映這個帳號
   在「配送部」這個模組的角色），維持既有樣板（base.html、incident_detail.
   html 等）裡 `user.role == "admin"` 這種寫法繼續有效，不用逐一改樣板。

**2026-09-22 起 `login_required` 改成「模組打勾 or 部門字串」兩者符合其一
即可**（跟財務部/桃園所/高雄所/加退保同一套做法，見
`has_delivery_access()` 說明），不再只認模組打勾——原本只有 `/accounts`
手動勾選「新北所(配送組)系統」的帳號才進得去，部門明明就是「新北所
(配送組)」卻還要另外開通模組權限，跟加退保之前踩過的落差是同一種問題。
既有已經勾模組權限的帳號完全不受影響；沒勾模組但部門符合的帳號現在也
進得去，在這裡算的 role 一律是 staff（`module_role()` 對這種帳號回傳
None，`current_user()` 原本的 fallback 就是 staff，不用另外改）。
"""
from fastapi import Request
from fastapi.responses import RedirectResponse

import platform_accounts

MODULE_CODE = "delivery"
DEPARTMENT = "新北所(配送組)"

authenticate = platform_accounts.authenticate
admin_required = platform_accounts.require_module_admin(MODULE_CODE)


def has_delivery_access(account: dict) -> bool:
    if not account:
        return False
    if platform_accounts.has_module_access(account, MODULE_CODE):
        return True
    return platform_accounts.normalize_department(account.get("department")) == platform_accounts.normalize_department(
        DEPARTMENT
    )


def login_required(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/{MODULE_CODE}/login", status_code=303)
    if not has_delivery_access(account):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def current_user(request):
    account = platform_accounts.current_account(request)
    if not account:
        return None
    enriched = dict(account)
    role = platform_accounts.module_role(account, MODULE_CODE)
    enriched["role"] = platform_accounts.ROLE_ADMIN if role == platform_accounts.ROLE_ADMIN else platform_accounts.ROLE_STAFF
    return enriched
