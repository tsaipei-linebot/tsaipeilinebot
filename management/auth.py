"""管理部系統的登入檢查：跟 delivery/auth.py 是同一種薄薄一層包法，共用
根目錄 platform_accounts.py 的帳號/權限邏輯，只是把模組代碼固定成
「management」。見 delivery/auth.py 的說明註解。"""
import platform_accounts

MODULE_CODE = "management"

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
