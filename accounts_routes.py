"""帳號權限管理（/accounts）：只有全平台管理員（老闆本人）看得到，可以幫
每組帳號指派橫跨各部門模組的權限（不開放/專員/主管），取代掉舊版配送部
系統自己的「帳號管理」頁面——那個只能設定單一模組的角色，多模組之後不夠用。

刻意不放進 delivery/ 或 management/ 底下：這是跨模組的東西，不屬於任何一個
部門，掛在根 app（main.py）上，用跟其他模組共用的同一顆 session cookie。
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

import platform_accounts
from platform_accounts import MODULE_ROLE_MAP, MODULE_ROLES, MODULES
from platform_templating import templates

router = APIRouter()


def _modules_from_form(form_data) -> dict:
    modules = {}
    for m in MODULES:
        value = form_data.get(f"module_{m['code']}", "")
        if value in MODULE_ROLE_MAP:
            modules[m["code"]] = value
    return modules


def _manager_usernames_from_form(form_data) -> list:
    return [u for u in form_data.getlist("manager_usernames") if u]


def _department_from_form(form_data) -> str:
    return (form_data.get("department") or "").strip()


def _account_form_context(request: Request, account: dict, error: str) -> dict:
    """新增/編輯帳號表單共用的 context：「所屬主管」下拉選單要列出除了自己
    以外的所有帳號可以選（可以選多個），編輯自己時排除自己，避免選到自己
    當自己的主管。"""
    all_accounts = platform_accounts.list_accounts()
    current_username = account["username"] if account else None
    manager_options = [a for a in all_accounts if a["username"] != current_username]
    return {
        "user": platform_accounts.current_account(request),
        "account": account,
        "modules": MODULES,
        "roles": MODULE_ROLES,
        "manager_options": manager_options,
        "error": error,
    }


@router.get("/")
def accounts_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    accounts = platform_accounts.list_accounts()
    name_by_username = {a["username"]: a["name"] for a in accounts}
    for a in accounts:
        a["manager_names"] = [name_by_username.get(u, u) for u in a["manager_usernames"]]
    return templates.TemplateResponse(
        request,
        "accounts_list.html",
        {
            "user": platform_accounts.current_account(request),
            "accounts": accounts,
            "modules": MODULES,
            "role_map": MODULE_ROLE_MAP,
            "error": error,
        },
    )


@router.get("/new")
def new_account_form(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "account_form.html", _account_form_context(request, None, ""))


@router.post("/new")
async def create_account_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    name = (form.get("name") or "").strip()
    modules = _modules_from_form(form)
    manager_usernames = _manager_usernames_from_form(form)
    department = _department_from_form(form)

    error = ""
    if not username or not password or not name:
        error = "帳號、密碼、姓名都要填。"
    elif platform_accounts.account_exists(username):
        error = "這個帳號已經存在，請換一個帳號名稱。"

    if error:
        return templates.TemplateResponse(
            request, "account_form.html", _account_form_context(request, None, error), status_code=400,
        )

    platform_accounts.create_account(
        username, password, name, modules,
        manager_usernames=manager_usernames, department=department,
    )
    return RedirectResponse(url="/accounts", status_code=303)


@router.get("/{username}/edit")
def edit_account_form(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    account = platform_accounts.get_account(username)
    if not account:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)
    return templates.TemplateResponse(request, "account_form.html", _account_form_context(request, account, ""))


@router.post("/{username}/edit")
async def edit_account_submit(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    account = platform_accounts.get_account(username)
    if not account:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)

    form = await request.form()
    name = (form.get("name") or "").strip()
    password = form.get("password") or ""
    modules = _modules_from_form(form)
    manager_usernames = _manager_usernames_from_form(form)
    department = _department_from_form(form)

    error = "" if name else "姓名不能空白。"
    if error:
        return templates.TemplateResponse(
            request, "account_form.html", _account_form_context(request, account, error), status_code=400,
        )

    platform_accounts.update_account(
        username, name, modules, password=password,
        manager_usernames=manager_usernames, department=department,
    )
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/{username}/delete")
def delete_account_submit(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    target = platform_accounts.get_account(username)
    if not target:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)

    current = platform_accounts.current_account(request)
    error = platform_accounts.validate_account_deletion(
        username, current["username"], target["is_platform_admin"]
    )
    if error:
        return RedirectResponse(url=f"/accounts?error={error}", status_code=303)

    platform_accounts.delete_account(username)
    return RedirectResponse(url="/accounts", status_code=303)
