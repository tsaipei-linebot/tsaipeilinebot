"""廠商管理（/vendors）：記錄「這個廠商的派遣員工用哪家公司簽約加保」，
只有全平台管理員（老闆本人）看得到，跟 /accounts、/companies 一樣直接掛
在根 app、用同一顆共用 session cookie。

刻意取複數 `vendors_routes.py`（不是 `vendor_routes.py`），避免跟
`delivery/routes/vendor_routes.py` 搞混——那支是配送部「依廠商篩選人員
清單」的頁面，用的是寫死在 `delivery/config.py` 的舊廠商清單，跟這裡是
完全獨立的兩份資料，見 platform_vendors.py 開頭的說明。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
import platform_companies
import platform_vendors
from platform_templating import templates

router = APIRouter()


def _fields_from_form(form_data) -> dict:
    return {field: form_data.get(field, "") for field in platform_vendors.FIELDS}


@router.get("/")
def vendors_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    vendors = platform_vendors.list_vendors()
    company_name_by_id = {c["id"]: c["name"] for c in platform_companies.list_companies()}
    for v in vendors:
        v["company_name"] = company_name_by_id.get(v["company_id"], v["company_id"] or "")
    return templates.TemplateResponse(
        request,
        "vendors_list.html",
        {"user": platform_accounts.current_account(request), "vendors": vendors, "error": error},
    )


@router.get("/new")
def new_vendor_form(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "vendor_form.html",
        {
            "user": platform_accounts.current_account(request),
            "vendor": None,
            "companies": platform_companies.list_companies(),
            "error": "",
        },
    )


@router.post("/new")
async def create_vendor_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    form = await request.form()
    vendor_id = (form.get("code") or "").strip()
    fields = _fields_from_form(form)

    error = platform_vendors.validate_vendor_fields(fields)
    if not error and vendor_id and platform_vendors.vendor_exists(vendor_id):
        error = "這個代號已經有廠商在用了，請換一個代號。"

    if error:
        return templates.TemplateResponse(
            request,
            "vendor_form.html",
            {
                "user": platform_accounts.current_account(request),
                "vendor": fields,
                "companies": platform_companies.list_companies(),
                "error": error,
            },
            status_code=400,
        )

    platform_vendors.create_vendor(vendor_id, fields)
    return RedirectResponse(url="/vendors", status_code=303)


@router.get("/{vendor_id}/edit")
def edit_vendor_form(vendor_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    vendor = platform_vendors.get_vendor(vendor_id)
    if not vendor:
        return RedirectResponse(url="/vendors?error=not_found", status_code=303)
    return templates.TemplateResponse(
        request,
        "vendor_form.html",
        {
            "user": platform_accounts.current_account(request),
            "vendor": vendor,
            "companies": platform_companies.list_companies(),
            "error": "",
        },
    )


@router.post("/{vendor_id}/edit")
async def edit_vendor_submit(vendor_id: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    vendor = platform_vendors.get_vendor(vendor_id)
    if not vendor:
        return RedirectResponse(url="/vendors?error=not_found", status_code=303)

    form = await request.form()
    fields = _fields_from_form(form)
    # 代號是文件 ID，建立後不能改，表單裡是唯讀欄位，這裡強制沿用原本的值。
    fields["code"] = vendor_id

    error = platform_vendors.validate_vendor_fields(fields)
    if error:
        return templates.TemplateResponse(
            request,
            "vendor_form.html",
            {
                "user": platform_accounts.current_account(request),
                "vendor": {**fields, "id": vendor_id},
                "companies": platform_companies.list_companies(),
                "error": error,
            },
            status_code=400,
        )

    platform_vendors.update_vendor(vendor_id, fields)
    return RedirectResponse(url="/vendors", status_code=303)


@router.post("/{vendor_id}/delete")
def delete_vendor_submit(vendor_id: str, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    if not platform_vendors.vendor_exists(vendor_id):
        return RedirectResponse(url="/vendors?error=not_found", status_code=303)
    platform_vendors.delete_vendor(vendor_id)
    return RedirectResponse(url="/vendors", status_code=303)
