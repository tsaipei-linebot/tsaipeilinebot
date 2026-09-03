from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from delivery.auth import authenticate
from delivery.templating import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/delivery/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = authenticate(username, password)
    if not user:
        return templates.TemplateResponse(
            request, "login.html", {"error": "帳號或密碼錯誤"}, status_code=401
        )
    request.session["user"] = user
    return RedirectResponse(url="/delivery/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/delivery/login", status_code=303)
