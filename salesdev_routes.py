"""少凱業務開發專區（/salesdev）：把「派遣客戶開發名單、新登記工廠監控
彙整」這份 Google Sheet 的內容，用網頁表格呈現。取代原本 /portal 首頁卡片
直接連去 Google Sheet 編輯畫面的做法——這裡是唯讀網頁，不會有人不小心
改到原始資料，畫面也比試算表好讀。

跟 delivery/management/hr 不同，這個模組資料量小、只是唯讀彙整，不需要
獨立掛載一個子系統，直接掛在根 app 上、複用同一顆登入 session cookie
（比照 portal_routes.py／accounts_routes.py 的做法）。是否看得到這張卡片、
能不能進來這個頁面，跟其他部門模組一樣由 /accounts 的權限設定決定。

2026-09-17 新增：「勾選要反查的職缺」功能。這份 Google Sheet 裡如果有分頁
含「審查狀態」欄位（目前是 tsaipei-linebot-recruitment-leads-scraper 這個
抓職缺程式寫入的「Leads」分頁），畫面上會讓使用者勾選「待審查」的職缺，
送出後把這些列的狀態改成「已勾選待反查」，之後由另一個獨立的每日反查
排程（不在這個 repo 裡）讀取「已勾選待反查」的列去執行實際的反查。這裡
只負責「勾選、寫回狀態」，不執行任何反查邏輯。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates
from services.salesdev_sheet_service import fetch_sheet_tabs, mark_rows_selected_for_reverse_lookup

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
def salesdev_home(
    request: Request,
    selected: str = "",
    select_error: str = "",
    redirect=Depends(_require_access),
):
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
            "selected_count": selected,
            "select_error": select_error,
        },
    )


@router.post("/salesdev/select")
async def salesdev_select(request: Request, redirect=Depends(_require_access)):
    """使用者在畫面上勾選職缺後送出：把這些列的「審查狀態」改成
    「已勾選待反查」。用 request.form() 而不是宣告 Form(...) 參數，是因為
    `row_numbers` 是數量不固定的勾選框（0 到多個），FastAPI 的 Form 語法
    處理「同名多值」不如直接讀原始表單資料直觀。
    """
    if redirect:
        return redirect
    form = await request.form()
    tab_title = form.get("tab_title", "")
    row_numbers = []
    for raw_value in form.getlist("row_numbers"):
        try:
            row_numbers.append(int(raw_value))
        except (TypeError, ValueError):
            continue

    if not tab_title or not row_numbers:
        return RedirectResponse(
            url=f"/salesdev?select_error={quote('請至少勾選一筆職缺再送出。')}", status_code=303
        )

    updated_count, error = mark_rows_selected_for_reverse_lookup(tab_title, row_numbers)
    if error:
        return RedirectResponse(url=f"/salesdev?select_error={quote(error)}", status_code=303)
    return RedirectResponse(url=f"/salesdev?selected={updated_count}", status_code=303)
