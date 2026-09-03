from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES, VENDORS
from delivery.storage import StorageNotConfigured, upload_file
from delivery.templating import templates

router = APIRouter()


@router.get("/function/sick-leave")
def sick_leave_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "sick_leave_form.html",
        {
            "user": current_user(request),
            "vendors": VENDORS,
            "records": repository.list_recent_sick_leaves(),
            "error": None,
        },
    )


@router.post("/function/sick-leave")
async def sick_leave_submit(
    request: Request,
    vendor: str = Form(...),
    personnel_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    receipt: UploadFile = File(None),
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    user = current_user(request)

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
                    "records": repository.list_recent_sick_leaves(),
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
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        receipt_file_path=receipt_path,
        created_by=user["username"],
    )
    return RedirectResponse(url="/delivery/function/sick-leave", status_code=303)
