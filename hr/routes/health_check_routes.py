from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from hr import repository
from hr.auth import admin_required, current_user, login_required
from hr.config import ALLOWED_UPLOAD_CONTENT_TYPES, MAX_UPLOAD_BYTES
from hr.storage import StorageNotConfigured, upload_file
from hr.templating import templates

router = APIRouter()


async def _read_optional_file(file: UploadFile):
    """回傳 (blob_path 用的原始 bytes/content_type/filename) 或 (None, None, None)
    代表這次沒有上傳新檔案。檔案太大/格式不支援時回傳 error 字串。"""
    if file is None or not file.filename:
        return None, None, None, ""
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    if len(content) > MAX_UPLOAD_BYTES:
        return None, None, None, "檔案超過 20MB 上限"
    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        return None, None, None, "檔案格式不支援，請上傳 PDF/Word/Excel/圖片"
    return content, content_type, file.filename, ""


@router.get("/health-checks")
def health_check_list(request: Request, name: str = "", department: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "health_check_list.html",
        {
            "user": current_user(request),
            "records": repository.list_health_checks(name_filter=name, department_filter=department),
            "filter_name": name,
            "filter_department": department,
        },
    )


@router.get("/health-checks/new")
def new_health_check_form(request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "health_check_form.html", {"user": current_user(request), "error": ""})


@router.post("/health-checks/new")
async def create_health_check_submit(
    request: Request,
    personnel_name: str = Form(...),
    department: str = Form(""),
    check_date: str = Form(...),
    next_due_date: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    personnel_name = personnel_name.strip()
    if not personnel_name or not check_date:
        return templates.TemplateResponse(
            request, "health_check_form.html", {"user": user, "error": "姓名跟受檢日期都要填。"}, status_code=400
        )

    content, content_type, filename, error = await _read_optional_file(file)
    if error:
        return templates.TemplateResponse(request, "health_check_form.html", {"user": user, "error": error}, status_code=400)

    blob_path = ""
    if content is not None:
        try:
            blob_path = upload_file("health-checks", user["username"], filename, content, content_type)
        except StorageNotConfigured:
            blob_path, filename = "", ""

    repository.create_health_check(
        personnel_name, department.strip(), check_date, next_due_date.strip(), note.strip(),
        blob_path, filename or "", user["username"], user["name"],
    )
    return RedirectResponse(url="/hr/health-checks", status_code=303)


@router.get("/health-checks/{record_id}")
def health_check_detail(record_id: str, request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    record = repository.get_health_check(record_id)
    if not record:
        return RedirectResponse(url="/hr/health-checks", status_code=303)
    return templates.TemplateResponse(
        request, "health_check_detail.html", {"user": current_user(request), "record": record}
    )


@router.post("/health-checks/{record_id}/update")
async def update_health_check_submit(
    record_id: str,
    request: Request,
    personnel_name: str = Form(...),
    department: str = Form(""),
    check_date: str = Form(...),
    next_due_date: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(None),
    redirect=Depends(admin_required),
):
    if redirect:
        return redirect
    user = current_user(request)
    content, content_type, filename, error = await _read_optional_file(file)
    blob_path = None
    if error:
        record = repository.get_health_check(record_id)
        return templates.TemplateResponse(
            request, "health_check_detail.html", {"user": user, "record": record, "error": error}, status_code=400
        )
    if content is not None:
        try:
            blob_path = upload_file("health-checks", user["username"], filename, content, content_type)
        except StorageNotConfigured:
            blob_path = None

    repository.update_health_check(
        record_id, personnel_name.strip(), department.strip(), check_date, next_due_date.strip(), note.strip(),
        blob_path=blob_path, filename=filename if blob_path else None,
    )
    return RedirectResponse(url=f"/hr/health-checks/{record_id}", status_code=303)


@router.post("/health-checks/{record_id}/delete")
def delete_health_check_submit(record_id: str, request: Request, redirect=Depends(admin_required)):
    if redirect:
        return redirect
    repository.delete_health_check(record_id)
    return RedirectResponse(url="/hr/health-checks", status_code=303)
