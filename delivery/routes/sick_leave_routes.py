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
    leave_date: str = Form(...),
    hours: str = Form(...),
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

    def _error_response(error: str):
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
        hours_value = float(hours)
        if hours_value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _error_response("請輸入正確的時數（大於 0 的數字，可以有小數）")

    receipt_path = ""
    if receipt is not None and receipt.filename:
        content = await receipt.read()
        content_type = receipt.content_type or "application/octet-stream"
        if len(content) > MAX_UPLOAD_BYTES:
            return _error_response("檔案超過 10MB 上限")
        if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            return _error_response("檔案格式不支援，請上傳 JPG/PNG/PDF")
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
        leave_date=leave_date,
        hours=hours_value,
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

    # 年度假別累積總表：只有同時篩了「姓名」+「廠商」才能明確對應到唯一
    # 一位人員（假別登記本身沒有存 personnel_id，見 repository.py 假別
    # 登記那節開頭的說明），不然沒辦法知道要幫誰算累積。
    quota_summary = None
    quota_person_name = ""
    clean_name = name.strip()
    if clean_name and vendor:
        person = repository.find_personnel_by_name_vendor(vendor, clean_name)
        if person:
            hire_date = repository._parse_date(person.get("hire_date"))
            person_records = [
                r for r in repository.list_sick_leaves(vendor_filter=vendor)
                if r.get("personnel_name") == person["name"]
            ]
            quota_summary = repository.leave_quota_summary_for_person(person_records, hire_date)
            quota_person_name = person["name"]

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
            "quota_summary": quota_summary,
            "quota_person_name": quota_person_name,
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
