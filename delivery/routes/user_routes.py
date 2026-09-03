from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from delivery.auth import (
    admin_required,
    count_admins,
    create_user,
    current_user,
    delete_user,
    get_user,
    list_users,
    user_exists,
    validate_user_deletion,
)
from delivery.templating import templates

router = APIRouter()

ROLES = [
    {"code": "staff", "name": "一般同仁"},
    {"code": "admin", "name": "管理員"},
]
ROLE_MAP = {r["code"]: r["name"] for r in ROLES}


@router.get("/users")
def users_list(request: Request, error: str = "", redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "users_list.html",
        {"user": current_user(request), "users": list_users(), "role_map": ROLE_MAP, "error": error},
    )


@router.get("/users/new")
def new_user_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "user_form.html", {"user": current_user(request), "roles": ROLES, "error": ""}
    )


@router.post("/users/new")
def create_user_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    role: str = Form("staff"),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    username = username.strip()
    name = name.strip()
    if role not in ROLE_MAP:
        role = "staff"

    error = ""
    if not username or not password or not name:
        error = "帳號、密碼、姓名都要填。"
    elif user_exists(username):
        error = "這個帳號已經存在，請換一個帳號名稱。"

    if error:
        return templates.TemplateResponse(
            request,
            "user_form.html",
            {"user": current_user(request), "roles": ROLES, "error": error},
            status_code=400,
        )

    create_user(username, password, name, role)
    return RedirectResponse(url="/delivery/users", status_code=303)


@router.post("/users/{username}/delete")
def delete_user_submit(username: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    target = get_user(username)
    if not target:
        return RedirectResponse(url="/delivery/users?error=not_found", status_code=303)

    current = current_user(request)
    error = validate_user_deletion(username, current["username"], target["role"], count_admins())
    if error:
        return RedirectResponse(url=f"/delivery/users?error={error}", status_code=303)

    delete_user(username)
    return RedirectResponse(url="/delivery/users", status_code=303)
