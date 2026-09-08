"""公司管理（/companies）：材霈旗下派遣公司牌照主檔，只有全平台管理員
（老闆本人）看得到，跟 /accounts 帳號權限管理一樣直接掛在根 app、用同一顆
共用 session cookie。

見 platform_companies.py 開頭的說明：這裡的「公司」是材霈自己的派遣牌照，
跟配送部系統既有的「廠商」（蝦皮、UD…）是不同概念，不要搞混。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
import platform_companies
from platform_templating import templates

router = APIRouter()


def _fields_from_form(form_data) -> dict:
    return {field: form_data.get(field, "") for field in platform_companies.FIELDS}


@router.get("/")
def companies_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "companies_list.html",
        {
            "user": platform_accounts.current_account(request),
            "companies": platform_companies.list_companies(),
            "error": error,
        },
    )


@router.get("/new")
def new_company_form(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "company_form.html",
        {"user": platform_accounts.current_account(request), "company": None, "error": ""},
    )


@router.post("/new")
async def create_company_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    form = await request.form()
    company_id = (form.get("short_name") or "").strip()
    fields = _fields_from_form(form)

    error = platform_companies.validate_company_fields(fields)
    if not error and company_id and platform_companies.company_exists(company_id):
        error = "這個簡稱已經有公司在用了，請換一個簡稱。"

    if error:
        return templates.TemplateResponse(
            request,
            "company_form.html",
            {"user": platform_accounts.current_account(request), "company": fields, "error": error},
            status_code=400,
        )

    platform_companies.create_company(company_id, fields)
    return RedirectResponse(url="/companies", status_code=303)


@router.get("/{company_id}/edit")
def edit_company_form(company_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    company = platform_companies.get_company(company_id)
    if not company:
        return RedirectResponse(url="/companies?error=not_found", status_code=303)
    return templates.TemplateResponse(
        request,
        "company_form.html",
        {"user": platform_accounts.current_account(request), "company": company, "error": ""},
    )


@router.post("/{company_id}/edit")
async def edit_company_submit(company_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    company = platform_companies.get_company(company_id)
    if not company:
        return RedirectResponse(url="/companies?error=not_found", status_code=303)

    form = await request.form()
    fields = _fields_from_form(form)
    # 簡稱是文件 ID，建立後不能改，表單裡是唯讀欄位，這裡強制沿用原本的值，
    # 避免有人繞過畫面直接送出改掉的簡稱、變成建立了另一份跟舊資料脫鉤的紀錄。
    fields["short_name"] = company_id

    error = platform_companies.validate_company_fields(fields)
    if error:
        return templates.TemplateResponse(
            request,
            "company_form.html",
            {"user": platform_accounts.current_account(request), "company": {**fields, "id": company_id}, "error": error},
            status_code=400,
        )

    platform_companies.update_company(company_id, fields)
    return RedirectResponse(url="/companies", status_code=303)


@router.post("/{company_id}/delete")
def delete_company_submit(company_id: str, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    if not platform_companies.company_exists(company_id):
        return RedirectResponse(url="/companies?error=not_found", status_code=303)
    platform_companies.delete_company(company_id)
    return RedirectResponse(url="/companies", status_code=303)
