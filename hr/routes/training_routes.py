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


@router.get("/trainings")
def training_list(request: Request, name: str = "", course: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "training_list.html",
        {
            "user": current_user(request),
            "records": repository.list_trainings(name_filter=name, course_filter=course),
            "filter_name": name,
            "filter_course": course,
        },
    )


@router.get("/trainings/new")
def new_training_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "training_form.html", {"user": current_user(request), "error": ""})


@router.post("/trainings/new")
async def create_training_submit(
    request: Request,
    personnel_name: str = Form(...),
    course_name: str = Form(...),
    training_date: str = Form(...),
    hours: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    personnel_name = personnel_name.strip()
    course_name = course_name.strip()
    if not personnel_name or not course_name or not training_date:
        return templates.TemplateResponse(
            request, "training_form.html", {"user": user, "error": "姓名、課程名稱、上課日期都要填。"}, status_code=400
        )

    file_bytes, content_type, filename, error = await _read_optional_file(file)
    if error:
        return templates.TemplateResponse(request, "training_form.html", {"user": user, "error": error}, status_code=400)

    blob_path = ""
    if file_bytes is not None:
        try:
            blob_path = upload_file("trainings", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path, filename = "", ""

    repository.create_training(
        personnel_name, course_name, training_date, hours.strip(), note.strip(),
        blob_path, filename or "", user["username"], user["name"],
    )
    return RedirectResponse(url="/hr/trainings", status_code=303)


@router.get("/trainings/{record_id}")
def training_detail(record_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    record = repository.get_training(record_id)
    if not record:
        return RedirectResponse(url="/hr/trainings", status_code=303)
    return templates.TemplateResponse(request, "training_detail.html", {"user": current_user(request), "record": record})


@router.post("/trainings/{record_id}/update")
async def update_training_submit(
    record_id: str,
    request: Request,
    personnel_name: str = Form(...),
    course_name: str = Form(...),
    training_date: str = Form(...),
    hours: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    file_bytes, content_type, filename, error = await _read_optional_file(file)
    blob_path = None
    if error:
        record = repository.get_training(record_id)
        return templates.TemplateResponse(
            request, "training_detail.html", {"user": user, "record": record, "error": error}, status_code=400
        )
    if file_bytes is not None:
        try:
            blob_path = upload_file("trainings", user["username"], filename, file_bytes, content_type)
        except StorageNotConfigured:
            blob_path = None

    repository.update_training(
        record_id, personnel_name.strip(), course_name.strip(), training_date, hours.strip(), note.strip(),
        blob_path=blob_path, filename=filename if blob_path else None,
    )
    return RedirectResponse(url=f"/hr/trainings/{record_id}", status_code=303)


@router.post("/trainings/{record_id}/delete")
def delete_training_submit(record_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_training(record_id)
    return RedirectResponse(url="/hr/trainings", status_code=303)
