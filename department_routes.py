"""部門管理（/departments）：只有全平台管理員（老闆本人）看得到，維護
`platform_departments.py` 的部門主檔——跟 /accounts、/companies 一樣直接
掛在根 app、用同一顆共用 session cookie。2026-09-12 新增，讓部門清單
不用寫死在程式碼裡，之後要新增/改名/調整順序都能直接在網頁上操作。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

import platform_accounts
import platform_departments
from platform_templating import templates

router = APIRouter()


@router.get("/")
def departments_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "departments_list.html",
        {
            "user": platform_accounts.current_account(request),
            "departments": platform_departments.list_departments(),
            "error": error,
        },
    )


@router.post("/reorder")
async def reorder_departments_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    platform_departments.reorder_departments(ids)
    return JSONResponse({"status": "ok"})


@router.get("/new")
def new_department_form(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "department_form.html",
        {"user": platform_accounts.current_account(request), "department": None, "error": ""},
    )


@router.post("/new")
async def create_department_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()

    error = platform_departments.validate_department_name(name)
    if error:
        return templates.TemplateResponse(
            request,
            "department_form.html",
            {"user": platform_accounts.current_account(request), "department": {"name": name}, "error": error},
            status_code=400,
        )

    platform_departments.create_department(name)
    return RedirectResponse(url="/departments", status_code=303)


@router.get("/{department_id}/edit")
def edit_department_form(department_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    department = platform_departments.get_department(department_id)
    if not department:
        return RedirectResponse(url="/departments?error=not_found", status_code=303)
    return templates.TemplateResponse(
        request,
        "department_form.html",
        {"user": platform_accounts.current_account(request), "department": department, "error": ""},
    )


@router.post("/{department_id}/edit")
async def edit_department_submit(department_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    department = platform_departments.get_department(department_id)
    if not department:
        return RedirectResponse(url="/departments?error=not_found", status_code=303)

    form = await request.form()
    name = (form.get("name") or "").strip()

    error = platform_departments.validate_department_name(name, editing_id=department_id)
    if error:
        return templates.TemplateResponse(
            request,
            "department_form.html",
            {"user": platform_accounts.current_account(request), "department": {**department, "name": name}, "error": error},
            status_code=400,
        )

    platform_departments.update_department_name(department_id, name)
    return RedirectResponse(url="/departments", status_code=303)


@router.post("/{department_id}/delete")
def delete_department_submit(department_id: str, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    department = platform_departments.get_department(department_id)
    if not department:
        return RedirectResponse(url="/departments?error=not_found", status_code=303)
    if platform_departments.count_accounts_using_department(department["name"]) > 0:
        return RedirectResponse(url="/departments?error=in_use", status_code=303)
    platform_departments.delete_department(department_id)
    return RedirectResponse(url="/departments", status_code=303)
