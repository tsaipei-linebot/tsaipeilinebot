"""配送部系統的登入檢查：這支模組本身不再存密碼/帳號邏輯，那些已經搬到
根目錄的 platform_accounts.py（全平台共用，帳號可能同時橫跨好幾個部門）。
這裡只是薄薄一層，把「delivery」這個模組代碼固定下來，並且：

1. 保留 login_required / admin_required / current_user / authenticate 這幾個
   名字，讓既有的路由檔案（vehicle_routes.py、incident_routes.py……）完全
   不用改匯入。
2. current_user() 額外算出一個 role 欄位（"admin"/"staff"，只反映這個帳號
   在「配送部」這個模組的角色），維持既有樣板（base.html、incident_detail.
   html 等）裡 `user.role == "admin"` 這種寫法繼續有效，不用逐一改樣板。
"""
import platform_accounts

MODULE_CODE = "delivery"

authenticate = platform_accounts.authenticate
login_required = platform_accounts.require_module_access(MODULE_CODE)
admin_required = platform_accounts.require_module_admin(MODULE_CODE)


def current_user(request):
    account = platform_accounts.current_account(request)
    if not account:
        return None
    enriched = dict(account)
    role = platform_accounts.module_role(account, MODULE_CODE)
    enriched["role"] = platform_accounts.ROLE_ADMIN if role == platform_accounts.ROLE_ADMIN else platform_accounts.ROLE_STAFF
    return enriched
