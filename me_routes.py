"""我的專區（/me）：登入後每個帳號都自動有的個人化頁面。

跟部門模組（/delivery、/management、/hr）或功能模組不一樣——這裡不是「這個
帳號能不能打開這個頁面」的權限問題（任何登入的帳號都能打開），而是「頁面
裡的資料哪些是這個人可以看的」，由各個小工具自己的服務模組決定資料怎麼
篩選（目前只有薪資補款紀錄一項，見 services/salary_repayment_service.py）。
之後如果要加其他跟個人相關的資訊，比照同樣的寫法各自加一個小工具、在這裡
多組一段資料即可，不需要改動這裡的權限判斷。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.salary_repayment_service import DISPLAY_COLUMNS, get_my_repayment_records

router = APIRouter()


def _require_login(request: Request):
    if not platform_accounts.current_account(request):
        return RedirectResponse(url="/login?next=/me", status_code=303)
    return None


@router.get("/me")
def my_zone(request: Request, redirect=Depends(_require_login)):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    records, error = get_my_repayment_records(account["name"])
    return templates.TemplateResponse(
        request,
        "me_home.html",
        {
            "user": account,
            "salary_repayment_columns": DISPLAY_COLUMNS,
            "salary_repayment_records": records,
            "salary_repayment_error": error,
        },
    )
