from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from delivery import repository
from delivery.auth import admin_required, current_user, login_required
from delivery.config import ALLOWED_UPLOAD_CONTENT_TYPES, LEAVE_TYPE_MAP, LEAVE_TYPES, MAX_UPLOAD_BYTES, VENDORS
from delivery.excel_export import build_sick_leave_workbook
from delivery.storage import StorageNotConfigured, upload_file
from delivery.templating import templates

router = APIRouter()


@router.get("/function/sick-leave")
def sick_leave_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    today = date.today().isoformat()
    return templates.TemplateResponse(
        request,
        "sick_leave_form.html",
        {"user": current_user(request), "vendors": VENDORS, "leave_types": LEAVE_TYPES, "today": today, "error": None},
    )


@router.post("/function/sick-leave")
async def sick_leave_submit(
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    leave_type: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    receipt: UploadFile = File(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    today = date.today().isoformat()
    if leave_type not in LEAVE_TYPE_MAP:
        leave_type = ""

    receipt_path = ""
    if receipt is not None and receipt.filename:
        content = await receipt.read()
        content_type = receipt.content_type or "application/octet-stream"
        error = None
        if len(content) > MAX_UPLOAD_BYTES:
            error = "檔案超過 10MB 上限"
        elif content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            error = "檔案格式不支援，請上傳 JPG/PNG/PDF"
        if error:
            return templates.TemplateResponse(
                request,
                "sick_leave_form.html",
                {
                    "user": user,
                    "vendors": VENDORS,
                    "leave_types": LEAVE_TYPES,
                    "today": today,
                    "error": error,
                },
                status_code=400,
            )
        try:
            receipt_path = upload_file(
                "sick-leave-receipts", user["username"], receipt.filename, content, content_type
            )
        except StorageNotConfigured:
            receipt_path = ""

    repository.create_sick_leave(
        personnel_id="",
        personnel_name=personnel_name,
        vendor=vendor,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        receipt_file_path=receipt_path,
        created_by=user["username"],
    )
    return RedirectResponse(url="/delivery/function/sick-leave", status_code=303)


@router.get("/function/sick-leave/records")
def sick_leave_records(
    request: Request,
    name: str = "",
    vendor: str = "",
    month: str = "",
    leave_type: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    records = repository.list_sick_leaves(
        name_keyword=name, vendor_filter=vendor, month_filter=month, leave_type_filter=leave_type
    )
    return templates.TemplateResponse(
        request,
        "sick_leave_records.html",
        {
            "user": user,
            "vendors": VENDORS,
            "leave_types": LEAVE_TYPES,
            "leave_type_map": LEAVE_TYPE_MAP,
            "records": records,
            "filter_name": name,
            "filter_vendor": vendor,
            "filter_month": month,
            "filter_leave_type": leave_type,
        },
    )


@router.get("/function/sick-leave/records/export")
def sick_leave_records_export(
    name: str = "", vendor: str = "", month: str = "", leave_type: str = "", redirect=Depends(login_required)
):
    if redirect:
        return redirect
    records = repository.list_sick_leaves(
        name_keyword=name, vendor_filter=vendor, month_filter=month, leave_type_filter=leave_type
    )
    content = build_sick_leave_workbook(records)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sick_leave_records.xlsx"},
    )


@router.post("/function/sick-leave/records/approve")
async def sick_leave_records_approve(request: Request, redirect=Depends(admin_required)):
    """核准是單向的，只開放管理員操作，沒有取消核准的路徑（見
    repository.bulk_approve_sick_leaves）。"""
    if redirect:
        return redirect
    form = await request.form()

    sick_leave_ids = []
    filters = {}
    for key, value in form.multi_items():
        if key.startswith("approve_"):
            sick_leave_ids.append(key[len("approve_"):])
        elif key == "filter_name" and value:
            filters["name"] = value
        elif key == "filter_vendor" and value:
            filters["vendor"] = value
        elif key == "filter_month" and value:
            filters["month"] = value
        elif key == "filter_leave_type" and value:
            filters["leave_type"] = value

    repository.bulk_approve_sick_leaves(sick_leave_ids)

    query = urlencode(filters)
    return RedirectResponse(
        url=f"/delivery/function/sick-leave/records{'?' + query if query else ''}", status_code=303
    )
