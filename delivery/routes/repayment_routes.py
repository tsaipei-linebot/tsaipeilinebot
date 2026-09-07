from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import VENDORS
from delivery.excel_export import build_repayment_workbook
from delivery.templating import templates

router = APIRouter()


@router.get("/function/repayment")
def repayment_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "repayment_form.html",
        {"user": current_user(request), "vendors": VENDORS, "today": date.today().isoformat(), "error": None},
    )


@router.post("/function/repayment")
def repayment_submit(
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    amount: str = Form(...),
    reason: str = Form(""),
    occurred_date: str = Form(...),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    try:
        amount_value = float(amount)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "repayment_form.html",
            {
                "user": user,
                "vendors": VENDORS,
                "today": date.today().isoformat(),
                "error": "金額格式錯誤，請輸入數字",
            },
            status_code=400,
        )

    repository.create_repayment(
        personnel_id="",
        personnel_name=personnel_name,
        vendor=vendor,
        amount=amount_value,
        reason=reason,
        occurred_date=occurred_date,
        created_by=user["username"],
    )
    return RedirectResponse(url="/delivery/function/repayment", status_code=303)


@router.get("/function/repayment/records")
def repayment_records(
    request: Request,
    name: str = "",
    vendor: str = "",
    month: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    records = repository.list_repayments(name_keyword=name, vendor_filter=vendor, month_filter=month)
    return templates.TemplateResponse(
        request,
        "repayment_records.html",
        {
            "user": user,
            "vendors": VENDORS,
            "records": records,
            "filter_name": name,
            "filter_vendor": vendor,
            "filter_month": month,
        },
    )


@router.get("/function/repayment/records/export")
def repayment_records_export(
    name: str = "", vendor: str = "", month: str = "", redirect=Depends(login_required)
):
    if redirect:
        return redirect
    records = repository.list_repayments(name_keyword=name, vendor_filter=vendor, month_filter=month)
    content = build_repayment_workbook(records)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=repayment_records.xlsx"},
    )


@router.post("/function/repayment/records/approve")
async def repayment_records_approve(request: Request, redirect=Depends(admin_required)):
    """核准是單向的，只開放管理員操作：勾選的補款登記會被標記為已核准，沒有
    取消核准的路徑（見 repository.bulk_approve_repayments）。"""
    if redirect:
        return redirect
    form = await request.form()

    repayment_ids = []
    filters = {}
    for key, value in form.multi_items():
        if key.startswith("approve_"):
            repayment_ids.append(key[len("approve_"):])
        elif key == "filter_name" and value:
            filters["name"] = value
        elif key == "filter_vendor" and value:
            filters["vendor"] = value
        elif key == "filter_month" and value:
            filters["month"] = value

    repository.bulk_approve_repayments(repayment_ids)

    query = urlencode(filters)
    return RedirectResponse(url=f"/delivery/function/repayment/records{'?' + query if query else ''}", status_code=303)
