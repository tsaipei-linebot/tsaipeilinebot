"""廠商管理（/vendors）：記錄「這個廠商的派遣員工用哪家公司簽約加保」，
只有全平台管理員（老闆本人）看得到，跟 /accounts、/companies 一樣直接掛
在根 app、用同一顆共用 session cookie。

刻意取複數 `vendors_routes.py`（不是 `vendor_routes.py`），避免跟
`delivery/routes/vendor_routes.py` 搞混——那支是配送部「依廠商篩選人員
清單」的頁面，用的是寫死在 `delivery/config.py` 的舊廠商清單，跟這裡是
完全獨立的兩份資料，見 platform_vendors.py 開頭的說明。

**2026-09-14 新增：列表頁顯示每一筆連動的合約/契約，可以直接點進去
預覽/下載**。合約產生器每次送出都新建一筆廠商紀錄、一對一連過去，所以
`client_contract_by_vendor_id` 最多一筆；派遣契約產生器可能被好幾個人
各自負責同一個廠商（見 `services/contract_summary_service.py` 的分組
說明），所以 `dispatch_contracts_by_vendor_id` 是清單，依送出人分組各
留最新一筆。這裡不用 `services/contract_summary_service.py` 的權限
過濾——`/vendors` 本來就是全平台管理員限定的頁面，管理員本來就看得到
全部，不用另外判斷服務部門。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_accounts
import platform_companies
import platform_departments
import platform_vendors
from platform_templating import templates
from services.client_contract_service import list_submissions as list_client_contract_submissions
from services.dispatch_contract_service import list_submissions as list_dispatch_contract_submissions

router = APIRouter()


def _fields_from_form(form_data) -> dict:
    fields = {field: form_data.get(field, "") for field in platform_vendors.FIELDS}
    fields["service_departments"] = form_data.getlist("service_departments")
    return fields


def _client_contract_by_vendor_id(limit: int = 5000) -> dict:
    """廠商文件 ID -> 連到的那一份合約產生器紀錄——每個廠商紀錄本來就是
    合約送出時一對一新建的，理論上最多一筆，用字典存第一筆遇到的即可。"""
    result = {}
    for record in list_client_contract_submissions(limit=limit):
        vendor_id = record.get("vendor_id")
        if vendor_id and vendor_id not in result:
            result[vendor_id] = record
    return result


def _dispatch_contracts_by_vendor_id(limit: int = 5000) -> dict:
    """廠商文件 ID -> 連到的派遣契約產生器紀錄清單，依「送出人」分組各自
    只留最新一筆（同一個廠商可能被不同人／不同團隊各自負責，見
    `services/contract_summary_service.py` 的分組說明），紀錄本身已經是
    依送出時間新到舊排序，遇到的第一筆就是最新的。"""
    result = {}
    seen_groups = set()
    for record in list_dispatch_contract_submissions(limit=limit):
        vendor_id = record.get("vendor_id")
        if not vendor_id:
            continue
        group_key = (vendor_id, record.get("submitted_by", ""))
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        result.setdefault(vendor_id, []).append(record)
    return result


@router.get("/")
def vendors_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    vendors = platform_vendors.list_vendors()
    company_name_by_id = {c["id"]: c["name"] for c in platform_companies.list_companies()}
    client_contract_by_vendor_id = _client_contract_by_vendor_id()
    dispatch_contracts_by_vendor_id = _dispatch_contracts_by_vendor_id()
    contract_years = sorted({v["contract_year"] for v in vendors if v["contract_year"]}, reverse=True)
    for v in vendors:
        v["company_name"] = company_name_by_id.get(v["company_id"], v["company_id"] or "")
        v["linked_client_contract"] = client_contract_by_vendor_id.get(v["id"])
        v["linked_dispatch_contracts"] = dispatch_contracts_by_vendor_id.get(v["id"], [])
    return templates.TemplateResponse(
        request,
        "vendors_list.html",
        {
            "user": platform_accounts.current_account(request),
            "vendors": vendors,
            "error": error,
            "contract_years": contract_years,
        },
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
            "department_options": platform_departments.list_department_names(),
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
                "department_options": platform_departments.list_department_names(),
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
            "department_options": platform_departments.list_department_names(),
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
                "department_options": platform_departments.list_department_names(),
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
