"""全平台共用的登入/登出頁面，掛在根 app、不屬於任何部門模組。

有這支之後，同仁改成先在 /portal 登入一次，之後點進 /delivery、
/management 都不用再登入一次（session cookie 共用）；/delivery/login、
/management/login 還是保留著，只當作有人略過 /portal、直接連到部門網址時
的備援登入頁，兩邊用的都是同一套 platform_accounts 帳號。
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_templating import templates

router = APIRouter()


def _safe_next_path(next_path: str) -> str:
    """只接受站內的相對路徑，避免被拿來當開放式重導向（open redirect）的
    跳板——例如 next=https://evil.example.com 這種絕對網址一律忽略，
    改回預設的 /portal。"""
    if not next_path:
        return "/portal"
    parsed = urlparse(next_path)
    if parsed.scheme or parsed.netloc or not next_path.startswith("/"):
        return "/portal"
    return next_path


@router.get("/login")
def login_page(request: Request, next: str = "/portal"):
    if platform_accounts.current_account(request):
        return RedirectResponse(url=_safe_next_path(next), status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": _safe_next_path(next)}
    )


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/portal")):
    account = platform_accounts.authenticate(username, password)
    safe_next = _safe_next_path(next)
    if not account:
        return templates.TemplateResponse(
            request, "login.html", {"error": "帳號或密碼錯誤", "next": safe_next}, status_code=401
        )
    request.session["user"] = account
    return RedirectResponse(url=safe_next, status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
