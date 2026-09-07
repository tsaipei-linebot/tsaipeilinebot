"""少凱業務開發專區（/salesdev）：把「派遣客戶開發名單、新登記工廠監控
彙整」這份 Google Sheet 的內容，用網頁表格呈現。取代原本 /portal 首頁卡片
直接連去 Google Sheet 編輯畫面的做法——這裡是唯讀網頁，不會有人不小心
改到原始資料，畫面也比試算表好讀。

跟 delivery/management/hr 不同，這個模組資料量小、只是唯讀彙整，不需要
獨立掛載一個子系統，直接掛在根 app 上、複用同一顆登入 session cookie
（比照 portal_routes.py／accounts_routes.py 的做法）。是否看得到這張卡片、
能不能進來這個頁面，跟其他部門模組一樣由 /accounts 的權限設定決定。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.salesdev_sheet_service import fetch_sheet_tabs

router = APIRouter()

MODULE_CODE = "salesdev"


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


@router.get("/salesdev")
def salesdev_home(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    tabs, error = fetch_sheet_tabs()
    return templates.TemplateResponse(
        request,
        "salesdev_home.html",
        {
            "user": platform_accounts.current_account(request),
            "tabs": tabs,
            "error": error,
        },
    )
