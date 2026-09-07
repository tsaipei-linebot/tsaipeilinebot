from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from hr import repository
from hr.auth import admin_required, current_user, login_required
from hr.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES
from hr.storage import StorageNotConfigured, upload_file
from hr.templating import templates

router = APIRouter()


async def _read_optional_file(file: UploadFile):
    if file is None or not file.filename:
        return None, None, None, ""
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    if len(content) > MAX_UPLOAD_BYTES:
        return None, None, None, "檔案超過 20MB 上限"
    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        return None, None, None, "檔案格式不支援，請上傳 PDF/Word/Excel/圖片"
    return content, content_type, file.filename, ""


@router.get("/care-logs")
def care_log_list(request: Request, q: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "care_log_list.html",
        {"user": current_user(request), "records": repository.list_care_logs(keyword_filter=q), "filter_q": q},
    )


@router.get("/care-logs/new")
def new_care_log_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "care_log_form.html", {"user": current_user(request), "error": ""})


@router.post("/care-logs/new")
async def create_care_log_submit(
    request: Request,
    log_date: str = Form(...),
    subject: str = Form(...),
    personnel_name: str = Form(""),
    content: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    subject = subject.strip()
    content = content.strip()
    if not log_date or not subject or not content:
        return templates.TemplateResponse(
            request, "care_log_form.html", {"user": user, "error": "日期、主題、內容都要填。"}, status_code=400
        )

    file_bytes, content_type, filename, error = await _read_optional_file(file)
    if error:
        return templates.TemplateResponse(request, "care_log_form.html", {"user": user, "error": error}, status_code=400)

    blob_path = ""
    if file_bytes is not None:
        try:
            blob_path = upload_file("care-logs", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path, filename = "", ""

    repository.create_care_log(
        log_date, subject, personnel_name.strip(), content, blob_path, filename or "", user["username"], user["name"]
    )
    return RedirectResponse(url="/hr/care-logs", status_code=303)


@router.get("/care-logs/{record_id}")
def care_log_detail(record_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    record = repository.get_care_log(record_id)
    if not record:
        return RedirectResponse(url="/hr/care-logs", status_code=303)
    return templates.TemplateResponse(request, "care_log_detail.html", {"user": current_user(request), "record": record})


@router.post("/care-logs/{record_id}/update")
async def update_care_log_submit(
    record_id: str,
    request: Request,
    log_date: str = Form(...),
    subject: str = Form(...),
    personnel_name: str = Form(""),
    content: str = Form(...),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    file_bytes, content_type, filename, error = await _read_optional_file(file)
    blob_path = None
    if error:
        record = repository.get_care_log(record_id)
        return templates.TemplateResponse(
            request, "care_log_detail.html", {"user": user, "record": record, "error": error}, status_code=400
        )
    if file_bytes is not None:
        try:
            blob_path = upload_file("care-logs", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path = None

    repository.update_care_log(
        record_id, log_date, subject.strip(), personnel_name.strip(), content.strip(),
        blob_path=blob_path, filename=filename if blob_path else None,
    )
    return RedirectResponse(url=f"/hr/care-logs/{record_id}", status_code=303)


@router.post("/care-logs/{record_id}/delete")
def delete_care_log_submit(record_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_care_log(record_id)
    return RedirectResponse(url="/hr/care-logs", status_code=303)
