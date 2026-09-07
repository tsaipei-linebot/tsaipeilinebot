"""人資專區的登入檢查：跟 delivery/auth.py、management/auth.py 是同一種薄薄
一層包法，共用根目錄 platform_accounts.py 的帳號/權限邏輯，只是把模組代碼
固定成「hr」。見 delivery/auth.py 的說明註解。"""
import platform_accounts

MODULE_CODE = "hr"

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
