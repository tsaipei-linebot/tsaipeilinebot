from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from management import repository
from management.auth import admin_required, current_user, login_required
from management.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES
from management.storage import StorageNotConfigured, upload_file
from management.templating import templates

router = APIRouter()


@router.get("/meetings")
def meeting_list(request: Request, department: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "meeting_list.html",
        {
            "user": current_user(request),
            "meetings": repository.list_meeting_notes(department_filter=department),
            "filter_department": department,
        },
    )


@router.get("/meetings/new")
def new_meeting_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "meeting_form.html", {"user": current_user(request), "error": ""})


@router.post("/meetings/new")
async def create_meeting_submit(
    request: Request,
    title: str = Form(...),
    meeting_date: str = Form(...),
    department: str = Form(...),
    content: str = Form(...),
    attachment: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    title = title.strip()
    department = department.strip()
    content = content.strip()
    if not title or not meeting_date or not department or not content:
        return templates.TemplateResponse(
            request,
            "meeting_form.html",
            {"user": user, "error": "標題、日期、部門、內容都要填。"},
            status_code=400,
        )

    attachment_blob_path = ""
    attachment_filename = ""
    if attachment is not None and attachment.filename:
        content_bytes = await attachment.read()
        content_type = attachment.content_type or "application/octet-stream"
        if len(content_bytes) > MAX_UPLOAD_BYTES:
            error = "附件超過 20MB 上限"
        elif content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            error = "附件格式不支援，請上傳 PDF/PPT/Word/Excel/圖片"
        else:
            error = ""
        if error:
            return templates.TemplateResponse(
                request, "meeting_form.html", {"user": user, "error": error}, status_code=400
            )
        try:
            attachment_blob_path = upload_file(
                "meeting-attachments", user["username"], attachment.filename, content_bytes, content_type
            )
            attachment_filename = attachment.filename
        except StorageNotConfigured:
            attachment_blob_path = ""
            attachment_filename = ""

    repository.create_meeting_note(
        title, meeting_date, department, content, user["username"], user["name"],
        attachment_blob_path=attachment_blob_path, attachment_filename=attachment_filename,
    )
    return RedirectResponse(url="/management/meetings", status_code=303)


@router.get("/meetings/{note_id}")
def meeting_detail(note_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    meeting = repository.get_meeting_note(note_id)
    if not meeting:
        return RedirectResponse(url="/management/meetings", status_code=303)
    return templates.TemplateResponse(request, "meeting_detail.html", {"user": current_user(request), "meeting": meeting})


@router.post("/meetings/{note_id}/delete")
def delete_meeting_submit(note_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_meeting_note(note_id)
    return RedirectResponse(url="/management/meetings", status_code=303)
