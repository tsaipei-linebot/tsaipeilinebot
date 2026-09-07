from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from delivery.auth import MODULE_CODE, authenticate
from delivery.templating import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/delivery/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "info": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    account = authenticate(username, password)
    if not account:
        return templates.TemplateResponse(
            request, "login.html", {"error": "帳號或密碼錯誤", "info": None}, status_code=401
        )
    request.session["user"] = account
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        # 帳密正確，但這組帳號沒有配送部系統的權限——還是設定 session（這樣
        # 之後去有權限的模組不用重新登入一次），只是不能直接進配送部系統。
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "info": "帳號密碼正確，但這組帳號沒有配送部系統的權限，請回主頁選擇您有權限的系統。"},
        )
    return RedirectResponse(url="/delivery/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/delivery/login", status_code=303)
